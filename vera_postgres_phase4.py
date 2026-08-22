"""Vera Spa PostgreSQL Phase 4: PostgreSQL-primary operational writes.

Phase 4 moves the core employee and leave CRUD write paths to PostgreSQL-first
while keeping Google Sheets as a synchronous mirror/rollback path.

Covered operational mutations:
- employees: create, profile update, bulk profile update, delete
- leave_records: single create, range create, reason/recalculation update, delete

The Streamlit UI and existing business validation remain in app_v92699_core.py.
Only the persistence order changes when PostgreSQL is enabled:

    PostgreSQL normalized CRUD -> Google Sheets mirror -> normal cache invalidation

If the Google Sheets mirror raises an error, PostgreSQL is compensated back to
its pre-operation state before the error is returned to the existing caller.
A PostgreSQL advisory lock serializes Phase-4 writes per dataset across Cloud Run
instances. Set VERA_PHASE4_WRITE_BACKEND=sheets for an immediate write-path
rollback without changing code or the read backend.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Any, Callable, Iterable, Mapping, Optional

import pandas as pd
from sqlalchemy import text


PHASE4_SCHEMA_VERSION = 4
EMPLOYEE_DATASET = "credentials"
LEAVE_DATASET = "leave_primary"
TARGET_DATASETS = {EMPLOYEE_DATASET, LEAVE_DATASET}


class Phase4MirrorError(RuntimeError):
    """Raised when PostgreSQL wrote first but the Google Sheets mirror failed."""


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


def write_backend(vpg) -> str:
    raw = str(os.getenv("VERA_PHASE4_WRITE_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg) -> bool:
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and write_backend(vpg) == "postgres"
        and bool(getattr(vpg, "_vera_phase3_installed", False))
    )


def _ensure_phase4_schema(vpg) -> None:
    if not _enabled(vpg):
        return
    engine = vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase4_postgres_primary_writes', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version = GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at = NOW()
                """
            ),
            {"version": PHASE4_SCHEMA_VERSION},
        )


def _event(vpg, dataset_key: str, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(str(dataset_key), str(event_type), str(detail or "")[:1800])
    except Exception:
        pass


def _mirror_result_failed(result: Any) -> bool:
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


@contextmanager
def _dataset_lock(vpg, dataset_key: str):
    """Serialize Phase-4 writes for one dataset across application instances."""
    if not is_active(vpg):
        yield
        return
    engine = vpg.get_engine()
    conn = engine.connect()
    lock_key = f"vera:phase4:{dataset_key}"
    locked = False
    try:
        conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": lock_key})
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key})
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _call_mirror(mirror_fn: Callable[[], Any]) -> Any:
    result = mirror_fn()
    if _mirror_result_failed(result):
        raise Phase4MirrorError(f"Google Sheets mirror returned failure: {result}")
    return result


def _get_employee(vpg, username: str) -> Optional[dict]:
    fn = getattr(vpg, "get_employee_pg", None)
    if not callable(fn):
        return None
    return fn(str(username or ""))


def _restore_employee(vpg, before: Optional[Mapping[str, Any]], username: str) -> None:
    if before:
        vpg.upsert_employee_pg(dict(before))
    else:
        vpg.delete_employee_pg(str(username or ""))


def employee_upsert(vpg, record: Mapping[str, Any], mirror_fn: Callable[[], Any], operation: str = "upsert") -> Any:
    if not is_active(vpg):
        return mirror_fn()
    raw = dict(record or {})
    username = str(raw.get("Tên nhân viên") or raw.get("username") or "").strip()
    if not username:
        raise ValueError("Phase 4 employee write requires Tên nhân viên/username")

    with _dataset_lock(vpg, EMPLOYEE_DATASET):
        before = _get_employee(vpg, username)
        try:
            vpg.upsert_employee_pg(raw)
            _event(vpg, EMPLOYEE_DATASET, "phase4_pg_primary_write", f"{operation}; employee={username}")
            result = _call_mirror(mirror_fn)
            _event(vpg, EMPLOYEE_DATASET, "phase4_sheet_mirror_ok", f"{operation}; employee={username}")
            return result
        except Exception as exc:
            try:
                _restore_employee(vpg, before, username)
                _event(vpg, EMPLOYEE_DATASET, "phase4_compensated", f"{operation}; employee={username}; mirror_error={type(exc).__name__}: {str(exc)[:600]}")
            except Exception as restore_exc:
                _event(vpg, EMPLOYEE_DATASET, "phase4_compensation_error", f"{operation}; employee={username}; mirror={str(exc)[:350]}; restore={str(restore_exc)[:350]}")
                raise Phase4MirrorError(f"Google Sheets mirror failed and PostgreSQL compensation also failed: {restore_exc}") from exc
            raise


def employee_batch_upsert(vpg, records: Iterable[Mapping[str, Any]], mirror_fn: Callable[[], Any], operation: str = "batch_upsert") -> Any:
    rows = [dict(r or {}) for r in (records or [])]
    if not is_active(vpg):
        return mirror_fn()
    if not rows:
        return mirror_fn()

    usernames = []
    for raw in rows:
        username = str(raw.get("Tên nhân viên") or raw.get("username") or "").strip()
        if username and username not in usernames:
            usernames.append(username)

    with _dataset_lock(vpg, EMPLOYEE_DATASET):
        before = {u: _get_employee(vpg, u) for u in usernames}
        try:
            for raw in rows:
                vpg.upsert_employee_pg(raw)
            _event(vpg, EMPLOYEE_DATASET, "phase4_pg_primary_batch", f"{operation}; rows={len(rows)}")
            result = _call_mirror(mirror_fn)
            _event(vpg, EMPLOYEE_DATASET, "phase4_sheet_mirror_ok", f"{operation}; rows={len(rows)}")
            return result
        except Exception as exc:
            restore_errors = []
            for u in usernames:
                try:
                    _restore_employee(vpg, before.get(u), u)
                except Exception as restore_exc:
                    restore_errors.append(f"{u}: {restore_exc}")
            if restore_errors:
                _event(vpg, EMPLOYEE_DATASET, "phase4_compensation_error", "; ".join(restore_errors)[:1200])
                raise Phase4MirrorError("Google Sheets mirror failed and some PostgreSQL employee rows could not be restored: " + " | ".join(restore_errors[:5])) from exc
            _event(vpg, EMPLOYEE_DATASET, "phase4_compensated", f"{operation}; rows={len(rows)}")
            raise


def employee_delete(vpg, usernames: Iterable[str], mirror_fn: Callable[[], Any], operation: str = "delete") -> Any:
    names = []
    for value in usernames or []:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    if not is_active(vpg):
        return mirror_fn()
    if not names:
        return mirror_fn()

    with _dataset_lock(vpg, EMPLOYEE_DATASET):
        before = {u: _get_employee(vpg, u) for u in names}
        try:
            for u in names:
                vpg.delete_employee_pg(u)
            _event(vpg, EMPLOYEE_DATASET, "phase4_pg_primary_delete", f"rows={len(names)}; employees={','.join(names)[:800]}")
            result = _call_mirror(mirror_fn)
            _event(vpg, EMPLOYEE_DATASET, "phase4_sheet_mirror_ok", f"delete; rows={len(names)}")
            return result
        except Exception as exc:
            restore_errors = []
            for u in names:
                before_row = before.get(u)
                if not before_row:
                    continue
                try:
                    vpg.upsert_employee_pg(before_row)
                except Exception as restore_exc:
                    restore_errors.append(f"{u}: {restore_exc}")
            if restore_errors:
                _event(vpg, EMPLOYEE_DATASET, "phase4_compensation_error", "; ".join(restore_errors)[:1200])
                raise Phase4MirrorError("Google Sheets delete mirror failed and PostgreSQL compensation was incomplete: " + " | ".join(restore_errors[:5])) from exc
            _event(vpg, EMPLOYEE_DATASET, "phase4_compensated", f"delete; rows={len(names)}")
            raise


def _leave_identity(raw: Mapping[str, Any]) -> tuple[str, int]:
    source_id = str(raw.get("__source_sheet_id") or raw.get("source_sheet_id") or "leave_primary").strip()
    source_row = raw.get("__source_row", raw.get("source_row", 0))
    try:
        source_row = int(float(source_row or 0))
    except Exception:
        source_row = 0
    if not source_id or not source_row:
        raise ValueError("Phase 4 leave write requires source sheet id and source row")
    return source_id, source_row


def _get_leave(vpg, source_id: str, source_row: int) -> Optional[dict]:
    engine = vpg.get_engine()
    ensure = getattr(vpg, "ensure_phase3_schema", None)
    if callable(ensure):
        try:
            ensure()
        except Exception:
            pass
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM leave_records WHERE source_sheet_id=:s AND source_row=:r"), {"s": str(source_id), "r": int(source_row)}).mappings().first()
    return dict(row) if row else None


def _restore_leave(vpg, before: Optional[Mapping[str, Any]], source_id: str, source_row: int) -> None:
    if before:
        vpg.upsert_leave_record_pg(dict(before))
    else:
        vpg.delete_leave_record_pg(source_id, source_row)


def leave_upsert(vpg, record: Mapping[str, Any], mirror_fn: Callable[[], Any], operation: str = "upsert") -> Any:
    if not is_active(vpg):
        return mirror_fn()
    raw = dict(record or {})
    source_id, source_row = _leave_identity(raw)
    raw.setdefault("__source_sheet_id", source_id)
    raw.setdefault("__source_row", source_row)

    with _dataset_lock(vpg, LEAVE_DATASET):
        before = _get_leave(vpg, source_id, source_row)
        try:
            vpg.upsert_leave_record_pg(raw)
            _event(vpg, LEAVE_DATASET, "phase4_pg_primary_write", f"{operation}; source={source_id}:{source_row}")
            result = _call_mirror(mirror_fn)
            _event(vpg, LEAVE_DATASET, "phase4_sheet_mirror_ok", f"{operation}; source={source_id}:{source_row}")
            return result
        except Exception as exc:
            try:
                _restore_leave(vpg, before, source_id, source_row)
                _event(vpg, LEAVE_DATASET, "phase4_compensated", f"{operation}; source={source_id}:{source_row}")
            except Exception as restore_exc:
                _event(vpg, LEAVE_DATASET, "phase4_compensation_error", f"{operation}; source={source_id}:{source_row}; mirror={str(exc)[:350]}; restore={str(restore_exc)[:350]}")
                raise Phase4MirrorError(f"Google Sheets mirror failed and PostgreSQL leave compensation also failed: {restore_exc}") from exc
            raise


def leave_batch_upsert(vpg, records: Iterable[Mapping[str, Any]], mirror_fn: Callable[[], Any], operation: str = "batch_upsert") -> Any:
    rows = [dict(r or {}) for r in (records or [])]
    if not is_active(vpg):
        return mirror_fn()
    if not rows:
        return mirror_fn()

    identities = []
    for raw in rows:
        source_id, source_row = _leave_identity(raw)
        raw.setdefault("__source_sheet_id", source_id)
        raw.setdefault("__source_row", source_row)
        identities.append((source_id, source_row))

    with _dataset_lock(vpg, LEAVE_DATASET):
        before = {(s, r): _get_leave(vpg, s, r) for s, r in identities}
        try:
            for raw in rows:
                vpg.upsert_leave_record_pg(raw)
            _event(vpg, LEAVE_DATASET, "phase4_pg_primary_batch", f"{operation}; rows={len(rows)}")
            result = _call_mirror(mirror_fn)
            _event(vpg, LEAVE_DATASET, "phase4_sheet_mirror_ok", f"{operation}; rows={len(rows)}")
            return result
        except Exception as exc:
            restore_errors = []
            for s, r in identities:
                try:
                    _restore_leave(vpg, before.get((s, r)), s, r)
                except Exception as restore_exc:
                    restore_errors.append(f"{s}:{r}: {restore_exc}")
            if restore_errors:
                _event(vpg, LEAVE_DATASET, "phase4_compensation_error", "; ".join(restore_errors)[:1200])
                raise Phase4MirrorError("Google Sheets batch mirror failed and PostgreSQL leave compensation was incomplete: " + " | ".join(restore_errors[:5])) from exc
            _event(vpg, LEAVE_DATASET, "phase4_compensated", f"{operation}; rows={len(rows)}")
            raise


def leave_delete(vpg, records: Iterable[Mapping[str, Any]], mirror_fn: Callable[[], Any], operation: str = "delete") -> Any:
    rows = [dict(r or {}) for r in (records or []) if r is not None]
    if not is_active(vpg):
        return mirror_fn()
    identities = []
    for raw in rows:
        try:
            identity = _leave_identity(raw)
        except Exception:
            continue
        if identity not in identities:
            identities.append(identity)
    if not identities:
        return mirror_fn()

    with _dataset_lock(vpg, LEAVE_DATASET):
        before = {(s, r): _get_leave(vpg, s, r) for s, r in identities}
        try:
            for s, r in identities:
                vpg.delete_leave_record_pg(s, r)
            _event(vpg, LEAVE_DATASET, "phase4_pg_primary_delete", f"rows={len(identities)}")
            result = _call_mirror(mirror_fn)
            _event(vpg, LEAVE_DATASET, "phase4_sheet_mirror_ok", f"delete; rows={len(identities)}")
            return result
        except Exception as exc:
            restore_errors = []
            for s, r in identities:
                before_row = before.get((s, r))
                if not before_row:
                    continue
                try:
                    vpg.upsert_leave_record_pg(before_row)
                except Exception as restore_exc:
                    restore_errors.append(f"{s}:{r}: {restore_exc}")
            if restore_errors:
                _event(vpg, LEAVE_DATASET, "phase4_compensation_error", "; ".join(restore_errors)[:1200])
                raise Phase4MirrorError("Google Sheets delete mirror failed and PostgreSQL leave compensation was incomplete: " + " | ".join(restore_errors[:5])) from exc
            _event(vpg, LEAVE_DATASET, "phase4_compensated", f"delete; rows={len(identities)}")
            raise


def reconcile_dataset(vpg, dataset_key: str, source_loader: Callable[[], pd.DataFrame]):
    """Optional immediate full reconcile after row shifts/renumbering."""
    if not is_active(vpg) or dataset_key not in TARGET_DATASETS:
        return None
    try:
        fresh = source_loader()
        if fresh is None:
            fresh = pd.DataFrame()
        sync_fn = getattr(vpg, "phase3_sync_dataset", None)
        out = sync_fn(dataset_key, fresh) if callable(sync_fn) else None
        primary_write = getattr(vpg, "write_primary_dataset", None)
        if callable(primary_write):
            try:
                primary_write(dataset_key, fresh, source_version="phase4", source_system="google_sheets_mirror")
            except Exception:
                pass
        _event(vpg, dataset_key, "phase4_reconcile", f"rows={len(fresh) if isinstance(fresh, pd.DataFrame) else 0}")
        return out
    except Exception as exc:
        _event(vpg, dataset_key, "phase4_reconcile_error", f"{type(exc).__name__}: {str(exc)[:800]}")
        return None


def get_status(vpg) -> dict:
    return {
        "enabled": bool(is_active(vpg)),
        "write_backend": write_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE4_SCHEMA_VERSION,
    }


def install(vpg) -> bool:
    """Install Phase-4 write helpers after Phase 3."""
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase4_installed", False):
        return True
    required = (
        "upsert_employee_pg", "delete_employee_pg", "get_employee_pg",
        "upsert_leave_record_pg", "delete_leave_record_pg",
    )
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    if _enabled(vpg):
        try:
            _ensure_phase4_schema(vpg)
        except Exception:
            pass

    vpg.phase4_is_enabled = lambda: is_active(vpg)
    vpg.phase4_write_backend = lambda: write_backend(vpg)
    vpg.phase4_employee_upsert = lambda record, mirror_fn, operation="upsert": employee_upsert(vpg, record, mirror_fn, operation=operation)
    vpg.phase4_employee_batch_upsert = lambda records, mirror_fn, operation="batch_upsert": employee_batch_upsert(vpg, records, mirror_fn, operation=operation)
    vpg.phase4_employee_delete = lambda usernames, mirror_fn, operation="delete": employee_delete(vpg, usernames, mirror_fn, operation=operation)
    vpg.phase4_leave_upsert = lambda record, mirror_fn, operation="upsert": leave_upsert(vpg, record, mirror_fn, operation=operation)
    vpg.phase4_leave_batch_upsert = lambda records, mirror_fn, operation="batch_upsert": leave_batch_upsert(vpg, records, mirror_fn, operation=operation)
    vpg.phase4_leave_delete = lambda records, mirror_fn, operation="delete": leave_delete(vpg, records, mirror_fn, operation=operation)
    vpg.phase4_reconcile_dataset = lambda dataset_key, source_loader: reconcile_dataset(vpg, dataset_key, source_loader)
    vpg.get_phase4_status = lambda: get_status(vpg)
    vpg.ensure_phase4_schema = lambda: _ensure_phase4_schema(vpg)
    vpg._vera_phase4_installed = True
    return True
