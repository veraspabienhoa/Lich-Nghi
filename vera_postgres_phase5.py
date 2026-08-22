"""Vera Spa PostgreSQL Phase 5: normalized PostgreSQL-primary reads.

Phase 5 cuts the two most important operational read paths away from Google
Sheets while preserving the exact legacy DataFrame shape expected by the large
V92.6.99 Streamlit core:

- ``credentials``  <- normalized PostgreSQL ``employees``
- ``leave_primary`` <- normalized PostgreSQL ``leave_records``

Safety during the transition:
- Other datasets keep the Phase-2/Phase-3 read behavior.
- If the normalized sync state is missing/stale, or row counts do not match,
  Phase 5 delegates one read to the legacy source path. That path reconciles
  PostgreSQL, after which subsequent reads use PostgreSQL again.
- This stale fallback is especially important while Google Sheets is still a
  synchronous mirror because deleting a physical Sheet row shifts the source
  row numbers used by existing edit/delete business code.
- Set ``VERA_PHASE5_READ_BACKEND=sheets`` for an immediate read-path rollback.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import os
from typing import Any, Mapping

import pandas as pd
from sqlalchemy import text


PHASE5_SCHEMA_VERSION = 5
EMPLOYEE_DATASET = "credentials"
LEAVE_DATASET = "leave_primary"
TARGET_DATASETS = {EMPLOYEE_DATASET, LEAVE_DATASET}
SYNC_STATE_TABLE = "vera_normalized_sync_state"

CREDENTIAL_COLUMNS = [
    "STT", "Tên nhân viên", "Mật khẩu", "Phân quyền", "Họ và tên đầy đủ", "Ngày sinh",
    "Điện thoại", "Email", "Địa chỉ", "Số tài khoản ngân hàng", "Tên ngân hàng",
    "Phát sinh tháng", "Có phép tháng", "Phép năm", "Ca làm việc", "Ngày bắt đầu ca",
    "Chu kỳ", "Khóa đăng nhập", "Remember Token Hash", "Remember Token Expiry",
    "Ngày bắt đầu làm",
]

LEAVE_DATA_COLUMNS = [
    "Ngày", "Thứ ngày", "Tên nhân viên", "Lý do nghỉ", "Loại nghỉ", "Chi tiết",
    "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
    "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật",
]


def _enabled(vpg) -> bool:
    try:
        return bool(vpg.is_enabled())
    except Exception:
        return False


def _mode(vpg) -> str:
    try:
        return str(vpg.data_backend_mode() or "sheets").strip().lower()
    except Exception:
        return "sheets"


def read_backend(vpg) -> str:
    raw = str(os.getenv("VERA_PHASE5_READ_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and bool(getattr(vpg, "_vera_phase3_installed", False)) else "sheets"


def is_active(vpg) -> bool:
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and read_backend(vpg) == "postgres"
        and bool(getattr(vpg, "_vera_phase3_installed", False))
    )


def _event(vpg, dataset_key: str, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(str(dataset_key), str(event_type), str(detail or "")[:1800])
    except Exception:
        pass


def _ensure_phase5_schema(vpg) -> None:
    if not _enabled(vpg):
        return
    engine = vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase5_postgres_primary_reads', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version = GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at = NOW()
                """
            ),
            {"version": PHASE5_SCHEMA_VERSION},
        )


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _plain_number(value: Any):
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        if pd.isna(value):
            return 0.0
    except Exception:
        pass
    try:
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return 0.0


def _payload(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except Exception:
            return {}
    return {}


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    s = _text(value)
    if not s:
        return ""
    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if not pd.isna(parsed):
            return parsed.strftime("%d/%m/%Y")
    except Exception:
        pass
    return s


def _state_row(vpg, dataset_key: str):
    """Return normalized sync state or None without masking database failures."""
    engine = vpg.get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT dataset_key,row_count,is_stale,last_error,synced_at,updated_at "
                f"FROM {SYNC_STATE_TABLE} WHERE dataset_key=:k"
            ),
            {"k": dataset_key},
        ).mappings().first()
    return dict(row) if row else None


def _credentials_from_pg(vpg) -> pd.DataFrame:
    raw = vpg.list_employees_pg()
    if raw is None or not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw if raw is not None else [])
    rows = []
    for _, r in raw.iterrows():
        p = _payload(r.get("payload"))
        item = {c: p.get(c, "") for c in CREDENTIAL_COLUMNS}
        item.update({
            "STT": r.get("stt") if r.get("stt") is not None else p.get("STT", ""),
            "Tên nhân viên": _text(r.get("username")),
            "Mật khẩu": _text(r.get("password_value")),
            "Phân quyền": _text(r.get("role")) or "nhanvien",
            "Họ và tên đầy đủ": _text(r.get("full_name")),
            "Ngày sinh": _text(r.get("birth_date")),
            "Điện thoại": _text(r.get("phone")),
            "Email": _text(r.get("email")),
            "Địa chỉ": _text(r.get("address")),
            "Số tài khoản ngân hàng": _text(r.get("bank_account")),
            "Tên ngân hàng": _text(r.get("bank_name")),
            "Phát sinh tháng": _plain_number(r.get("monthly_generated")),
            "Có phép tháng": _plain_number(r.get("monthly_leave")),
            "Phép năm": _plain_number(r.get("annual_leave")),
            "Ca làm việc": _text(r.get("work_shift")),
            "Ngày bắt đầu ca": _text(r.get("shift_start_date")),
            "Chu kỳ": _text(r.get("rotation_cycle")),
            "Khóa đăng nhập": "KHÓA" if bool(r.get("login_locked")) else "",
            "Remember Token Hash": _text(r.get("remember_token_hash")),
            "Remember Token Expiry": _text(r.get("remember_token_expiry")),
            "Ngày bắt đầu làm": _text(r.get("employment_start_date")),
        })
        rows.append(item)
    return pd.DataFrame(rows, columns=CREDENTIAL_COLUMNS)


def _leave_from_pg(vpg) -> pd.DataFrame:
    raw = vpg.list_leave_records_pg()
    if raw is None or not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw if raw is not None else [])
    rows = []
    for _, r in raw.iterrows():
        p = _payload(r.get("payload"))
        item = {c: p.get(c, "") for c in LEAVE_DATA_COLUMNS}
        leave_date = _format_date(r.get("leave_date")) or _format_date(p.get("Ngày"))
        item.update({
            "Ngày": leave_date,
            "Thứ ngày": _text(r.get("weekday_label")) or _text(p.get("Thứ ngày")),
            "Tên nhân viên": _text(r.get("employee_name")),
            "Lý do nghỉ": _text(r.get("leave_reason")),
            "Loại nghỉ": _text(r.get("leave_type")),
            "Chi tiết": _text(r.get("detail")),
            "Số ngày tính": _plain_number(r.get("calculated_days")),
            "Số ngày phép cộng dồn": _plain_number(r.get("accumulated_leave")),
            "Phạt vi phạm": _plain_number(r.get("penalty")),
            "Ngày cập nhật": _text(r.get("update_date")),
            "Giờ cập nhật": _text(r.get("update_time")),
            "Người cập nhật": _text(r.get("updated_by")),
            "__source_sheet_id": _text(r.get("source_sheet_id")),
            "__source_row": r.get("source_row"),
        })
        raw_values = p.get("__raw_values")
        item["__raw_values"] = raw_values if isinstance(raw_values, list) else []
        rows.append(item)

    cols = LEAVE_DATA_COLUMNS + ["__source_sheet_id", "__source_row", "__raw_values"]
    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out["__phase5_sheet_sort"] = pd.to_numeric(out["__source_row"], errors="coerce")
        out = out.sort_values(
            ["__source_sheet_id", "__phase5_sheet_sort"], kind="stable", na_position="last"
        ).drop(columns=["__phase5_sheet_sort"]).reset_index(drop=True)
    return out


def _normalized_read(vpg, dataset_key: str) -> pd.DataFrame:
    return _credentials_from_pg(vpg) if dataset_key == EMPLOYEE_DATASET else _leave_from_pg(vpg)


def _state_allows_pg_read(state: dict | None, pg_rows: int) -> tuple[bool, str]:
    if not state:
        return False, "sync_state_missing"
    if bool(state.get("is_stale")):
        return False, "sync_state_stale"
    try:
        expected = int(state.get("row_count") or 0)
    except Exception:
        expected = 0
    if expected != int(pg_rows):
        return False, f"row_count_mismatch:{expected}!={int(pg_rows)}"
    return True, "current"


def get_status(vpg) -> dict:
    result = {
        "enabled": bool(is_active(vpg)),
        "read_backend": read_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE5_SCHEMA_VERSION,
    }
    if _enabled(vpg):
        try:
            result["sync_state"] = {
                k: _state_row(vpg, k) for k in sorted(TARGET_DATASETS)
            }
        except Exception as exc:
            result["sync_state_error"] = f"{type(exc).__name__}: {exc}"
    return result


def install(vpg) -> bool:
    """Install PostgreSQL-primary reads after Phase 2/3/4 are installed."""
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase5_installed", False):
        return True
    required = ("load_dataset", "list_employees_pg", "list_leave_records_pg")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    original_load_dataset = vpg.load_dataset

    if _enabled(vpg):
        try:
            _ensure_phase5_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase5", "phase5_schema_warning", f"{type(exc).__name__}: {exc}")

    def phase5_load_dataset(dataset_key, source_loader, ttl_seconds=120, force_refresh=False, wait_seconds=3.0):
        if dataset_key not in TARGET_DATASETS or not is_active(vpg):
            return original_load_dataset(
                dataset_key,
                source_loader,
                ttl_seconds=ttl_seconds,
                force_refresh=force_refresh,
                wait_seconds=wait_seconds,
            )

        if force_refresh:
            fresh = original_load_dataset(
                dataset_key,
                source_loader,
                ttl_seconds=ttl_seconds,
                force_refresh=True,
                wait_seconds=wait_seconds,
            )
            _event(vpg, dataset_key, "phase5_explicit_source_refresh", f"rows={len(fresh) if isinstance(fresh, pd.DataFrame) else 0}")
            return fresh

        try:
            pg_df = _normalized_read(vpg, dataset_key)
            state = _state_row(vpg, dataset_key)
            allowed, reason = _state_allows_pg_read(state, len(pg_df))
            if allowed:
                _event(vpg, dataset_key, "phase5_pg_primary_read", f"rows={len(pg_df)}")
                return pg_df
            _event(vpg, dataset_key, "phase5_pg_reconcile_required", reason)
        except Exception as exc:
            _event(vpg, dataset_key, "phase5_pg_primary_read_error", f"{type(exc).__name__}: {str(exc)[:900]}")

        fallback = original_load_dataset(
            dataset_key,
            source_loader,
            ttl_seconds=ttl_seconds,
            force_refresh=True,
            wait_seconds=wait_seconds,
        )
        _event(vpg, dataset_key, "phase5_source_reconcile_fallback", f"rows={len(fallback) if isinstance(fallback, pd.DataFrame) else 0}")
        return fallback

    vpg.load_dataset = phase5_load_dataset
    vpg.phase5_is_enabled = lambda: is_active(vpg)
    vpg.phase5_read_backend = lambda: read_backend(vpg)
    vpg.get_phase5_status = lambda: get_status(vpg)
    vpg.ensure_phase5_schema = lambda: _ensure_phase5_schema(vpg)
    vpg.phase5_credentials_dataframe = lambda: _credentials_from_pg(vpg)
    vpg.phase5_leave_dataframe = lambda: _leave_from_pg(vpg)
    vpg._vera_phase5_installed = True
    return True
