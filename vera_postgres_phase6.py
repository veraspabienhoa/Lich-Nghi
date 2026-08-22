"""Vera Spa PostgreSQL Phase 6: PostgreSQL-primary reads for remaining routed datasets.

Phase 5 moved credentials and leave_primary to normalized PostgreSQL reads.
Phase 6 moves the three other high-volume datasets already routed through the
Phase-2 durable store:

- tichluy
- violation_debt
- payroll_history

These datasets still use the existing Google Sheets mutation code during this
phase. Existing invalidation marks the durable PostgreSQL snapshot stale; the
next read performs one source reconciliation, then normal reads return to
PostgreSQL. A later phase will move their writes to PostgreSQL-first as well.

Set VERA_PHASE6_READ_BACKEND=sheets for immediate rollback of these reads.
"""
from __future__ import annotations

import os
import pandas as pd
from sqlalchemy import text


PHASE6_SCHEMA_VERSION = 6
TARGET_DATASETS = {"tichluy", "violation_debt", "payroll_history"}


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


def read_backend(vpg):
    raw = str(os.getenv("VERA_PHASE6_READ_BACKEND", "") or "").strip().lower()
    if raw in {"sheets", "google", "google_sheets", "legacy"}:
        return "sheets"
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "postgres" if _enabled(vpg) and bool(getattr(vpg, "_vera_phase2_installed", False)) else "sheets"


def is_active(vpg):
    return (
        _enabled(vpg)
        and _mode(vpg) in {"dual", "postgres"}
        and read_backend(vpg) == "postgres"
        and bool(getattr(vpg, "_vera_phase2_installed", False))
    )


def _event(vpg, dataset_key, event_type, detail=""):
    try:
        vpg.record_event(str(dataset_key), str(event_type), str(detail or "")[:1800])
    except Exception:
        pass


def _ensure_schema(vpg):
    if not _enabled(vpg):
        return
    engine = vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {version_table}(component, version, updated_at)
                VALUES ('phase6_remaining_primary_reads', :version, NOW())
                ON CONFLICT (component) DO UPDATE
                SET version=GREATEST({version_table}.version, EXCLUDED.version),
                    updated_at=NOW()
                """
            ),
            {"version": PHASE6_SCHEMA_VERSION},
        )


def _primary_state(vpg, dataset_key):
    status_fn = getattr(vpg, "get_primary_status", None)
    if not callable(status_fn):
        return None
    status = status_fn()
    if not isinstance(status, pd.DataFrame) or status.empty or "dataset_key" not in status.columns:
        return None
    hit = status[status["dataset_key"].astype(str).eq(str(dataset_key))]
    if hit.empty:
        return None
    return hit.iloc[-1].to_dict()


def _read_current(vpg, dataset_key):
    state = _primary_state(vpg, dataset_key)
    if not state:
        return None, "primary_state_missing"
    if bool(state.get("is_stale")):
        return None, "primary_state_stale"
    reader = getattr(vpg, "read_primary_dataset", None)
    if not callable(reader):
        return None, "primary_reader_missing"
    df = reader(dataset_key, allow_stale=False)
    if df is None:
        return None, "primary_payload_missing"
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    try:
        expected = int(state.get("row_count") or 0)
    except Exception:
        expected = len(df)
    if expected != len(df):
        return None, f"row_count_mismatch:{expected}!={len(df)}"
    return df, "current"


def get_status(vpg):
    states = {}
    for key in sorted(TARGET_DATASETS):
        try:
            states[key] = _primary_state(vpg, key)
        except Exception as exc:
            states[key] = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "enabled": bool(is_active(vpg)),
        "read_backend": read_backend(vpg),
        "data_backend": _mode(vpg),
        "schema_version": PHASE6_SCHEMA_VERSION,
        "datasets": states,
    }


def install(vpg):
    if vpg is None:
        return False
    if getattr(vpg, "_vera_phase6_installed", False):
        return True
    required = ("load_dataset", "read_primary_dataset", "get_primary_status")
    if not all(callable(getattr(vpg, name, None)) for name in required):
        return False

    original_load_dataset = vpg.load_dataset
    if _enabled(vpg):
        try:
            _ensure_schema(vpg)
        except Exception as exc:
            _event(vpg, "phase6", "phase6_schema_warning", f"{type(exc).__name__}: {exc}")

    def phase6_load_dataset(dataset_key, source_loader, ttl_seconds=120, force_refresh=False, wait_seconds=3.0):
        if dataset_key not in TARGET_DATASETS or not is_active(vpg):
            return original_load_dataset(
                dataset_key, source_loader, ttl_seconds=ttl_seconds,
                force_refresh=force_refresh, wait_seconds=wait_seconds,
            )

        if force_refresh:
            fresh = original_load_dataset(
                dataset_key, source_loader, ttl_seconds=ttl_seconds,
                force_refresh=True, wait_seconds=wait_seconds,
            )
            _event(vpg, dataset_key, "phase6_explicit_source_refresh", f"rows={len(fresh) if isinstance(fresh, pd.DataFrame) else 0}")
            return fresh

        try:
            df, reason = _read_current(vpg, dataset_key)
            if df is not None:
                _event(vpg, dataset_key, "phase6_pg_primary_read", f"rows={len(df)}")
                return df
            _event(vpg, dataset_key, "phase6_pg_reconcile_required", reason)
        except Exception as exc:
            _event(vpg, dataset_key, "phase6_pg_primary_read_error", f"{type(exc).__name__}: {str(exc)[:900]}")

        fresh = original_load_dataset(
            dataset_key, source_loader, ttl_seconds=ttl_seconds,
            force_refresh=True, wait_seconds=wait_seconds,
        )
        _event(vpg, dataset_key, "phase6_source_reconcile_fallback", f"rows={len(fresh) if isinstance(fresh, pd.DataFrame) else 0}")
        return fresh

    vpg.load_dataset = phase6_load_dataset
    vpg.phase6_is_enabled = lambda: is_active(vpg)
    vpg.phase6_read_backend = lambda: read_backend(vpg)
    vpg.get_phase6_status = lambda: get_status(vpg)
    vpg.ensure_phase6_schema = lambda: _ensure_schema(vpg)
    vpg._vera_phase6_installed = True
    return True
