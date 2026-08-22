"""Vera Spa PostgreSQL Phase 2 durable dataset layer.

This module upgrades the existing ``vera_postgres`` cache without changing the
business code in ``app_v92699_core.py``.

Modes:
- sheets: keep the current Phase-1 behavior.
- dual: keep Google Sheets authoritative and mirror each loaded dataset into a
  durable PostgreSQL primary snapshot.
- postgres: read durable PostgreSQL first. If a dataset is missing/stale (for
  example after an existing Google-Sheets write invalidates it), refresh once
  from the current source loader and immediately promote the result back to
  PostgreSQL.

The wrappers are deliberately best-effort in ``dual`` mode so a PostgreSQL
problem never blocks the existing Google Sheets workflow.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import text


PRIMARY_DATASET_TABLE = "vera_primary_dataset"
PHASE2_SCHEMA_VERSION = 2


def _phase2_enabled(vpg) -> bool:
    try:
        return bool(vpg.is_enabled())
    except Exception:
        return False


def _mode(vpg) -> str:
    try:
        return str(vpg.data_backend_mode() or "sheets").strip().lower()
    except Exception:
        return "sheets"


def _ensure_phase2_schema(vpg, engine=None) -> None:
    if not _phase2_enabled(vpg):
        return
    engine = engine or vpg.get_engine()
    version_table = getattr(vpg, "SCHEMA_VERSION_TABLE", "vera_schema_version")
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {PRIMARY_DATASET_TABLE} (
            dataset_key TEXT PRIMARY KEY,
            payload JSONB NOT NULL DEFAULT '[]'::jsonb,
            row_count INTEGER NOT NULL DEFAULT 0,
            checksum TEXT NOT NULL DEFAULT '',
            source_version TEXT NOT NULL DEFAULT '',
            source_system TEXT NOT NULL DEFAULT 'google_sheets',
            revision BIGINT NOT NULL DEFAULT 1,
            is_stale BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{PRIMARY_DATASET_TABLE}_updated "
        f"ON {PRIMARY_DATASET_TABLE}(updated_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{PRIMARY_DATASET_TABLE}_stale "
        f"ON {PRIMARY_DATASET_TABLE}(is_stale, updated_at DESC)",
        f"""
        INSERT INTO {version_table}(component, version, updated_at)
        VALUES ('phase2_primary_dataset', {PHASE2_SCHEMA_VERSION}, NOW())
        ON CONFLICT (component) DO UPDATE
        SET version = GREATEST({version_table}.version, EXCLUDED.version),
            updated_at = NOW()
        """,
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _read_primary_row(vpg, conn, dataset_key: str):
    return conn.execute(
        text(
            f"""
            SELECT dataset_key,payload,row_count,checksum,source_version,
                   source_system,revision,is_stale,created_at,updated_at
            FROM {PRIMARY_DATASET_TABLE}
            WHERE dataset_key=:k
            """
        ),
        {"k": str(dataset_key)},
    ).mappings().first()


def _frame_to_payload(vpg, df: pd.DataFrame):
    helper = getattr(vpg, "_frame_to_payload", None)
    if callable(helper):
        return helper(df)

    import hashlib
    import json

    if df is None:
        df = pd.DataFrame()
    records = df.copy().where(pd.notna(df), None).to_dict(orient="records")
    payload = json.dumps(
        records,
        ensure_ascii=False,
        default=str,
        allow_nan=False,
        separators=(",", ":"),
    )
    return payload, len(df), hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_to_frame(vpg, payload) -> pd.DataFrame:
    helper = getattr(vpg, "_payload_to_frame", None)
    if callable(helper):
        return helper(payload)

    import json

    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame(payload if isinstance(payload, list) else [])


def _write_primary_conn(
    vpg,
    conn,
    dataset_key: str,
    df: pd.DataFrame,
    source_version: str = "",
    source_system: str = "google_sheets",
) -> pd.DataFrame:
    if df is None:
        df = pd.DataFrame()
    payload, row_count, checksum = _frame_to_payload(vpg, df)
    conn.execute(
        text(
            f"""
            INSERT INTO {PRIMARY_DATASET_TABLE}
                (dataset_key,payload,row_count,checksum,source_version,source_system,
                 revision,is_stale,created_at,updated_at)
            VALUES
                (:k,CAST(:payload AS JSONB),:row_count,:checksum,:source_version,
                 :source_system,1,FALSE,NOW(),NOW())
            ON CONFLICT (dataset_key)
            DO UPDATE SET
                payload=EXCLUDED.payload,
                row_count=EXCLUDED.row_count,
                checksum=EXCLUDED.checksum,
                source_version=EXCLUDED.source_version,
                source_system=EXCLUDED.source_system,
                revision={PRIMARY_DATASET_TABLE}.revision + 1,
                is_stale=FALSE,
                updated_at=NOW()
            """
        ),
        {
            "k": str(dataset_key),
            "payload": payload,
            "row_count": int(row_count),
            "checksum": str(checksum),
            "source_version": str(source_version or ""),
            "source_system": str(source_system or "google_sheets"),
        },
    )
    return df


def read_primary_dataset(vpg, dataset_key: str, allow_stale: bool = True) -> Optional[pd.DataFrame]:
    if not _phase2_enabled(vpg):
        return None
    try:
        engine = vpg.get_engine()
        _ensure_phase2_schema(vpg, engine)
        with engine.connect() as conn:
            row = _read_primary_row(vpg, conn, dataset_key)
        if not row:
            return None
        if not allow_stale and bool(row["is_stale"]):
            return None
        return _payload_to_frame(vpg, row["payload"])
    except Exception:
        return None


def write_primary_dataset(
    vpg,
    dataset_key: str,
    df: pd.DataFrame,
    source_version: str = "",
    source_system: str = "app",
) -> pd.DataFrame:
    engine = vpg.get_engine()
    _ensure_phase2_schema(vpg, engine)
    with engine.begin() as conn:
        out = _write_primary_conn(
            vpg,
            conn,
            dataset_key,
            df,
            source_version=source_version,
            source_system=source_system,
        )
    try:
        vpg.record_event(
            dataset_key,
            "phase2_primary_write",
            f"rows={len(df) if isinstance(df, pd.DataFrame) else 0}; source={source_system}",
        )
    except Exception:
        pass
    return out


def mark_primary_stale(vpg, dataset_key: str) -> None:
    if not _phase2_enabled(vpg):
        return
    try:
        engine = vpg.get_engine()
        _ensure_phase2_schema(vpg, engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    UPDATE {PRIMARY_DATASET_TABLE}
                    SET is_stale=TRUE, updated_at=NOW()
                    WHERE dataset_key=:k
                    """
                ),
                {"k": str(dataset_key)},
            )
    except Exception:
        pass


def get_primary_status(vpg) -> pd.DataFrame:
    columns = [
        "dataset_key", "row_count", "checksum", "source_version",
        "source_system", "revision", "is_stale", "created_at", "updated_at",
    ]
    if not _phase2_enabled(vpg):
        return pd.DataFrame(columns=columns)
    try:
        engine = vpg.get_engine()
        _ensure_phase2_schema(vpg, engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT dataset_key,row_count,checksum,source_version,
                           source_system,revision,is_stale,created_at,updated_at
                    FROM {PRIMARY_DATASET_TABLE}
                    ORDER BY dataset_key
                    """
                )
            ).mappings().all()
        return pd.DataFrame([dict(row) for row in rows], columns=columns)
    except Exception:
        return pd.DataFrame(columns=columns)


def install(vpg) -> bool:
    """Install Phase-2 wrappers on an imported ``vera_postgres`` module."""
    if vpg is None or getattr(vpg, "_vera_phase2_installed", False):
        return bool(vpg is not None)
    if not all(
        callable(getattr(vpg, name, None))
        for name in ("load_dataset", "invalidate_dataset", "write_dataset")
    ):
        return False

    original_load_dataset = vpg.load_dataset
    original_invalidate_dataset = vpg.invalidate_dataset
    original_write_dataset = vpg.write_dataset

    def phase2_load_dataset(
        dataset_key,
        source_loader,
        ttl_seconds=120,
        force_refresh=False,
        wait_seconds=3.0,
    ):
        mode = _mode(vpg)
        if mode == "sheets" or not _phase2_enabled(vpg):
            return original_load_dataset(
                dataset_key,
                source_loader,
                ttl_seconds=ttl_seconds,
                force_refresh=force_refresh,
                wait_seconds=wait_seconds,
            )

        engine = None
        primary_row = None
        try:
            engine = vpg.get_engine()
            _ensure_phase2_schema(vpg, engine)
            with engine.connect() as conn:
                primary_row = _read_primary_row(vpg, conn, dataset_key)
        except Exception:
            if mode == "dual":
                return original_load_dataset(
                    dataset_key,
                    source_loader,
                    ttl_seconds=ttl_seconds,
                    force_refresh=force_refresh,
                    wait_seconds=wait_seconds,
                )

        if (
            mode == "postgres"
            and primary_row
            and not bool(primary_row["is_stale"])
            and not force_refresh
        ):
            try:
                vpg.record_event(dataset_key, "phase2_primary_read", "source=postgres")
            except Exception:
                pass
            return _payload_to_frame(vpg, primary_row["payload"])

        try:
            fresh = original_load_dataset(
                dataset_key,
                source_loader,
                ttl_seconds=ttl_seconds,
                force_refresh=bool(force_refresh or mode == "postgres"),
                wait_seconds=wait_seconds,
            )
            if fresh is None:
                fresh = pd.DataFrame()
        except Exception:
            if primary_row:
                try:
                    vpg.record_event(
                        dataset_key,
                        "phase2_primary_fallback",
                        "source refresh failed",
                    )
                except Exception:
                    pass
                return _payload_to_frame(vpg, primary_row["payload"])
            raise

        try:
            if engine is None:
                engine = vpg.get_engine()
            _ensure_phase2_schema(vpg, engine)
            with engine.begin() as conn:
                _write_primary_conn(
                    vpg,
                    conn,
                    dataset_key,
                    fresh,
                    source_version="phase2",
                    source_system="google_sheets",
                )
            try:
                vpg.record_event(
                    dataset_key,
                    "phase2_primary_mirror",
                    f"mode={mode}; rows={len(fresh) if isinstance(fresh, pd.DataFrame) else 0}",
                )
            except Exception:
                pass
        except Exception:
            if mode == "postgres" and primary_row:
                return _payload_to_frame(vpg, primary_row["payload"])
        return fresh

    def phase2_invalidate_dataset(dataset_key):
        original_invalidate_dataset(dataset_key)
        if _mode(vpg) in {"dual", "postgres"}:
            mark_primary_stale(vpg, dataset_key)

    def phase2_write_dataset(dataset_key, df, ttl_seconds=120, source_version=""):
        out = original_write_dataset(
            dataset_key,
            df,
            ttl_seconds=ttl_seconds,
            source_version=source_version,
        )
        if _mode(vpg) in {"dual", "postgres"}:
            try:
                write_primary_dataset(
                    vpg,
                    dataset_key,
                    df,
                    source_version=source_version or "phase2",
                    source_system="app",
                )
            except Exception:
                if _mode(vpg) == "postgres":
                    raise
        return out

    vpg.load_dataset = phase2_load_dataset
    vpg.invalidate_dataset = phase2_invalidate_dataset
    vpg.write_dataset = phase2_write_dataset
    vpg.read_primary_dataset = (
        lambda dataset_key, allow_stale=True:
        read_primary_dataset(vpg, dataset_key, allow_stale=allow_stale)
    )
    vpg.write_primary_dataset = (
        lambda dataset_key, df, source_version="", source_system="app":
        write_primary_dataset(
            vpg,
            dataset_key,
            df,
            source_version=source_version,
            source_system=source_system,
        )
    )
    vpg.mark_primary_dataset_stale = (
        lambda dataset_key: mark_primary_stale(vpg, dataset_key)
    )
    vpg.get_primary_status = lambda: get_primary_status(vpg)
    vpg.ensure_phase2_schema = lambda: _ensure_phase2_schema(vpg)
    vpg._vera_phase2_installed = True
    return True
