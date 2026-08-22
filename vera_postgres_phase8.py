"""Vera Spa PostgreSQL Phase 8: PostgreSQL-first NoViPham + PayrollHistory writes.

The two datasets already use the Phase-2 durable PostgreSQL store for reads.
Phase 8 makes mutations PostgreSQL-first while keeping Google Sheets as a
synchronous mirror during the transition.

Datasets:
- violation_debt
- payroll_history

Set VERA_PHASE8_WRITE_BACKEND=sheets for immediate rollback.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Any, Callable, Optional

import pandas as pd
from sqlalchemy import text


PHASE8_SCHEMA_VERSION = 8
TARGET_DATASETS = {"violation_debt", "payroll_history"}
PRIMARY_TABLE = "vera_primary_dataset"


class Phase8MirrorError(RuntimeError):
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
    raw = str(os.getenv("VERA_PHASE8_WRITE_BACKEND", "") or "").strip().lower()
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


def _event(vpg, dataset_key: str, event_type: str, detail: str = "") -> None:
    try:
        vpg.record_event(str(dataset_key), str(event_type), str(detail or "")[:1800])
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
                VALUES ('phase8_debt_payroll_primary_writes', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE8_SCHEMA_VERSION},
        )


@contextmanager
def _lock(vpg, dataset_key: str):
    if not is_active(vpg):
        yield
        return
    engine = vpg.get_engine()
    conn = engine.connect()
    locked = False
    key = f"vera:phase8:{dataset_key}"
    try:
        conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": key})
        locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
            except Exception:
                pass
        conn.close()


def _delete_primary(vpg, dataset_key: str) -> None:
    engine = vpg.get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {PRIMARY_TABLE} WHERE dataset_key=:k"), {"k": dataset_key})


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


def commit_dataset(
    vpg,
    dataset_key: str,
    new_df: pd.DataFrame,
    mirror_fn: Callable[[], Any],
    operation: str = "update",
    confirm_fn: Optional[Callable[[], pd.DataFrame]] = None,
):
    if dataset_key not in TARGET_DATASETS or not is_active(vpg):
        return mirror_fn()
    if not isinstance(new_df, pd.DataFrame):
        new_df = pd.DataFrame(new_df if new_df is not None else [])
    new_df = new_df.copy()

    with _lock(vpg, dataset_key):
        before = None
        try:
            before = vpg.read_primary_dataset(dataset_key, allow_stale=True)
        except Exception:
            before = None

        try:
            vpg.write_primary_dataset(
                dataset_key,
                new_df,
                source_version="phase8",
                source_system="postgres_primary",
            )
            _event(vpg, dataset_key, "phase8_pg_primary_write", f"{operation}; rows={len(new_df)}")

            result = mirror_fn()
            if _mirror_failed(result):
                raise Phase8MirrorError(f"Google Sheets mirror returned failure for {dataset_key}:{operation}")

            confirmed = new_df
            if callable(confirm_fn):
                confirmed = _as_frame(confirm_fn(), new_df)
            vpg.write_primary_dataset(
                dataset_key,
                confirmed,
                source_version="phase8_mirror_confirmed",
                source_system="postgres_primary",
            )
            _event(vpg, dataset_key, "phase8_sheet_mirror_ok", f"{operation}; rows={len(confirmed)}")
            return result
        except Exception as exc:
            restore_error = None
            try:
                if isinstance(before, pd.DataFrame):
                    vpg.write_primary_dataset(
                        dataset_key,
                        before,
                        source_version="phase8_compensation",
                        source_system="compensation",
                    )
                else:
                    _delete_primary(vpg, dataset_key)
            except Exception as restore_exc:
                restore_error = restore_exc

            if restore_error is not None:
                _event(vpg, dataset_key, "phase8_compensation_error", f"{type(restore_error).__name__}: {restore_error}")
                raise Phase8MirrorError(
                    f"{dataset_key} mirror failed and PostgreSQL compensation was incomplete: {restore_error}"
                ) from exc

            _event(vpg, dataset_key, "phase8_compensated", f"{operation}; {type(exc).__name__}")
            raise


def get_status(vpg) -> dict:
    return {
        "enabled": bool(is_active(vpg)),
        "write_backend": write_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE8_SCHEMA_VERSION,
        "datasets": sorted(TARGET_DATASETS),
    }


def install(vpg) -> bool:
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase8_installed", False):
        return True
    required = ("read_primary_dataset", "write_primary_dataset")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase8", "phase8_schema_warning", f"{type(exc).__name__}: {exc}")

    vpg.phase8_is_enabled = lambda: is_active(vpg)
    vpg.phase8_write_backend = lambda: write_backend(vpg)
    vpg.phase8_dataset_commit = (
        lambda dataset_key, new_df, mirror_fn, operation="update", confirm_fn=None:
        commit_dataset(vpg, dataset_key, new_df, mirror_fn, operation=operation, confirm_fn=confirm_fn)
    )
    vpg.get_phase8_status = lambda: get_status(vpg)
    vpg.ensure_phase8_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase8_installed = True
    return True
