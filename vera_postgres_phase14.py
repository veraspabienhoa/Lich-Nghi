"""Vera Spa PostgreSQL Phase 14: operational HR records PostgreSQL-primary.

Migrates these operational datasets away from direct Google-Sheets-primary reads/writes:
- TrangThaiNhanSu
- NghiDaiHan
- LichHenNhanSu

PostgreSQL stores durable records keyed by stable logical IDs. Google Sheets remains a
synchronous mirror during the migration window.

Set VERA_PHASE14_OPERATIONS_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import text


PHASE14_SCHEMA_VERSION = 14
RECORD_TABLE = "vera_phase14_record"
STATE_TABLE = "vera_phase14_dataset_state"
VALID_DATASETS = {"employment_status", "long_leave", "staff_plan"}


def _enabled(vpg):
    try:
        return bool(vpg.is_enabled())
    except Exception:
        return False


def _mode(vpg):
    try:
        return str(vpg.data_backend_mode() or "sheets").strip().lower()
    except Exception:
        return "sheets"


def operations_backend(vpg):
    raw = str(os.getenv("VERA_PHASE14_OPERATIONS_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg):
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and operations_backend(vpg) == "postgres"
        and callable(getattr(vpg, "get_engine", None))
    )


def _event(vpg, event_type, detail=""):
    try:
        vpg.record_event(
            "phase14",
            str(event_type),
            str(detail or "")[:1800],
        )
    except Exception:
        pass


def _copy(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _validate_dataset(dataset):
    dataset = str(dataset or "").strip()
    if dataset not in VALID_DATASETS:
        raise ValueError(f"Unsupported Phase 14 dataset: {dataset}")
    return dataset


def _ensure_schema(vpg):
    if not _enabled(vpg):
        return
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with vpg.get_engine().begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RECORD_TABLE} (
                dataset TEXT NOT NULL,
                logical_id TEXT NOT NULL,
                source_row INTEGER,
                employee_key TEXT NOT NULL DEFAULT '',
                record_type TEXT NOT NULL DEFAULT '',
                record_status TEXT NOT NULL DEFAULT '',
                date_from TEXT NOT NULL DEFAULT '',
                date_to TEXT NOT NULL DEFAULT '',
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                source TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '',
                revision BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (dataset, logical_id)
            )
        """))
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_{RECORD_TABLE}_employee
            ON {RECORD_TABLE}(dataset, employee_key)
        """))
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_{RECORD_TABLE}_status
            ON {RECORD_TABLE}(dataset, record_status)
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                dataset TEXT PRIMARY KEY,
                seeded BOOLEAN NOT NULL DEFAULT FALSE,
                source TEXT NOT NULL DEFAULT '',
                revision BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(
            text(f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase14_operational_records', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
            """),
            {"version": PHASE14_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg, dataset):
    dataset = _validate_dataset(dataset)
    if not is_active(vpg):
        yield
        return
    conn = vpg.get_engine().connect()
    locked = False
    try:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtext(:k))"),
            {"k": f"vera:phase14:{dataset}"},
        )
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"),
                    {"k": f"vera:phase14:{dataset}"},
                )
            except Exception:
                pass
        conn.close()


def _decode_payload(raw):
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _metadata(record):
    record = record if isinstance(record, dict) else {}
    employee = str(record.get("Tên nhân viên", "") or record.get("employee_name", "") or "").strip()
    record_type = str(
        record.get("Loại đơn", "")
        or record.get("Loại kế hoạch", "")
        or record.get("record_type", "")
        or ""
    ).strip()
    record_status = str(record.get("Trạng thái", "") or record.get("status", "") or "").strip()
    date_from = str(record.get("Từ ngày", "") or record.get("date_from", "") or "").strip()
    date_to = str(record.get("Đến ngày", "") or record.get("date_to", "") or "").strip()
    try:
        import unicodedata
        key = unicodedata.normalize("NFKD", employee)
        key = "".join(ch for ch in key if not unicodedata.combining(ch)).casefold().strip()
    except Exception:
        key = employee.casefold()
    return key, record_type, record_status, date_from, date_to


def _normalize_record(record):
    rec = dict(record or {})
    logical_id = str(rec.pop("__phase14_id", "") or "").strip()
    if not logical_id:
        raise ValueError("Phase 14 record is missing __phase14_id")
    source_row = rec.get("__row")
    try:
        source_row = int(source_row) if source_row not in (None, "") else None
    except Exception:
        source_row = None
    return logical_id, source_row, rec


def _dataset_seeded(conn, dataset):
    row = conn.execute(
        text(f"SELECT seeded FROM {STATE_TABLE} WHERE dataset=:dataset"),
        {"dataset": dataset},
    ).mappings().first()
    return bool(row and row.get("seeded"))


def _load_records_conn(conn, dataset):
    rows = conn.execute(
        text(f"""
            SELECT logical_id, source_row, payload
            FROM {RECORD_TABLE}
            WHERE dataset=:dataset
            ORDER BY COALESCE(source_row, 2147483647), logical_id
        """),
        {"dataset": dataset},
    ).mappings().all()
    out = []
    for row in rows:
        rec = _decode_payload(row.get("payload"))
        if row.get("source_row") is not None:
            rec["__row"] = int(row["source_row"])
        rec["__phase14_id"] = str(row.get("logical_id") or "")
        out.append(rec)
    return out


def _load_records(vpg, dataset):
    dataset = _validate_dataset(dataset)
    with vpg.get_engine().begin() as conn:
        return _dataset_seeded(conn, dataset), _load_records_conn(conn, dataset)


def _replace_records(vpg, dataset, records, *, updated_by="", source="postgres_primary"):
    dataset = _validate_dataset(dataset)
    normalized = []
    for item in list(records or []):
        logical_id, source_row, rec = _normalize_record(item)
        employee_key, record_type, record_status, date_from, date_to = _metadata(rec)
        normalized.append({
            "dataset": dataset,
            "logical_id": logical_id,
            "source_row": source_row,
            "employee_key": employee_key,
            "record_type": record_type,
            "record_status": record_status,
            "date_from": date_from,
            "date_to": date_to,
            "payload": json.dumps(rec, ensure_ascii=False, default=str),
            "source": str(source or ""),
            "updated_by": str(updated_by or ""),
        })

    with vpg.get_engine().begin() as conn:
        conn.execute(
            text(f"DELETE FROM {RECORD_TABLE} WHERE dataset=:dataset"),
            {"dataset": dataset},
        )
        if normalized:
            conn.execute(
                text(f"""
                    INSERT INTO {RECORD_TABLE}(
                        dataset, logical_id, source_row, employee_key,
                        record_type, record_status, date_from, date_to,
                        payload, source, updated_by, revision, updated_at
                    )
                    VALUES (
                        :dataset, :logical_id, :source_row, :employee_key,
                        :record_type, :record_status, :date_from, :date_to,
                        CAST(:payload AS jsonb), :source, :updated_by, 1, NOW()
                    )
                """),
                normalized,
            )
        conn.execute(
            text(f"""
                INSERT INTO {STATE_TABLE}(dataset, seeded, source, revision, updated_at)
                VALUES (:dataset, TRUE, :source, 1, NOW())
                ON CONFLICT (dataset) DO UPDATE
                SET seeded=TRUE,
                    source=EXCLUDED.source,
                    revision={STATE_TABLE}.revision + 1,
                    updated_at=NOW()
            """),
            {"dataset": dataset, "source": str(source or "")},
        )


def _call_source(source_loader):
    value = source_loader()
    if value is None:
        return []
    if not isinstance(value, list):
        value = list(value)
    return [dict(x) for x in value if isinstance(x, dict)]


def _failed(result):
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def read_records(vpg, dataset, source_loader: Callable[[], list[dict]]):
    dataset = _validate_dataset(dataset)
    if not is_active(vpg):
        try:
            return _call_source(source_loader)
        except Exception:
            return []

    try:
        seeded, records = _load_records(vpg, dataset)
        if seeded:
            _event(vpg, f"phase14_{dataset}_pg_read", f"records={len(records)}")
            return _copy(records)
    except Exception as exc:
        _event(vpg, f"phase14_{dataset}_pg_read_error", f"{type(exc).__name__}: {exc}")
        try:
            return _call_source(source_loader)
        except Exception:
            return []

    try:
        records = _call_source(source_loader)
        _replace_records(
            vpg, dataset, records,
            updated_by="phase14-seed", source="google_sheets_seed",
        )
        _event(vpg, f"phase14_{dataset}_seeded", f"records={len(records)}")
        return _copy(records)
    except Exception as exc:
        _event(vpg, f"phase14_{dataset}_seed_error", f"{type(exc).__name__}: {exc}")
        return []


def mutate_records(
    vpg,
    dataset,
    source_loader: Callable[[], list[dict]],
    mutator: Callable[[list[dict]], list[dict]],
    mirror_fn: Callable[[], Any],
    *,
    updated_by="",
    confirm_fn: Optional[Callable[[], list[dict]]] = None,
):
    dataset = _validate_dataset(dataset)
    if not is_active(vpg):
        return mirror_fn()

    with _lock(vpg, dataset):
        seeded, before = _load_records(vpg, dataset)
        if not seeded:
            before = _call_source(source_loader)
            _replace_records(
                vpg, dataset, before,
                updated_by="phase14-seed", source="google_sheets_seed",
            )

        before = _copy(before)
        next_records = mutator(_copy(before))
        if next_records is None:
            next_records = before
        next_records = [dict(x) for x in list(next_records) if isinstance(x, dict)]

        written = False
        try:
            _replace_records(
                vpg, dataset, next_records,
                updated_by=updated_by, source="postgres_primary",
            )
            written = True
            _event(vpg, f"phase14_{dataset}_pg_write", f"records={len(next_records)}")

            result = mirror_fn()
            if _failed(result):
                _replace_records(
                    vpg, dataset, before,
                    updated_by="phase14-compensation", source="phase14_compensation",
                )
                _event(vpg, f"phase14_{dataset}_compensated", "mirror_returned_failure")
                return result

            confirmed = next_records
            if callable(confirm_fn):
                try:
                    confirmed = _call_source(confirm_fn)
                except Exception as exc:
                    _event(
                        vpg, f"phase14_{dataset}_confirm_warning",
                        f"{type(exc).__name__}: {exc}",
                    )
            _replace_records(
                vpg, dataset, confirmed,
                updated_by=updated_by, source="postgres_primary_confirmed",
            )
            _event(vpg, f"phase14_{dataset}_sheet_mirror_ok", f"records={len(confirmed)}")
            return result
        except Exception as exc:
            if written:
                try:
                    _replace_records(
                        vpg, dataset, before,
                        updated_by="phase14-compensation", source="phase14_compensation",
                    )
                    _event(vpg, f"phase14_{dataset}_compensated", type(exc).__name__)
                except Exception:
                    pass
            raise


def get_status(vpg):
    result = {
        "enabled": bool(is_active(vpg)),
        "operations_backend": operations_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE14_SCHEMA_VERSION,
        "datasets": {},
    }
    if not _enabled(vpg):
        return result
    try:
        with vpg.get_engine().begin() as conn:
            rows = conn.execute(text(f"""
                SELECT s.dataset, s.seeded, s.source, s.revision, s.updated_at,
                       COUNT(r.logical_id) AS record_count
                FROM {STATE_TABLE} s
                LEFT JOIN {RECORD_TABLE} r ON r.dataset=s.dataset
                GROUP BY s.dataset, s.seeded, s.source, s.revision, s.updated_at
            """)).mappings().all()
        for row in rows:
            result["datasets"][str(row.get("dataset"))] = {
                "seeded": bool(row.get("seeded")),
                "source": row.get("source"),
                "revision": row.get("revision"),
                "updated_at": row.get("updated_at"),
                "record_count": int(row.get("record_count") or 0),
            }
    except Exception:
        pass
    return result


def install(vpg):
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase14_installed", False):
        return True
    if not callable(getattr(vpg, "get_engine", None)):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase14_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase14_is_enabled = lambda: is_active(vpg)
    vpg.phase14_operations_backend = lambda: operations_backend(vpg)
    vpg.phase14_read_records = lambda dataset, source_loader: read_records(
        vpg, dataset, source_loader
    )
    vpg.phase14_mutate_records = lambda dataset, source_loader, mutator, mirror_fn, updated_by="", confirm_fn=None: mutate_records(
        vpg, dataset, source_loader, mutator, mirror_fn,
        updated_by=updated_by, confirm_fn=confirm_fn,
    )
    vpg.get_phase14_status = lambda: get_status(vpg)
    vpg.ensure_phase14_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase14_installed = True
    return True
