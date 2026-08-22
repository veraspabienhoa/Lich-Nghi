"""Vera Spa PostgreSQL Phase 10: PostgreSQL-primary control settings.

Moves small, high-frequency control-plane settings away from live Google-Sheets reads
while keeping the legacy Sheets functions as synchronous mirrors during transition.

Covered settings:
- registration role locks
- Auto Check RUNNING/PAUSED configuration
- mid-shift return deadline / late threshold

Set VERA_PHASE10_SETTINGS_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from typing import Any, Callable, Optional

from sqlalchemy import text


PHASE10_SCHEMA_VERSION = 10
SETTING_CATEGORY = "control"
TARGET_KEYS = {
    "registration_role_locks",
    "auto_penalty_config",
    "midshift_deadline_config",
}


class Phase10MirrorError(RuntimeError):
    pass


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


def settings_backend(vpg) -> str:
    raw = str(os.getenv("VERA_PHASE10_SETTINGS_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg) -> bool:
    required = ("get_setting", "write_setting", "delete_setting")
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and settings_backend(vpg) == "postgres"
        and all(callable(getattr(vpg, name, None)) for name in required)
    )


def _event(vpg, key: str, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(f"setting:{SETTING_CATEGORY}:{key}", event_type, str(detail or "")[:1800])
    except Exception:
        pass


def _ensure_schema(vpg) -> None:
    if not _enabled(vpg):
        return
    engine = vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase10_control_settings', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE10_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg, setting_key: str):
    if not is_active(vpg):
        yield
        return
    engine = vpg.get_engine()
    conn = engine.connect()
    locked = False
    lock_key = f"vera:phase10:{SETTING_CATEGORY}:{setting_key}"
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
        conn.close()


def _copy(value: Any):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _mirror_failed(result: Any) -> bool:
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def read_setting_primary(
    vpg,
    setting_key: str,
    source_loader: Callable[[], Any],
    default: Any = None,
):
    """Read PostgreSQL first; seed once from the legacy source when missing."""
    if setting_key not in TARGET_KEYS or not is_active(vpg):
        try:
            return source_loader()
        except Exception:
            return _copy(default)

    try:
        row = vpg.get_setting(SETTING_CATEGORY, setting_key)
        if row is not None:
            value = row.get("value", default)
            _event(vpg, setting_key, "phase10_pg_setting_read")
            return _copy(value)
    except Exception as exc:
        _event(vpg, setting_key, "phase10_pg_setting_read_error", f"{type(exc).__name__}: {exc}")

    try:
        value = source_loader()
    except Exception as exc:
        _event(vpg, setting_key, "phase10_seed_source_error", f"{type(exc).__name__}: {exc}")
        return _copy(default)

    try:
        vpg.write_setting(
            SETTING_CATEGORY,
            setting_key,
            value,
            updated_by="phase10-seed",
            source="google_sheets_seed",
        )
        _event(vpg, setting_key, "phase10_setting_seeded")
    except Exception as exc:
        _event(vpg, setting_key, "phase10_setting_seed_error", f"{type(exc).__name__}: {exc}")
    return _copy(value)


def _restore(vpg, setting_key: str, before: Optional[dict]) -> None:
    if before is None:
        vpg.delete_setting(SETTING_CATEGORY, setting_key, updated_by="phase10-compensation")
        return
    vpg.write_setting(
        SETTING_CATEGORY,
        setting_key,
        before.get("value"),
        updated_by=str(before.get("updated_by") or "phase10-compensation"),
        source="phase10_compensation",
    )


def commit_setting(
    vpg,
    setting_key: str,
    value: Any,
    mirror_fn: Callable[[], Any],
    updated_by: str = "",
    operation: str = "update",
    confirm_fn: Optional[Callable[[], Any]] = None,
):
    """Write PostgreSQL first, mirror Sheets, then confirm; compensate on mirror failure."""
    if setting_key not in TARGET_KEYS or not is_active(vpg):
        return mirror_fn()

    with _lock(vpg, setting_key):
        before = None
        try:
            before = vpg.get_setting(SETTING_CATEGORY, setting_key)
        except Exception:
            before = None

        pg_written = False
        try:
            vpg.write_setting(
                SETTING_CATEGORY,
                setting_key,
                value,
                updated_by=str(updated_by or ""),
                source="postgres_primary",
            )
            pg_written = True
            _event(vpg, setting_key, "phase10_pg_setting_write", operation)

            result = mirror_fn()
            if _mirror_failed(result):
                if pg_written:
                    _restore(vpg, setting_key, before)
                _event(vpg, setting_key, "phase10_setting_compensated", f"{operation}; mirror_returned_failure")
                return result

            confirmed = value
            if callable(confirm_fn):
                try:
                    confirmed = confirm_fn()
                except Exception as exc:
                    _event(vpg, setting_key, "phase10_confirm_warning", f"{type(exc).__name__}: {exc}")
                    confirmed = value

            vpg.write_setting(
                SETTING_CATEGORY,
                setting_key,
                confirmed,
                updated_by=str(updated_by or ""),
                source="postgres_primary_confirmed",
            )
            _event(vpg, setting_key, "phase10_sheet_mirror_ok", operation)
            return result
        except Exception as exc:
            if pg_written:
                try:
                    _restore(vpg, setting_key, before)
                    _event(vpg, setting_key, "phase10_setting_compensated", f"{operation}; {type(exc).__name__}")
                except Exception as restore_exc:
                    _event(
                        vpg,
                        setting_key,
                        "phase10_compensation_error",
                        f"{type(restore_exc).__name__}: {restore_exc}",
                    )
                    raise Phase10MirrorError(
                        f"{setting_key} mirror failed and PostgreSQL compensation was incomplete: {restore_exc}"
                    ) from exc
            raise


def get_status(vpg) -> dict:
    rows = {}
    if _enabled(vpg):
        for key in sorted(TARGET_KEYS):
            try:
                row = vpg.get_setting(SETTING_CATEGORY, key)
                rows[key] = {
                    "exists": row is not None,
                    "revision": (row or {}).get("revision"),
                    "source": (row or {}).get("source"),
                    "updated_at": (row or {}).get("updated_at"),
                }
            except Exception as exc:
                rows[key] = {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "enabled": bool(is_active(vpg)),
        "settings_backend": settings_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE10_SCHEMA_VERSION,
        "settings": rows,
    }


def install(vpg) -> bool:
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase10_installed", False):
        return True

    required = ("get_setting", "write_setting", "delete_setting")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase10", "phase10_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase10_is_enabled = lambda: is_active(vpg)
    vpg.phase10_settings_backend = lambda: settings_backend(vpg)
    vpg.phase10_read_setting = (
        lambda setting_key, source_loader, default=None:
        read_setting_primary(vpg, setting_key, source_loader, default=default)
    )
    vpg.phase10_commit_setting = (
        lambda setting_key, value, mirror_fn, updated_by="", operation="update", confirm_fn=None:
        commit_setting(
            vpg,
            setting_key,
            value,
            mirror_fn,
            updated_by=updated_by,
            operation=operation,
            confirm_fn=confirm_fn,
        )
    )
    vpg.get_phase10_status = lambda: get_status(vpg)
    vpg.ensure_phase10_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase10_installed = True
    return True
