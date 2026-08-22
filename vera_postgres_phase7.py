"""Vera Spa PostgreSQL Phase 7: PostgreSQL-first TichLuy writes.

TichLuy is stored as a full DataFrame in the Phase-2 durable PostgreSQL dataset.
The core loader keeps canonical business columns plus raw Sheet row/header metadata
so user-added columns are preserved across the cutover. Existing Sheet mutations
remain a synchronous mirror. PostgreSQL is written first and compensated to the
previous snapshot if the mirror fails.

Set VERA_PHASE7_TICHLUY_WRITE_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Any, Callable, Optional

import pandas as pd
from sqlalchemy import text


PHASE7_SCHEMA_VERSION = 7
DATASET_KEY = "tichluy"
PRIMARY_TABLE = "vera_primary_dataset"


class Phase7MirrorError(RuntimeError):
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


def write_backend(vpg) -> str:
    raw = str(os.getenv("VERA_PHASE7_TICHLUY_WRITE_BACKEND", "") or "").strip().lower()
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
        and bool(getattr(vpg, "_vera_phase2_installed", False))
    )


def _event(vpg, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(DATASET_KEY, str(event_type), str(detail or "")[:1800])
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
                VALUES ('phase7_tichluy_primary_writes', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE7_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg):
    if not is_active(vpg):
        yield
        return
    engine = vpg.get_engine()
    conn = engine.connect()
    locked = False
    try:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtext(:k))"),
            {"k": "vera:phase7:tichluy"},
        )
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"),
                    {"k": "vera:phase7:tichluy"},
                )
            except Exception:
                pass
        conn.close()


def _delete_primary(vpg) -> None:
    engine = vpg.get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {PRIMARY_TABLE} WHERE dataset_key=:k"), {"k": DATASET_KEY})


def _mirror_failed(result: Any) -> bool:
    if isinstance(result, bool):
        return result is False
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool):
        return result[0] is False
    return False


def _as_frame(value: Any, fallback: pd.DataFrame) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if value is None:
        return fallback.copy()
    try:
        return pd.DataFrame(value)
    except Exception:
        return fallback.copy()


def commit(
    vpg,
    new_df: pd.DataFrame,
    mirror_fn: Callable[[], Any],
    operation: str = "update",
    confirm_fn: Optional[Callable[[], pd.DataFrame]] = None,
):
    """Write intended TichLuy snapshot to PostgreSQL first, then mirror Sheets.

    After a successful mirror, ``confirm_fn`` may read back the exact Sheet snapshot.
    That final confirmation is useful for physical ``__sheet_row`` values and custom
    columns. If mirror or confirmation fails, the previous PostgreSQL snapshot is
    restored.
    """
    if not is_active(vpg):
        return mirror_fn()

    if not isinstance(new_df, pd.DataFrame):
        new_df = pd.DataFrame(new_df if new_df is not None else [])
    new_df = new_df.copy()

    with _lock(vpg):
        before = None
        try:
            before = vpg.read_primary_dataset(DATASET_KEY, allow_stale=True)
        except Exception:
            before = None

        try:
            vpg.write_primary_dataset(
                DATASET_KEY,
                new_df,
                source_version="phase7",
                source_system="postgres_primary",
            )
            _event(vpg, "phase7_pg_primary_write", f"{operation}; rows={len(new_df)}")

            result = mirror_fn()
            if _mirror_failed(result):
                raise Phase7MirrorError(f"Google Sheets TichLuy mirror returned failure for {operation}")

            confirmed_df = new_df
            if callable(confirm_fn):
                confirmed_df = _as_frame(confirm_fn(), new_df)

            # Mirror may invalidate the durable dataset. Promote the exact confirmed
            # snapshot so normal reads finish current/non-stale.
            vpg.write_primary_dataset(
                DATASET_KEY,
                confirmed_df,
                source_version="phase7_mirror_confirmed",
                source_system="postgres_primary",
            )
            _event(vpg, "phase7_sheet_mirror_ok", f"{operation}; rows={len(confirmed_df)}")
            return result
        except Exception as exc:
            restore_error = None
            try:
                if isinstance(before, pd.DataFrame):
                    vpg.write_primary_dataset(
                        DATASET_KEY,
                        before,
                        source_version="phase7_compensation",
                        source_system="compensation",
                    )
                else:
                    _delete_primary(vpg)
            except Exception as restore_exc:
                restore_error = restore_exc

            if restore_error is not None:
                _event(
                    vpg,
                    "phase7_compensation_error",
                    f"{type(restore_error).__name__}: {restore_error}",
                )
                raise Phase7MirrorError(
                    "TichLuy mirror failed and PostgreSQL compensation was incomplete: "
                    + str(restore_error)
                ) from exc

            _event(vpg, "phase7_compensated", f"{operation}; {type(exc).__name__}")
            raise


def get_status(vpg) -> dict:
    return {
        "enabled": bool(is_active(vpg)),
        "write_backend": write_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE7_SCHEMA_VERSION,
    }


def install(vpg) -> bool:
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase7_installed", False):
        return True
    required = ("read_primary_dataset", "write_primary_dataset")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase7_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase7_tichluy_is_enabled = lambda: is_active(vpg)
    vpg.phase7_tichluy_write_backend = lambda: write_backend(vpg)
    vpg.phase7_tichluy_commit = (
        lambda new_df, mirror_fn, operation="update", confirm_fn=None:
        commit(vpg, new_df, mirror_fn, operation=operation, confirm_fn=confirm_fn)
    )
    vpg.get_phase7_status = lambda: get_status(vpg)
    vpg.ensure_phase7_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase7_installed = True
    return True
