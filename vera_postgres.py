"""Shared PostgreSQL data layer for Vera Spa Streamlit on Cloud Run.

This module is intentionally independent from Streamlit. It provides:
- pooled PostgreSQL connections via SQLAlchemy
- cross-instance shared dataset cache in PostgreSQL
- anti-stampede refresh with PostgreSQL advisory locks
- stale-while-refresh behavior so concurrent users do not all hit Google Sheets

Safe migration mode: Google Sheets can remain the write/backup source while reads are
served from PostgreSQL. After validation, individual business tables can be promoted
to true PostgreSQL-primary storage without changing the Cloud Run deployment model.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime
from functools import lru_cache
from typing import Callable, Optional
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


CACHE_TABLE = "vera_dataset_cache"
EVENT_TABLE = "vera_sync_event"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_enabled() -> bool:
    if "VERA_DB_ENABLED" in os.environ:
        return _truthy(os.getenv("VERA_DB_ENABLED"))
    return bool(os.getenv("DATABASE_URL") or os.getenv("DB_NAME"))


def _build_database_url() -> str:
    direct = str(os.getenv("DATABASE_URL", "")).strip()
    if direct:
        # Cloud providers sometimes emit postgres://; SQLAlchemy prefers postgresql+psycopg://
        if direct.startswith("postgres://"):
            direct = "postgresql+psycopg://" + direct[len("postgres://"):]
        elif direct.startswith("postgresql://") and "+" not in direct.split("://", 1)[0]:
            direct = "postgresql+psycopg://" + direct[len("postgresql://"):]
        return direct

    user = quote_plus(str(os.getenv("DB_USER", "vera_app")))
    password = quote_plus(str(os.getenv("DB_PASS", "")))
    db_name = quote_plus(str(os.getenv("DB_NAME", "vera_spa")))
    instance = str(os.getenv("INSTANCE_CONNECTION_NAME", "")).strip()
    host = str(os.getenv("DB_HOST", "")).strip()
    port = str(os.getenv("DB_PORT", "5432")).strip() or "5432"

    if instance:
        # Cloud Run + --add-cloudsql-instances exposes this Unix socket.
        socket_dir = f"/cloudsql/{instance}"
        return f"postgresql+psycopg://{user}:{password}@/{db_name}?host={quote_plus(socket_dir)}"
    if not host:
        host = "127.0.0.1"
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if not is_enabled():
        raise RuntimeError("PostgreSQL is disabled. Set VERA_DB_ENABLED=1 and DB settings.")
    pool_size = max(2, int(os.getenv("DB_POOL_SIZE", "8")))
    max_overflow = max(0, int(os.getenv("DB_MAX_OVERFLOW", "12")))
    timeout = max(5, int(os.getenv("DB_POOL_TIMEOUT", "20")))
    recycle = max(60, int(os.getenv("DB_POOL_RECYCLE", "1200")))
    connect_timeout = max(3, int(os.getenv("DB_CONNECT_TIMEOUT", "10")))
    engine = create_engine(
        _build_database_url(),
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=timeout,
        pool_recycle=recycle,
        connect_args={"connect_timeout": connect_timeout},
        future=True,
    )
    ensure_schema(engine)
    return engine


def ensure_schema(engine: Optional[Engine] = None) -> None:
    engine = engine or get_engine()
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
        dataset_key TEXT PRIMARY KEY,
        payload JSONB NOT NULL DEFAULT '[]'::jsonb,
        row_count INTEGER NOT NULL DEFAULT 0,
        checksum TEXT NOT NULL DEFAULT '',
        source_version TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_{CACHE_TABLE}_expires ON {CACHE_TABLE}(expires_at);

    CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
        id BIGSERIAL PRIMARY KEY,
        dataset_key TEXT NOT NULL,
        event_type TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_dataset_created
        ON {EVENT_TABLE}(dataset_key, created_at DESC);
    """
    with engine.begin() as conn:
        for statement in [x.strip() for x in ddl.split(";") if x.strip()]:
            conn.execute(text(statement))


def healthcheck() -> tuple[bool, str]:
    if not is_enabled():
        return False, "PostgreSQL chưa bật (VERA_DB_ENABLED=0)."
    try:
        with get_engine().connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar_one()
        return value == 1, "PostgreSQL kết nối bình thường."
    except Exception as exc:
        return False, f"PostgreSQL lỗi kết nối: {exc}"


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _frame_to_payload(df: pd.DataFrame) -> tuple[str, int, str]:
    if df is None:
        df = pd.DataFrame()
    clean = df.copy()
    clean = clean.where(pd.notnull(clean), None)
    records = clean.to_dict(orient="records")
    payload = json.dumps(records, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return payload, len(clean), checksum


def _payload_to_frame(payload) -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return pd.DataFrame()
    if isinstance(payload, dict):
        payload = payload.get("rows", [])
    if not isinstance(payload, list):
        return pd.DataFrame()
    return pd.DataFrame(payload)


def _read_cache_row(conn, dataset_key: str):
    return conn.execute(
        text(
            f"SELECT payload, row_count, checksum, source_version, updated_at, expires_at, "
            f"(expires_at > NOW()) AS is_fresh FROM {CACHE_TABLE} WHERE dataset_key=:k"
        ),
        {"k": dataset_key},
    ).mappings().first()


def read_dataset(dataset_key: str, allow_stale: bool = True) -> Optional[pd.DataFrame]:
    if not is_enabled():
        return None
    try:
        with get_engine().connect() as conn:
            row = _read_cache_row(conn, dataset_key)
            if not row:
                return None
            if not allow_stale and not bool(row["is_fresh"]):
                return None
            return _payload_to_frame(row["payload"])
    except Exception:
        return None


def _write_dataset_conn(conn, dataset_key: str, df: pd.DataFrame, ttl_seconds: int,
                        source_version: str = "") -> pd.DataFrame:
    payload, row_count, checksum = _frame_to_payload(df)
    ttl_seconds = max(5, int(ttl_seconds))
    conn.execute(
        text(
            f"""
            INSERT INTO {CACHE_TABLE}
                (dataset_key, payload, row_count, checksum, source_version, updated_at, expires_at)
            VALUES
                (:k, CAST(:payload AS JSONB), :row_count, :checksum, :source_version, NOW(),
                 NOW() + (:ttl_seconds * INTERVAL '1 second'))
            ON CONFLICT (dataset_key) DO UPDATE SET
                payload = EXCLUDED.payload,
                row_count = EXCLUDED.row_count,
                checksum = EXCLUDED.checksum,
                source_version = EXCLUDED.source_version,
                updated_at = NOW(),
                expires_at = EXCLUDED.expires_at
            """
        ),
        {
            "k": dataset_key,
            "payload": payload,
            "row_count": row_count,
            "checksum": checksum,
            "source_version": str(source_version or ""),
            "ttl_seconds": ttl_seconds,
        },
    )
    conn.execute(
        text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES(:k,'refresh',:d)"),
        {"k": dataset_key, "d": f"rows={row_count}; checksum={checksum[:12]}"},
    )
    return df


def write_dataset(dataset_key: str, df: pd.DataFrame, ttl_seconds: int = 120,
                  source_version: str = "") -> pd.DataFrame:
    with get_engine().begin() as conn:
        return _write_dataset_conn(conn, dataset_key, df, ttl_seconds, source_version)


def invalidate_dataset(dataset_key: str) -> None:
    if not is_enabled():
        return
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(f"UPDATE {CACHE_TABLE} SET expires_at=NOW()-INTERVAL '1 second' WHERE dataset_key=:k"),
                {"k": dataset_key},
            )
            conn.execute(
                text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES(:k,'invalidate','')"),
                {"k": dataset_key},
            )
    except Exception:
        pass


def invalidate_many(*dataset_keys: str) -> None:
    for key in dataset_keys:
        if key:
            invalidate_dataset(key)


def load_dataset(
    dataset_key: str,
    source_loader: Callable[[], pd.DataFrame],
    ttl_seconds: int = 120,
    force_refresh: bool = False,
    wait_seconds: float = 3.0,
) -> pd.DataFrame:
    """Read a DataFrame from PostgreSQL and refresh from the source when needed.

    Only one Cloud Run instance refreshes an expired dataset at a time. Other instances
    return the stale PostgreSQL snapshot immediately (if available) instead of all
    hitting Google Sheets simultaneously.
    """
    if not is_enabled():
        return source_loader()

    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = _read_cache_row(conn, dataset_key)
            if row and bool(row["is_fresh"]) and not force_refresh:
                return _payload_to_frame(row["payload"])

            lock_ok = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"vera-dataset:{dataset_key}"},
                ).scalar()
            )
            if lock_ok:
                # Re-check after lock in case another transaction refreshed just before us.
                row2 = _read_cache_row(conn, dataset_key)
                if row2 and bool(row2["is_fresh"]) and not force_refresh:
                    return _payload_to_frame(row2["payload"])
                fresh = source_loader()
                if fresh is None:
                    fresh = pd.DataFrame()
                return _write_dataset_conn(conn, dataset_key, fresh, ttl_seconds)

            if row:
                # Stale-while-refresh: fast response beats a Google Sheets request storm.
                return _payload_to_frame(row["payload"])
    except Exception:
        # Database outage must not take down the spa; fall back to the existing source.
        return source_loader()

    # No cache yet and another instance is bootstrapping it: wait briefly, then fall back.
    deadline = time.time() + max(0.2, float(wait_seconds))
    while time.time() < deadline:
        cached = read_dataset(dataset_key, allow_stale=True)
        if cached is not None:
            return cached
        time.sleep(0.15)
    return source_loader()


def get_status() -> pd.DataFrame:
    if not is_enabled():
        return pd.DataFrame(columns=["dataset_key", "row_count", "updated_at", "expires_at", "is_fresh"])
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT dataset_key,row_count,updated_at,expires_at,(expires_at>NOW()) AS is_fresh "
                    f"FROM {CACHE_TABLE} ORDER BY dataset_key"
                )
            ).mappings().all()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception:
        return pd.DataFrame(columns=["dataset_key", "row_count", "updated_at", "expires_at", "is_fresh"])
