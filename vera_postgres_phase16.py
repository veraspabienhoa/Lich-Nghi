"""Vera Spa PostgreSQL Phase 16: residual audit/notification persistence.

Phase 16 moves the verified remaining high-frequency Google-Sheets-primary paths:
- NhatKyLichNghi (leave activity Log Book)
- ThongBaoSuaXoaLichNghi (admin leave-edit/delete notices)

Small credential control writes are patched to reuse Phase-4 PostgreSQL-first employee
transactions instead of creating duplicate storage.

Google Sheets stays a synchronous mirror. Set VERA_PHASE16_RESIDUAL_BACKEND=sheets
for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from typing import Any, Callable

from sqlalchemy import text

PHASE16_SCHEMA_VERSION = 16
RECORD_TABLE = "vera_phase16_record"
STATE_TABLE = "vera_phase16_dataset_state"
VALID_DATASETS = {"leave_activity_log", "leave_audit_notice"}


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


def residual_backend(vpg):
    raw = str(os.getenv("VERA_PHASE16_RESIDUAL_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg):
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and residual_backend(vpg) == "postgres"
        and callable(getattr(vpg, "get_engine", None))
    )


def _valid_dataset(dataset):
    dataset = str(dataset or "").strip()
    if dataset not in VALID_DATASETS:
        raise ValueError(f"Unsupported Phase 16 dataset: {dataset}")
    return dataset


def _event(vpg, dataset, event_type, detail=""):
    try:
        vpg.record_event(f"phase16:{dataset}", str(event_type), str(detail or "")[:1800])
    except Exception:
        pass


def _copy(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


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
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                updated_by TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (dataset, logical_id)
            )
        """))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{RECORD_TABLE}_dataset_row ON {RECORD_TABLE}(dataset, source_row)"))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                dataset TEXT PRIMARY KEY,
                seeded BOOLEAN NOT NULL DEFAULT FALSE,
                source TEXT NOT NULL DEFAULT '',
                revision BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text(f"""
            INSERT INTO {version_table}(component, version, updated_at)
            VALUES ('phase16_residual_records', :version, NOW())
            ON CONFLICT (component) DO UPDATE
            SET version=GREATEST({version_table}.version, EXCLUDED.version), updated_at=NOW()
        """), {"version": PHASE16_SCHEMA_VERSION})


@contextmanager
def _lock(vpg, dataset):
    dataset = _valid_dataset(dataset)
    if not is_active(vpg):
        yield
        return
    conn = vpg.get_engine().connect()
    locked = False
    try:
        conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"vera:phase16:{dataset}"})
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"vera:phase16:{dataset}"})
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _decode_payload(raw):
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _seeded(conn, dataset):
    row = conn.execute(text(f"SELECT seeded FROM {STATE_TABLE} WHERE dataset=:d"), {"d": dataset}).mappings().first()
    return bool(row and row.get("seeded"))


def _load_conn(conn, dataset):
    rows = conn.execute(text(f"""
        SELECT logical_id, source_row, payload FROM {RECORD_TABLE}
        WHERE dataset=:d ORDER BY COALESCE(source_row, 2147483647), created_at, logical_id
    """), {"d": dataset}).mappings().all()
    out = []
    for row in rows:
        rec = _decode_payload(row.get("payload"))
        rec["__phase16_id"] = str(row.get("logical_id") or "")
        if row.get("source_row") is not None:
            rec["__row"] = int(row.get("source_row"))
        out.append(rec)
    return out


def _load(vpg, dataset):
    with vpg.get_engine().begin() as conn:
        return _seeded(conn, dataset), _load_conn(conn, dataset)


def _normalize(record):
    rec = dict(record or {})
    logical_id = str(rec.pop("__phase16_id", "") or "").strip() or str(rec.get("ID", "") or "").strip()
    if not logical_id:
        raise ValueError("Phase 16 record requires logical ID")
    source_row = rec.get("__row")
    try:
        source_row = int(source_row) if source_row not in (None, "") else None
    except Exception:
        source_row = None
    rec.pop("__row", None)
    return logical_id, source_row, rec


def _replace(vpg, dataset, records, *, updated_by="", source="postgres_primary"):
    dataset = _valid_dataset(dataset)
    normalized = []
    for raw in list(records or []):
        logical_id, source_row, rec = _normalize(raw)
        normalized.append({
            "dataset": dataset, "logical_id": logical_id, "source_row": source_row,
            "payload": json.dumps(rec, ensure_ascii=False, default=str),
            "updated_by": str(updated_by or ""), "source": str(source or ""),
        })
    with vpg.get_engine().begin() as conn:
        conn.execute(text(f"DELETE FROM {RECORD_TABLE} WHERE dataset=:d"), {"d": dataset})
        if normalized:
            conn.execute(text(f"""
                INSERT INTO {RECORD_TABLE}(dataset,logical_id,source_row,payload,updated_by,source,created_at,updated_at)
                VALUES(:dataset,:logical_id,:source_row,CAST(:payload AS jsonb),:updated_by,:source,NOW(),NOW())
            """), normalized)
        conn.execute(text(f"""
            INSERT INTO {STATE_TABLE}(dataset,seeded,source,revision,updated_at)
            VALUES(:d,TRUE,:s,1,NOW())
            ON CONFLICT(dataset) DO UPDATE SET seeded=TRUE,source=EXCLUDED.source,
                revision={STATE_TABLE}.revision+1,updated_at=NOW()
        """), {"d": dataset, "s": str(source or "")})


def _call_source(source_loader):
    value = source_loader()
    if value is None:
        return []
    return [dict(x) for x in list(value) if isinstance(x, dict)]


def _failed(result):
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def read_records(vpg, dataset, source_loader: Callable[[], list[dict]]):
    dataset = _valid_dataset(dataset)
    if not is_active(vpg):
        try:
            return _call_source(source_loader)
        except Exception:
            return []
    try:
        seeded, rows = _load(vpg, dataset)
        if seeded:
            _event(vpg, dataset, "phase16_pg_read", f"records={len(rows)}")
            return _copy(rows)
    except Exception as exc:
        _event(vpg, dataset, "phase16_pg_read_error", f"{type(exc).__name__}: {exc}")
        try:
            return _call_source(source_loader)
        except Exception:
            return []
    try:
        rows = _call_source(source_loader)
        _replace(vpg, dataset, rows, updated_by="phase16-seed", source="google_sheets_seed")
        _event(vpg, dataset, "phase16_seeded", f"records={len(rows)}")
        return _copy(rows)
    except Exception as exc:
        _event(vpg, dataset, "phase16_seed_error", f"{type(exc).__name__}: {exc}")
        return []


def mutate_records(vpg, dataset, source_loader, mutator, mirror_fn, *, updated_by=""):
    dataset = _valid_dataset(dataset)
    if not is_active(vpg):
        return mirror_fn()
    with _lock(vpg, dataset):
        seeded, before = _load(vpg, dataset)
        if not seeded:
            before = _call_source(source_loader)
            _replace(vpg, dataset, before, updated_by="phase16-seed", source="google_sheets_seed")
        before = _copy(before)
        after = mutator(_copy(before))
        if after is None:
            after = before
        after = [dict(x) for x in list(after) if isinstance(x, dict)]
        written = False
        try:
            _replace(vpg, dataset, after, updated_by=updated_by, source="postgres_primary")
            written = True
            result = mirror_fn()
            if _failed(result):
                _replace(vpg, dataset, before, updated_by="phase16-compensation", source="phase16_compensation")
                _event(vpg, dataset, "phase16_compensated", "mirror_returned_failure")
                return result
            _event(vpg, dataset, "phase16_sheet_mirror_ok", f"records={len(after)}")
            return result
        except Exception as exc:
            if written:
                try:
                    _replace(vpg, dataset, before, updated_by="phase16-compensation", source="phase16_compensation")
                    _event(vpg, dataset, "phase16_compensated", type(exc).__name__)
                except Exception:
                    pass
            raise


def get_status(vpg):
    result = {"enabled": bool(is_active(vpg)), "residual_backend": residual_backend(vpg), "data_backend": _mode(vpg), "schema_version": PHASE16_SCHEMA_VERSION, "datasets": {}}
    if not _enabled(vpg):
        return result
    try:
        with vpg.get_engine().begin() as conn:
            rows = conn.execute(text(f"""
                SELECT s.dataset,s.seeded,s.source,s.revision,s.updated_at,COUNT(r.logical_id) AS record_count
                FROM {STATE_TABLE} s LEFT JOIN {RECORD_TABLE} r ON r.dataset=s.dataset
                GROUP BY s.dataset,s.seeded,s.source,s.revision,s.updated_at
            """)).mappings().all()
        for row in rows:
            result["datasets"][str(row.get("dataset"))] = {
                "seeded": bool(row.get("seeded")), "source": row.get("source"),
                "revision": row.get("revision"), "updated_at": row.get("updated_at"),
                "record_count": int(row.get("record_count") or 0),
            }
    except Exception:
        pass
    return result


def install(vpg):
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase16_installed", False):
        return True
    if not callable(getattr(vpg, "get_engine", None)):
        return False
    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "schema", "phase16_schema_warning", f"{type(exc).__name__}: {exc}")
    vpg.phase16_is_enabled = lambda: is_active(vpg)
    vpg.phase16_residual_backend = lambda: residual_backend(vpg)
    vpg.phase16_read_records = lambda dataset, source_loader: read_records(vpg, dataset, source_loader)
    vpg.phase16_mutate_records = lambda dataset, source_loader, mutator, mirror_fn, updated_by="": mutate_records(vpg, dataset, source_loader, mutator, mirror_fn, updated_by=updated_by)
    vpg.get_phase16_status = lambda: get_status(vpg)
    vpg.ensure_phase16_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase16_installed = True
    return True
