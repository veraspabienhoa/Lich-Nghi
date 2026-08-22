"""Vera Spa PostgreSQL Phase 3 normalized CRUD + reconciliation layer.

Phase 3 intentionally keeps the existing Streamlit/business code untouched.
The current Google Sheets write path remains authoritative in ``dual`` mode,
while the two primary operational datasets are continuously reconciled into
normalized PostgreSQL tables:

- ``credentials`` -> ``employees``
- ``leave_primary`` -> ``leave_records``

The module also exposes explicit PostgreSQL CRUD helpers for the next cutover
step. Existing app writes invalidate the dataset; Phase 3 marks its normalized
copy stale and the next successful source load reconciles the full table.

Safety properties:
- ``sheets`` mode behaves exactly as before.
- PostgreSQL errors never block the legacy Google Sheets flow in ``dual`` mode.
- Empty/invalid snapshots do not destructively wipe normalized tables by
  default. Set VERA_PHASE3_ALLOW_EMPTY_SYNC=1 only for controlled maintenance.
- Every normalized row keeps the complete source row in JSONB ``payload`` so no
  source column is lost when schemas evolve.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
from typing import Any, Mapping, Optional

import pandas as pd
from sqlalchemy import text


PHASE3_SCHEMA_VERSION = 3
SYNC_STATE_TABLE = "vera_normalized_sync_state"
EMPLOYEE_DATASET = "credentials"
LEAVE_DATASET = "leave_primary"
TARGET_DATASETS = {EMPLOYEE_DATASET, LEAVE_DATASET}


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


def _active(vpg) -> bool:
    return _enabled(vpg) and _mode(vpg) in {"dual", "postgres"}


def _allow_empty_sync() -> bool:
    return str(os.getenv("VERA_PHASE3_ALLOW_EMPTY_SYNC", "") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _safe_int(value: Any) -> Optional[int]:
    s = _safe_text(value)
    if not s:
        return None
    try:
        return int(float(s.replace(",", "")))
    except Exception:
        return None


def _safe_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    try:
        if pd.isna(value):
            return Decimal("0")
    except Exception:
        pass
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    s = _safe_text(value)
    if not s:
        return Decimal("0")
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s or s in {"-", ".", ","}:
        return Decimal("0")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        tail = s.rsplit(",", 1)[-1]
        s = s.replace(",", "") if len(tail) == 3 else s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3 and parts[0].lstrip("-").isdigit()):
            s = s.replace(".", "")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = _safe_text(value).lower()
    return s in {"1", "true", "yes", "y", "x", "locked", "khoa", "khóa", "co", "có"}


def _safe_date(value: Any):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _safe_text(value)
    if not s:
        return None
    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def _jsonable(value: Any):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    return value


def _row_payload(row: Mapping[str, Any]) -> str:
    obj = {str(k): _jsonable(v) for k, v in dict(row).items()}
    return json.dumps(obj, ensure_ascii=False, default=str, allow_nan=False, separators=(",", ":"))


def _frame_checksum(df: pd.DataFrame) -> str:
    if not isinstance(df, pd.DataFrame):
        return ""
    rows = []
    for _, row in df.iterrows():
        rows.append({str(k): _jsonable(v) for k, v in row.to_dict().items()})
    payload = json.dumps(rows, ensure_ascii=False, default=str, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_phase3_schema(vpg, engine=None) -> None:
    if not _enabled(vpg):
        return
    engine = engine or vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    statements = [
        """
        CREATE TABLE IF NOT EXISTS employees (
            username TEXT PRIMARY KEY,
            stt INTEGER,
            password_value TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'nhanvien',
            full_name TEXT NOT NULL DEFAULT '',
            birth_date TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            bank_account TEXT NOT NULL DEFAULT '',
            bank_name TEXT NOT NULL DEFAULT '',
            monthly_generated NUMERIC NOT NULL DEFAULT 0,
            monthly_leave NUMERIC NOT NULL DEFAULT 0,
            annual_leave NUMERIC NOT NULL DEFAULT 0,
            work_shift TEXT NOT NULL DEFAULT '',
            shift_start_date TEXT NOT NULL DEFAULT '',
            rotation_cycle TEXT NOT NULL DEFAULT '',
            login_locked BOOLEAN NOT NULL DEFAULT FALSE,
            remember_token_hash TEXT NOT NULL DEFAULT '',
            remember_token_expiry TEXT NOT NULL DEFAULT '',
            employment_start_date TEXT NOT NULL DEFAULT '',
            source_sheet_id TEXT NOT NULL DEFAULT 'credentials',
            source_row INTEGER,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS employment_start_date TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS source_sheet_id TEXT NOT NULL DEFAULT 'credentials'",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS source_row INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb",
        "CREATE INDEX IF NOT EXISTS idx_employees_role ON employees(role)",
        "CREATE INDEX IF NOT EXISTS idx_employees_source_row ON employees(source_sheet_id, source_row)",
        """
        CREATE TABLE IF NOT EXISTS leave_records (
            id BIGSERIAL PRIMARY KEY,
            source_sheet_id TEXT NOT NULL DEFAULT '',
            source_row INTEGER,
            leave_date DATE,
            employee_name TEXT NOT NULL,
            leave_reason TEXT NOT NULL,
            leave_type TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            calculated_days NUMERIC NOT NULL DEFAULT 0,
            accumulated_leave NUMERIC NOT NULL DEFAULT 0,
            penalty NUMERIC NOT NULL DEFAULT 0,
            update_date TEXT NOT NULL DEFAULT '',
            update_time TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            weekday_label TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(source_sheet_id, source_row)
        )
        """,
        "ALTER TABLE leave_records ADD COLUMN IF NOT EXISTS weekday_label TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE leave_records ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_leave_records_source ON leave_records(source_sheet_id, source_row)",
        "CREATE INDEX IF NOT EXISTS idx_leave_records_date_employee ON leave_records(leave_date, employee_name)",
        "CREATE INDEX IF NOT EXISTS idx_leave_records_employee_date ON leave_records(employee_name, leave_date DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {SYNC_STATE_TABLE} (
            dataset_key TEXT PRIMARY KEY,
            table_name TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            checksum TEXT NOT NULL DEFAULT '',
            revision BIGINT NOT NULL DEFAULT 1,
            is_stale BOOLEAN NOT NULL DEFAULT FALSE,
            last_error TEXT NOT NULL DEFAULT '',
            synced_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{SYNC_STATE_TABLE}_stale ON {SYNC_STATE_TABLE}(is_stale, updated_at DESC)",
        f"""
        INSERT INTO {version_table}(component, version, updated_at)
        VALUES ('phase3_normalized_crud', {PHASE3_SCHEMA_VERSION}, NOW())
        ON CONFLICT (component) DO UPDATE
        SET version = GREATEST({version_table}.version, EXCLUDED.version),
            updated_at = NOW()
        """,
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _update_state_conn(conn, dataset_key: str, table_name: str, row_count: int, checksum: str,
                       stale: bool = False, error: str = "") -> None:
    conn.execute(
        text(
            f"""
            INSERT INTO {SYNC_STATE_TABLE}
                (dataset_key,table_name,row_count,checksum,revision,is_stale,last_error,synced_at,updated_at)
            VALUES
                (:k,:t,:n,:c,1,:stale,:err,CASE WHEN :stale THEN NULL ELSE NOW() END,NOW())
            ON CONFLICT (dataset_key)
            DO UPDATE SET
                table_name=EXCLUDED.table_name,
                row_count=EXCLUDED.row_count,
                checksum=EXCLUDED.checksum,
                revision={SYNC_STATE_TABLE}.revision + 1,
                is_stale=EXCLUDED.is_stale,
                last_error=EXCLUDED.last_error,
                synced_at=CASE WHEN EXCLUDED.is_stale THEN {SYNC_STATE_TABLE}.synced_at ELSE NOW() END,
                updated_at=NOW()
            """
        ),
        {"k": dataset_key, "t": table_name, "n": int(row_count), "c": checksum,
         "stale": bool(stale), "err": _safe_text(error)[:2000]},
    )


def _mark_state_stale(vpg, dataset_key: str, error: str = "") -> None:
    if dataset_key not in TARGET_DATASETS or not _active(vpg):
        return
    table_name = "employees" if dataset_key == EMPLOYEE_DATASET else "leave_records"
    try:
        engine = vpg.get_engine()
        _ensure_phase3_schema(vpg, engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {SYNC_STATE_TABLE}
                        (dataset_key,table_name,row_count,checksum,revision,is_stale,last_error,updated_at)
                    VALUES (:k,:t,0,'',1,TRUE,:err,NOW())
                    ON CONFLICT (dataset_key)
                    DO UPDATE SET is_stale=TRUE,last_error=:err,updated_at=NOW()
                    """
                ),
                {"k": dataset_key, "t": table_name, "err": _safe_text(error)[:2000]},
            )
    except Exception:
        pass


def _credential_record(row: Mapping[str, Any], source_row: int) -> Optional[dict]:
    username = _safe_text(row.get("Tên nhân viên"))
    if not username or username.lower() in {"none", "nan", "<na>"}:
        return None
    return {
        "username": username,
        "stt": _safe_int(row.get("STT")),
        "password_value": _safe_text(row.get("Mật khẩu")),
        "role": _safe_text(row.get("Phân quyền")) or "nhanvien",
        "full_name": _safe_text(row.get("Họ và tên đầy đủ")),
        "birth_date": _safe_text(row.get("Ngày sinh")),
        "phone": _safe_text(row.get("Điện thoại")),
        "email": _safe_text(row.get("Email")),
        "address": _safe_text(row.get("Địa chỉ")),
        "bank_account": _safe_text(row.get("Số tài khoản ngân hàng")),
        "bank_name": _safe_text(row.get("Tên ngân hàng")),
        "monthly_generated": _safe_decimal(row.get("Phát sinh tháng")),
        "monthly_leave": _safe_decimal(row.get("Có phép tháng")),
        "annual_leave": _safe_decimal(row.get("Phép năm")),
        "work_shift": _safe_text(row.get("Ca làm việc")),
        "shift_start_date": _safe_text(row.get("Ngày bắt đầu ca")),
        "rotation_cycle": _safe_text(row.get("Chu kỳ")),
        "login_locked": _safe_bool(row.get("Khóa đăng nhập")),
        "remember_token_hash": _safe_text(row.get("Remember Token Hash")),
        "remember_token_expiry": _safe_text(row.get("Remember Token Expiry")),
        "employment_start_date": _safe_text(row.get("Ngày bắt đầu làm")),
        "source_sheet_id": "credentials",
        "source_row": int(source_row),
        "payload": _row_payload(row),
    }


def _leave_record(row: Mapping[str, Any], fallback_row: int) -> Optional[dict]:
    employee = _safe_text(row.get("Tên nhân viên"))
    reason = _safe_text(row.get("Lý do nghỉ"))
    leave_date = _safe_date(row.get("Ngày"))
    if not employee or not reason:
        return None
    source_id = _safe_text(row.get("__source_sheet_id")) or "leave_primary"
    source_row = _safe_int(row.get("__source_row")) or int(fallback_row)
    return {
        "source_sheet_id": source_id,
        "source_row": source_row,
        "leave_date": leave_date,
        "employee_name": employee,
        "leave_reason": reason,
        "leave_type": _safe_text(row.get("Loại nghỉ")),
        "detail": _safe_text(row.get("Chi tiết")),
        "calculated_days": _safe_decimal(row.get("Số ngày tính")),
        "accumulated_leave": _safe_decimal(row.get("Số ngày phép cộng dồn")),
        "penalty": _safe_decimal(row.get("Phạt vi phạm")),
        "update_date": _safe_text(row.get("Ngày cập nhật")),
        "update_time": _safe_text(row.get("Giờ cập nhật")),
        "updated_by": _safe_text(row.get("Người cập nhật")),
        "weekday_label": _safe_text(row.get("Thứ ngày")),
        "payload": _row_payload(row),
    }


_EMPLOYEE_UPSERT_SQL = text(
    """
    INSERT INTO employees (
        username,stt,password_value,role,full_name,birth_date,phone,email,address,
        bank_account,bank_name,monthly_generated,monthly_leave,annual_leave,
        work_shift,shift_start_date,rotation_cycle,login_locked,remember_token_hash,
        remember_token_expiry,employment_start_date,source_sheet_id,source_row,payload,updated_at
    ) VALUES (
        :username,:stt,:password_value,:role,:full_name,:birth_date,:phone,:email,:address,
        :bank_account,:bank_name,:monthly_generated,:monthly_leave,:annual_leave,
        :work_shift,:shift_start_date,:rotation_cycle,:login_locked,:remember_token_hash,
        :remember_token_expiry,:employment_start_date,:source_sheet_id,:source_row,
        CAST(:payload AS JSONB),NOW()
    )
    ON CONFLICT (username) DO UPDATE SET
        stt=EXCLUDED.stt,password_value=EXCLUDED.password_value,role=EXCLUDED.role,
        full_name=EXCLUDED.full_name,birth_date=EXCLUDED.birth_date,phone=EXCLUDED.phone,
        email=EXCLUDED.email,address=EXCLUDED.address,bank_account=EXCLUDED.bank_account,
        bank_name=EXCLUDED.bank_name,monthly_generated=EXCLUDED.monthly_generated,
        monthly_leave=EXCLUDED.monthly_leave,annual_leave=EXCLUDED.annual_leave,
        work_shift=EXCLUDED.work_shift,shift_start_date=EXCLUDED.shift_start_date,
        rotation_cycle=EXCLUDED.rotation_cycle,login_locked=EXCLUDED.login_locked,
        remember_token_hash=EXCLUDED.remember_token_hash,
        remember_token_expiry=EXCLUDED.remember_token_expiry,
        employment_start_date=EXCLUDED.employment_start_date,
        source_sheet_id=EXCLUDED.source_sheet_id,source_row=EXCLUDED.source_row,
        payload=EXCLUDED.payload,updated_at=NOW()
    """
)

_LEAVE_UPSERT_SQL = text(
    """
    INSERT INTO leave_records (
        source_sheet_id,source_row,leave_date,employee_name,leave_reason,leave_type,detail,
        calculated_days,accumulated_leave,penalty,update_date,update_time,updated_by,
        weekday_label,payload,created_at,updated_at
    ) VALUES (
        :source_sheet_id,:source_row,:leave_date,:employee_name,:leave_reason,:leave_type,:detail,
        :calculated_days,:accumulated_leave,:penalty,:update_date,:update_time,:updated_by,
        :weekday_label,CAST(:payload AS JSONB),NOW(),NOW()
    )
    ON CONFLICT (source_sheet_id,source_row) DO UPDATE SET
        leave_date=EXCLUDED.leave_date,employee_name=EXCLUDED.employee_name,
        leave_reason=EXCLUDED.leave_reason,leave_type=EXCLUDED.leave_type,detail=EXCLUDED.detail,
        calculated_days=EXCLUDED.calculated_days,accumulated_leave=EXCLUDED.accumulated_leave,
        penalty=EXCLUDED.penalty,update_date=EXCLUDED.update_date,update_time=EXCLUDED.update_time,
        updated_by=EXCLUDED.updated_by,weekday_label=EXCLUDED.weekday_label,
        payload=EXCLUDED.payload,updated_at=NOW()
    """
)


def _sync_employees(vpg, df: pd.DataFrame) -> dict:
    records = []
    for offset, (_, row) in enumerate(df.iterrows(), start=2):
        item = _credential_record(row.to_dict(), offset)
        if item:
            records.append(item)
    if not records and not _allow_empty_sync():
        raise ValueError("credentials snapshot is empty/invalid; destructive Phase 3 sync skipped")

    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    checksum = _frame_checksum(df)
    current = {r["username"] for r in records}
    with engine.begin() as conn:
        for record in records:
            conn.execute(_EMPLOYEE_UPSERT_SQL, record)
        if current:
            existing = {str(r[0]) for r in conn.execute(text("SELECT username FROM employees")).all()}
            for username in existing - current:
                conn.execute(text("DELETE FROM employees WHERE username=:u"), {"u": username})
        elif _allow_empty_sync():
            conn.execute(text("DELETE FROM employees"))
        _update_state_conn(conn, EMPLOYEE_DATASET, "employees", len(records), checksum)
    return {"dataset_key": EMPLOYEE_DATASET, "table": "employees", "rows": len(records), "checksum": checksum}


def _sync_leave_records(vpg, df: pd.DataFrame) -> dict:
    records = []
    for offset, (_, row) in enumerate(df.iterrows(), start=2):
        item = _leave_record(row.to_dict(), offset)
        if item:
            records.append(item)
    if not records and not _allow_empty_sync():
        raise ValueError("leave_primary snapshot is empty/invalid; destructive Phase 3 sync skipped")

    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    checksum = _frame_checksum(df)
    current = {(r["source_sheet_id"], int(r["source_row"])) for r in records}
    with engine.begin() as conn:
        for record in records:
            conn.execute(_LEAVE_UPSERT_SQL, record)
        if current:
            existing = {
                (_safe_text(r[0]), int(r[1]))
                for r in conn.execute(text("SELECT source_sheet_id,source_row FROM leave_records WHERE source_row IS NOT NULL")).all()
                if r[1] is not None
            }
            for source_id, source_row in existing - current:
                conn.execute(
                    text("DELETE FROM leave_records WHERE source_sheet_id=:s AND source_row=:r"),
                    {"s": source_id, "r": source_row},
                )
        elif _allow_empty_sync():
            conn.execute(text("DELETE FROM leave_records"))
        _update_state_conn(conn, LEAVE_DATASET, "leave_records", len(records), checksum)
    return {"dataset_key": LEAVE_DATASET, "table": "leave_records", "rows": len(records), "checksum": checksum}


def sync_dataset(vpg, dataset_key: str, df: pd.DataFrame) -> Optional[dict]:
    if dataset_key not in TARGET_DATASETS or not _active(vpg):
        return None
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df if df is not None else [])
    try:
        result = _sync_employees(vpg, df) if dataset_key == EMPLOYEE_DATASET else _sync_leave_records(vpg, df)
        try:
            vpg.record_event(dataset_key, "phase3_normalized_sync", f"table={result['table']}; rows={result['rows']}")
        except Exception:
            pass
        return result
    except Exception as exc:
        _mark_state_stale(vpg, dataset_key, str(exc))
        try:
            vpg.record_event(dataset_key, "phase3_normalized_sync_error", f"{type(exc).__name__}: {str(exc)[:500]}")
        except Exception:
            pass
        if _mode(vpg) == "postgres" and str(os.getenv("VERA_PHASE3_STRICT", "") or "").strip() == "1":
            raise
        return None


def get_sync_status(vpg) -> pd.DataFrame:
    cols = ["dataset_key", "table_name", "row_count", "checksum", "revision", "is_stale", "last_error", "synced_at", "updated_at"]
    if not _enabled(vpg):
        return pd.DataFrame(columns=cols)
    try:
        engine = vpg.get_engine()
        _ensure_phase3_schema(vpg, engine)
        with engine.connect() as conn:
            rows = conn.execute(text(f"SELECT {','.join(cols)} FROM {SYNC_STATE_TABLE} ORDER BY dataset_key")).mappings().all()
        return pd.DataFrame([dict(r) for r in rows], columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


# -------- Explicit normalized PostgreSQL CRUD helpers --------
def list_employees(vpg) -> pd.DataFrame:
    if not _enabled(vpg):
        return pd.DataFrame()
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM employees ORDER BY COALESCE(stt,2147483647), username")).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def get_employee(vpg, username: str) -> Optional[dict]:
    if not _enabled(vpg):
        return None
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM employees WHERE username=:u"), {"u": _safe_text(username)}).mappings().first()
    return dict(row) if row else None


def upsert_employee(vpg, record: Mapping[str, Any]) -> dict:
    raw = dict(record or {})
    username = _safe_text(raw.get("username") or raw.get("Tên nhân viên"))
    if not username:
        raise ValueError("username/Tên nhân viên is required")
    if "Tên nhân viên" in raw:
        normalized = _credential_record(raw, _safe_int(raw.get("source_row")) or 0)
    else:
        normalized = {
            "username": username,
            "stt": _safe_int(raw.get("stt")),
            "password_value": _safe_text(raw.get("password_value")),
            "role": _safe_text(raw.get("role")) or "nhanvien",
            "full_name": _safe_text(raw.get("full_name")),
            "birth_date": _safe_text(raw.get("birth_date")),
            "phone": _safe_text(raw.get("phone")),
            "email": _safe_text(raw.get("email")),
            "address": _safe_text(raw.get("address")),
            "bank_account": _safe_text(raw.get("bank_account")),
            "bank_name": _safe_text(raw.get("bank_name")),
            "monthly_generated": _safe_decimal(raw.get("monthly_generated")),
            "monthly_leave": _safe_decimal(raw.get("monthly_leave")),
            "annual_leave": _safe_decimal(raw.get("annual_leave")),
            "work_shift": _safe_text(raw.get("work_shift")),
            "shift_start_date": _safe_text(raw.get("shift_start_date")),
            "rotation_cycle": _safe_text(raw.get("rotation_cycle")),
            "login_locked": _safe_bool(raw.get("login_locked")),
            "remember_token_hash": _safe_text(raw.get("remember_token_hash")),
            "remember_token_expiry": _safe_text(raw.get("remember_token_expiry")),
            "employment_start_date": _safe_text(raw.get("employment_start_date")),
            "source_sheet_id": _safe_text(raw.get("source_sheet_id")) or "credentials",
            "source_row": _safe_int(raw.get("source_row")),
            "payload": _row_payload(raw),
        }
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.begin() as conn:
        conn.execute(_EMPLOYEE_UPSERT_SQL, normalized)
    return get_employee(vpg, username) or {"username": username}


def delete_employee(vpg, username: str) -> bool:
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM employees WHERE username=:u"), {"u": _safe_text(username)})
    return bool(getattr(result, "rowcount", 0))


def list_leave_records(vpg, employee_name: str = "", start_date=None, end_date=None) -> pd.DataFrame:
    if not _enabled(vpg):
        return pd.DataFrame()
    clauses = []
    params = {}
    if _safe_text(employee_name):
        clauses.append("employee_name=:employee")
        params["employee"] = _safe_text(employee_name)
    if _safe_date(start_date):
        clauses.append("leave_date>=:start_date")
        params["start_date"] = _safe_date(start_date)
    if _safe_date(end_date):
        clauses.append("leave_date<=:end_date")
        params["end_date"] = _safe_date(end_date)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM leave_records" + where + " ORDER BY leave_date DESC, source_row DESC"), params).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def upsert_leave_record(vpg, record: Mapping[str, Any]) -> dict:
    raw = dict(record or {})
    normalized = _leave_record(raw, _safe_int(raw.get("source_row") or raw.get("__source_row")) or 0)
    if normalized is None:
        employee = _safe_text(raw.get("employee_name"))
        reason = _safe_text(raw.get("leave_reason"))
        if not employee or not reason:
            raise ValueError("employee_name/Tên nhân viên and leave_reason/Lý do nghỉ are required")
        normalized = {
            "source_sheet_id": _safe_text(raw.get("source_sheet_id")) or "leave_primary",
            "source_row": _safe_int(raw.get("source_row")) or 0,
            "leave_date": _safe_date(raw.get("leave_date")),
            "employee_name": employee,
            "leave_reason": reason,
            "leave_type": _safe_text(raw.get("leave_type")),
            "detail": _safe_text(raw.get("detail")),
            "calculated_days": _safe_decimal(raw.get("calculated_days")),
            "accumulated_leave": _safe_decimal(raw.get("accumulated_leave")),
            "penalty": _safe_decimal(raw.get("penalty")),
            "update_date": _safe_text(raw.get("update_date")),
            "update_time": _safe_text(raw.get("update_time")),
            "updated_by": _safe_text(raw.get("updated_by")),
            "weekday_label": _safe_text(raw.get("weekday_label")),
            "payload": _row_payload(raw),
        }
    if not normalized["source_row"]:
        raise ValueError("source_row/__source_row is required for normalized leave CRUD")
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.begin() as conn:
        conn.execute(_LEAVE_UPSERT_SQL, normalized)
        row = conn.execute(
            text("SELECT * FROM leave_records WHERE source_sheet_id=:s AND source_row=:r"),
            {"s": normalized["source_sheet_id"], "r": normalized["source_row"]},
        ).mappings().first()
    return dict(row) if row else normalized


def delete_leave_record(vpg, source_sheet_id: str, source_row: int) -> bool:
    engine = vpg.get_engine()
    _ensure_phase3_schema(vpg, engine)
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM leave_records WHERE source_sheet_id=:s AND source_row=:r"),
            {"s": _safe_text(source_sheet_id), "r": int(source_row)},
        )
    return bool(getattr(result, "rowcount", 0))


def install(vpg) -> bool:
    """Install Phase-3 wrappers after Phase 2 has wrapped ``vera_postgres``."""
    if vpg is None or getattr(vpg, "_vera_phase3_installed", False):
        return bool(vpg is not None)
    if not all(callable(getattr(vpg, name, None)) for name in ("load_dataset", "invalidate_dataset", "write_dataset")):
        return False

    original_load_dataset = vpg.load_dataset
    original_invalidate_dataset = vpg.invalidate_dataset
    original_write_dataset = vpg.write_dataset

    def phase3_load_dataset(dataset_key, source_loader, ttl_seconds=120, force_refresh=False, wait_seconds=3.0):
        df = original_load_dataset(
            dataset_key,
            source_loader,
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
            wait_seconds=wait_seconds,
        )
        if dataset_key in TARGET_DATASETS and _active(vpg):
            sync_dataset(vpg, dataset_key, df)
        return df

    def phase3_invalidate_dataset(dataset_key):
        original_invalidate_dataset(dataset_key)
        if dataset_key in TARGET_DATASETS and _active(vpg):
            _mark_state_stale(vpg, dataset_key)

    def phase3_write_dataset(dataset_key, df, ttl_seconds=120, source_version=""):
        out = original_write_dataset(
            dataset_key,
            df,
            ttl_seconds=ttl_seconds,
            source_version=source_version,
        )
        if dataset_key in TARGET_DATASETS and _active(vpg):
            sync_dataset(vpg, dataset_key, df)
        return out

    vpg.load_dataset = phase3_load_dataset
    vpg.invalidate_dataset = phase3_invalidate_dataset
    vpg.write_dataset = phase3_write_dataset
    vpg.ensure_phase3_schema = lambda: _ensure_phase3_schema(vpg)
    vpg.phase3_sync_dataset = lambda dataset_key, df: sync_dataset(vpg, dataset_key, df)
    vpg.get_phase3_status = lambda: get_sync_status(vpg)
    vpg.list_employees_pg = lambda: list_employees(vpg)
    vpg.get_employee_pg = lambda username: get_employee(vpg, username)
    vpg.upsert_employee_pg = lambda record: upsert_employee(vpg, record)
    vpg.delete_employee_pg = lambda username: delete_employee(vpg, username)
    vpg.list_leave_records_pg = lambda employee_name="", start_date=None, end_date=None: list_leave_records(
        vpg, employee_name=employee_name, start_date=start_date, end_date=end_date
    )
    vpg.upsert_leave_record_pg = lambda record: upsert_leave_record(vpg, record)
    vpg.delete_leave_record_pg = lambda source_sheet_id, source_row: delete_leave_record(vpg, source_sheet_id, source_row)
    vpg._vera_phase3_installed = True
    return True
