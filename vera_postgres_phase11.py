"""Vera Spa PostgreSQL Phase 11: authorization settings PostgreSQL-primary.

Moves two security-sensitive configuration datasets from Google-Sheets-primary to
PostgreSQL-primary while retaining synchronous Sheets mirrors for transition:

- feature_permissions
- shared_input_grants

Set VERA_PHASE11_AUTH_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from typing import Any, Callable, Optional

from sqlalchemy import text


PHASE11_SCHEMA_VERSION = 11
SETTING_CATEGORY = "authorization"
TARGET_KEYS = {"feature_permissions", "shared_input_grants"}


class Phase11MirrorError(RuntimeError):
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


def auth_backend(vpg) -> str:
    raw = str(os.getenv("VERA_PHASE11_AUTH_BACKEND", "") or "").strip().lower()
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
        and auth_backend(vpg) == "postgres"
        and all(callable(getattr(vpg, name, None)) for name in required)
    )


def _event(vpg, key: str, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(
            f"setting:{SETTING_CATEGORY}:{key}",
            str(event_type),
            str(detail or "")[:1800],
        )
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
                VALUES ('phase11_authorization_settings', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE11_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg, setting_key: str):
    if not is_active(vpg):
        yield
        return
    conn = vpg.get_engine().connect()
    locked = False
    lock_key = f"vera:phase11:{SETTING_CATEGORY}:{setting_key}"
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


def read_auth_setting(
    vpg,
    setting_key: str,
    source_loader: Callable[[], Any],
    default: Any = None,
):
    if setting_key not in TARGET_KEYS or not is_active(vpg):
        try:
            return source_loader()
        except Exception:
            return _copy(default)

    try:
        row = vpg.get_setting(SETTING_CATEGORY, setting_key)
        if row is not None:
            _event(vpg, setting_key, "phase11_pg_auth_read")
            return _copy(row.get("value", default))
    except Exception as exc:
        _event(vpg, setting_key, "phase11_pg_auth_read_error", f"{type(exc).__name__}: {exc}")

    try:
        value = source_loader()
    except Exception as exc:
        _event(vpg, setting_key, "phase11_seed_source_error", f"{type(exc).__name__}: {exc}")
        return _copy(default)

    try:
        vpg.write_setting(
            SETTING_CATEGORY,
            setting_key,
            value,
            updated_by="phase11-seed",
            source="google_sheets_seed",
        )
        _event(vpg, setting_key, "phase11_auth_seeded")
    except Exception as exc:
        _event(vpg, setting_key, "phase11_auth_seed_error", f"{type(exc).__name__}: {exc}")
    return _copy(value)


def _restore(vpg, setting_key: str, before: Optional[dict]) -> None:
    if before is None:
        vpg.delete_setting(
            SETTING_CATEGORY,
            setting_key,
            updated_by="phase11-compensation",
        )
        return
    vpg.write_setting(
        SETTING_CATEGORY,
        setting_key,
        before.get("value"),
        updated_by=str(before.get("updated_by") or "phase11-compensation"),
        source="phase11_compensation",
    )


def commit_auth_setting(
    vpg,
    setting_key: str,
    value: Any,
    mirror_fn: Callable[[], Any],
    updated_by: str = "",
    operation: str = "update",
    confirm_fn: Optional[Callable[[], Any]] = None,
):
    if setting_key not in TARGET_KEYS or not is_active(vpg):
        return mirror_fn()

    with _lock(vpg, setting_key):
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
            _event(vpg, setting_key, "phase11_pg_auth_write", operation)

            result = mirror_fn()
            if _mirror_failed(result):
                _restore(vpg, setting_key, before)
                _event(vpg, setting_key, "phase11_auth_compensated", f"{operation}; mirror_returned_failure")
                return result

            confirmed = value
            if callable(confirm_fn):
                try:
                    confirmed = confirm_fn()
                except Exception as exc:
                    _event(vpg, setting_key, "phase11_confirm_warning", f"{type(exc).__name__}: {exc}")
                    confirmed = value

            vpg.write_setting(
                SETTING_CATEGORY,
                setting_key,
                confirmed,
                updated_by=str(updated_by or ""),
                source="postgres_primary_confirmed",
            )
            _event(vpg, setting_key, "phase11_sheet_mirror_ok", operation)
            return result
        except Exception as exc:
            if pg_written:
                try:
                    _restore(vpg, setting_key, before)
                    _event(vpg, setting_key, "phase11_auth_compensated", f"{operation}; {type(exc).__name__}")
                except Exception as restore_exc:
                    _event(vpg, setting_key, "phase11_compensation_error",
                           f"{type(restore_exc).__name__}: {restore_exc}")
                    raise Phase11MirrorError(
                        f"{setting_key} mirror failed and PostgreSQL compensation was incomplete: {restore_exc}"
                    ) from exc
            raise


def get_status(vpg) -> dict:
    settings = {}
    if _enabled(vpg):
        for key in sorted(TARGET_KEYS):
            try:
                row = vpg.get_setting(SETTING_CATEGORY, key)
                settings[key] = {
                    "exists": row is not None,
                    "revision": (row or {}).get("revision"),
                    "source": (row or {}).get("source"),
                    "updated_at": (row or {}).get("updated_at"),
                }
            except Exception as exc:
                settings[key] = {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "enabled": bool(is_active(vpg)),
        "auth_backend": auth_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE11_SCHEMA_VERSION,
        "settings": settings,
    }


def install(vpg) -> bool:
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase11_installed", False):
        return True

    required = ("get_setting", "write_setting", "delete_setting")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase11", "phase11_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase11_is_enabled = lambda: is_active(vpg)
    vpg.phase11_auth_backend = lambda: auth_backend(vpg)
    vpg.phase11_read_auth_setting = (
        lambda setting_key, source_loader, default=None:
        read_auth_setting(vpg, setting_key, source_loader, default=default)
    )
    vpg.phase11_commit_auth_setting = (
        lambda setting_key, value, mirror_fn, updated_by="", operation="update", confirm_fn=None:
        commit_auth_setting(
            vpg,
            setting_key,
            value,
            mirror_fn,
            updated_by=updated_by,
            operation=operation,
            confirm_fn=confirm_fn,
        )
    )
    vpg.get_phase11_status = lambda: get_status(vpg)
    vpg.ensure_phase11_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase11_installed = True
    return True
