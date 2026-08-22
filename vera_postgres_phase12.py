"""Vera Spa PostgreSQL Phase 12: UI theme PostgreSQL-primary.

Moves the global Desktop/Mobile theme configuration to PostgreSQL-primary while
retaining the legacy Google Sheet as a synchronous mirror.

Set VERA_PHASE12_UI_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from typing import Any, Callable, Optional

from sqlalchemy import text


PHASE12_SCHEMA_VERSION = 12
SETTING_CATEGORY = "ui"
SETTING_KEY = "theme_config"


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


def ui_backend(vpg):
    raw = str(os.getenv("VERA_PHASE12_UI_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and _mode(vpg) in {"dual", "postgres"} else "sheets"


def is_active(vpg):
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and ui_backend(vpg) == "postgres"
        and all(callable(getattr(vpg, x, None)) for x in ("get_setting", "write_setting", "delete_setting"))
    )


def _event(vpg, event_type, detail=""):
    try:
        vpg.record_event(
            f"setting:{SETTING_CATEGORY}:{SETTING_KEY}",
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
                VALUES ('phase12_ui_theme', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE12_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg):
    if not is_active(vpg):
        yield
        return
    conn = vpg.get_engine().connect()
    locked = False
    try:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtext(:k))"),
            {"k": "vera:phase12:ui:theme_config"},
        )
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"),
                    {"k": "vera:phase12:ui:theme_config"},
                )
            except Exception:
                pass
        conn.close()


def _copy(v):
    try:
        return copy.deepcopy(v)
    except Exception:
        return v


def _failed(result):
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def read_theme(vpg, source_loader: Callable[[], Any], default: Any):
    if not is_active(vpg):
        try:
            return source_loader()
        except Exception:
            return _copy(default)

    try:
        row = vpg.get_setting(SETTING_CATEGORY, SETTING_KEY)
        if row is not None:
            _event(vpg, "phase12_pg_theme_read")
            return _copy(row.get("value", default))
    except Exception as exc:
        _event(vpg, "phase12_pg_theme_read_error", f"{type(exc).__name__}: {exc}")

    try:
        value = source_loader()
    except Exception as exc:
        _event(vpg, "phase12_theme_seed_source_error", f"{type(exc).__name__}: {exc}")
        return _copy(default)

    try:
        vpg.write_setting(
            SETTING_CATEGORY, SETTING_KEY, value,
            updated_by="phase12-seed", source="google_sheets_seed",
        )
        _event(vpg, "phase12_theme_seeded")
    except Exception as exc:
        _event(vpg, "phase12_theme_seed_error", f"{type(exc).__name__}: {exc}")
    return _copy(value)


def _restore(vpg, before: Optional[dict]):
    if before is None:
        vpg.delete_setting(
            SETTING_CATEGORY, SETTING_KEY,
            updated_by="phase12-compensation",
        )
    else:
        vpg.write_setting(
            SETTING_CATEGORY, SETTING_KEY, before.get("value"),
            updated_by=str(before.get("updated_by") or "phase12-compensation"),
            source="phase12_compensation",
        )


def commit_theme(
    vpg,
    value: Any,
    mirror_fn: Callable[[], Any],
    updated_by="",
    confirm_fn: Optional[Callable[[], Any]] = None,
):
    if not is_active(vpg):
        return mirror_fn()

    with _lock(vpg):
        try:
            before = vpg.get_setting(SETTING_CATEGORY, SETTING_KEY)
        except Exception:
            before = None

        written = False
        try:
            vpg.write_setting(
                SETTING_CATEGORY, SETTING_KEY, value,
                updated_by=str(updated_by or ""), source="postgres_primary",
            )
            written = True
            _event(vpg, "phase12_pg_theme_write")

            result = mirror_fn()
            if _failed(result):
                _restore(vpg, before)
                _event(vpg, "phase12_theme_compensated", "mirror_returned_failure")
                return result

            confirmed = value
            if callable(confirm_fn):
                try:
                    confirmed = confirm_fn()
                except Exception as exc:
                    _event(vpg, "phase12_theme_confirm_warning", f"{type(exc).__name__}: {exc}")

            vpg.write_setting(
                SETTING_CATEGORY, SETTING_KEY, confirmed,
                updated_by=str(updated_by or ""), source="postgres_primary_confirmed",
            )
            _event(vpg, "phase12_sheet_mirror_ok")
            return result
        except Exception as exc:
            if written:
                try:
                    _restore(vpg, before)
                    _event(vpg, "phase12_theme_compensated", type(exc).__name__)
                except Exception:
                    pass
            raise


def get_status(vpg):
    row = None
    if _enabled(vpg):
        try:
            row = vpg.get_setting(SETTING_CATEGORY, SETTING_KEY)
        except Exception:
            row = None
    return {
        "enabled": bool(is_active(vpg)),
        "ui_backend": ui_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE12_SCHEMA_VERSION,
        "exists": row is not None,
        "revision": (row or {}).get("revision"),
        "source": (row or {}).get("source"),
        "updated_at": (row or {}).get("updated_at"),
    }


def install(vpg):
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase12_installed", False):
        return True
    if not all(callable(getattr(vpg, x, None)) for x in ("get_setting", "write_setting", "delete_setting")):
        return False
    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase12_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase12_is_enabled = lambda: is_active(vpg)
    vpg.phase12_ui_backend = lambda: ui_backend(vpg)
    vpg.phase12_read_theme = lambda source_loader, default=None: read_theme(vpg, source_loader, default)
    vpg.phase12_commit_theme = lambda value, mirror_fn, updated_by="", confirm_fn=None: commit_theme(
        vpg, value, mirror_fn, updated_by=updated_by, confirm_fn=confirm_fn
    )
    vpg.get_phase12_status = lambda: get_status(vpg)
    vpg.ensure_phase12_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase12_installed = True
    return True
