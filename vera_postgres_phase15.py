"""Vera Spa PostgreSQL Phase 15: shift configuration and assignments.

Migrates the actual shift data paths used by the current core:
- CauHinhCaLamViec (shift definitions)
- CauHinhNghiGiuaCa (department break defaults)
- Sheet1 O:P:Q shift assignments, reusing the normalized credentials dataset from Phase 4/5.

Google Sheets remains a synchronous mirror during migration.
Set VERA_PHASE15_SHIFT_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from typing import Any, Callable, Optional

from sqlalchemy import text


PHASE15_SCHEMA_VERSION = 15
SETTING_CATEGORY = "shift"
SHIFT_DEFINITIONS_KEY = "shift_definitions"
SHIFT_BREAK_CONFIG_KEY = "shift_break_config"
SETTING_KEYS = (SHIFT_DEFINITIONS_KEY, SHIFT_BREAK_CONFIG_KEY)


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


def shift_backend(vpg):
    raw = str(os.getenv("VERA_PHASE15_SHIFT_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg):
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and shift_backend(vpg) == "postgres"
        and all(
            callable(getattr(vpg, name, None))
            for name in ("get_setting", "write_setting", "delete_setting")
        )
    )


def _valid_key(key):
    key = str(key or "").strip()
    if key not in SETTING_KEYS:
        raise KeyError(f"Unsupported Phase 15 setting key: {key}")
    return key


def _event(vpg, key, event_type, detail=""):
    try:
        vpg.record_event(
            f"setting:{SETTING_CATEGORY}:{key}",
            str(event_type),
            str(detail or "")[:1800],
        )
    except Exception:
        pass


def _ensure_schema(vpg):
    if not _enabled(vpg):
        return
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with vpg.get_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase15_shift_configuration', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE15_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg, key):
    key = _valid_key(key)
    if not is_active(vpg):
        yield
        return
    conn = vpg.get_engine().connect()
    locked = False
    try:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtext(:k))"),
            {"k": f"vera:phase15:shift:{key}"},
        )
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"),
                    {"k": f"vera:phase15:shift:{key}"},
                )
            except Exception:
                pass
        conn.close()


def _copy(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _failed(result):
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def _source_result(source_loader, default):
    try:
        result = source_loader()
    except Exception as exc:
        return _copy(default), f"{type(exc).__name__}: {exc}"
    if isinstance(result, tuple) and len(result) >= 2:
        return _copy(result[0]), str(result[1] or "")
    return _copy(result), ""


def read_config(vpg, key, source_loader: Callable[[], Any], default: Any):
    key = _valid_key(key)
    if not is_active(vpg):
        return _source_result(source_loader, default)

    try:
        row = vpg.get_setting(SETTING_CATEGORY, key)
        if row is not None:
            _event(vpg, key, "phase15_pg_shift_read")
            return _copy(row.get("value", default)), ""
    except Exception as exc:
        _event(vpg, key, "phase15_pg_shift_read_error", f"{type(exc).__name__}: {exc}")

    value, err = _source_result(source_loader, default)
    if err:
        _event(vpg, key, "phase15_shift_seed_source_error", err)
        return value, err

    try:
        vpg.write_setting(
            SETTING_CATEGORY,
            key,
            value,
            updated_by="phase15-seed",
            source="google_sheets_seed",
        )
        _event(vpg, key, "phase15_shift_seeded")
    except Exception as exc:
        _event(vpg, key, "phase15_shift_seed_error", f"{type(exc).__name__}: {exc}")
    return _copy(value), ""


def _restore(vpg, key, before: Optional[dict]):
    key = _valid_key(key)
    if before is None:
        vpg.delete_setting(
            SETTING_CATEGORY,
            key,
            updated_by="phase15-compensation",
        )
    else:
        vpg.write_setting(
            SETTING_CATEGORY,
            key,
            before.get("value"),
            updated_by=str(before.get("updated_by") or "phase15-compensation"),
            source="phase15_compensation",
        )


def commit_config(
    vpg,
    key,
    value: Any,
    mirror_fn: Callable[[], Any],
    updated_by="",
    confirm_fn: Optional[Callable[[], Any]] = None,
):
    key = _valid_key(key)
    if not is_active(vpg):
        return mirror_fn()

    with _lock(vpg, key):
        try:
            before = vpg.get_setting(SETTING_CATEGORY, key)
        except Exception:
            before = None

        written = False
        try:
            vpg.write_setting(
                SETTING_CATEGORY,
                key,
                value,
                updated_by=str(updated_by or ""),
                source="postgres_primary",
            )
            written = True
            _event(vpg, key, "phase15_pg_shift_write")

            result = mirror_fn()
            if _failed(result):
                _restore(vpg, key, before)
                _event(vpg, key, "phase15_shift_compensated", "mirror_returned_failure")
                return result

            confirmed = value
            if callable(confirm_fn):
                confirmed_value, confirm_err = _source_result(confirm_fn, value)
                if confirm_err:
                    _event(vpg, key, "phase15_shift_confirm_warning", confirm_err)
                else:
                    confirmed = confirmed_value

            vpg.write_setting(
                SETTING_CATEGORY,
                key,
                confirmed,
                updated_by=str(updated_by or ""),
                source="postgres_primary_confirmed",
            )
            _event(vpg, key, "phase15_sheet_mirror_ok")
            return result
        except Exception as exc:
            if written:
                try:
                    _restore(vpg, key, before)
                    _event(vpg, key, "phase15_shift_compensated", type(exc).__name__)
                except Exception:
                    pass
            raise


def employee_upsert(vpg, record, mirror_fn, operation="shift_upsert"):
    if not is_active(vpg):
        return mirror_fn()
    fn = getattr(vpg, "phase4_employee_upsert", None)
    if not callable(fn):
        return mirror_fn()
    return fn(record, mirror_fn=mirror_fn, operation=operation)


def employee_batch_upsert(vpg, records, mirror_fn, operation="shift_batch_upsert"):
    rows = [dict(r or {}) for r in (records or [])]
    if not is_active(vpg):
        return mirror_fn()
    fn = getattr(vpg, "phase4_employee_batch_upsert", None)
    if not callable(fn):
        return mirror_fn()
    return fn(rows, mirror_fn=mirror_fn, operation=operation)


def get_status(vpg):
    settings = {}
    for key in SETTING_KEYS:
        row = None
        if _enabled(vpg):
            try:
                row = vpg.get_setting(SETTING_CATEGORY, key)
            except Exception:
                row = None
        settings[key] = {
            "exists": row is not None,
            "revision": (row or {}).get("revision"),
            "source": (row or {}).get("source"),
            "updated_at": (row or {}).get("updated_at"),
        }
    return {
        "enabled": bool(is_active(vpg)),
        "shift_backend": shift_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE15_SCHEMA_VERSION,
        "settings": settings,
        "assignment_backend": (
            "phase4_credentials"
            if callable(getattr(vpg, "phase4_employee_batch_upsert", None))
            else "sheets"
        ),
    }


def install(vpg):
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase15_installed", False):
        return True
    if not all(
        callable(getattr(vpg, name, None))
        for name in ("get_setting", "write_setting", "delete_setting")
    ):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(
                vpg,
                SHIFT_DEFINITIONS_KEY,
                "phase15_schema_warning",
                f"{type(exc).__name__}: {exc}",
            )

    vpg.phase15_is_enabled = lambda: is_active(vpg)
    vpg.phase15_shift_backend = lambda: shift_backend(vpg)
    vpg.phase15_read_config = lambda key, source_loader, default=None: read_config(
        vpg, key, source_loader, default
    )
    vpg.phase15_commit_config = (
        lambda key, value, mirror_fn, updated_by="", confirm_fn=None: commit_config(
            vpg,
            key,
            value,
            mirror_fn,
            updated_by=updated_by,
            confirm_fn=confirm_fn,
        )
    )
    vpg.phase15_employee_upsert = (
        lambda record, mirror_fn, operation="shift_upsert": employee_upsert(
            vpg, record, mirror_fn, operation=operation
        )
    )
    vpg.phase15_employee_batch_upsert = (
        lambda records, mirror_fn, operation="shift_batch_upsert": employee_batch_upsert(
            vpg, records, mirror_fn, operation=operation
        )
    )
    vpg.get_phase15_status = lambda: get_status(vpg)
    vpg.ensure_phase15_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase15_installed = True
    return True
