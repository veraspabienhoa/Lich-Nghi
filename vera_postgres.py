"""Shared PostgreSQL data layer for Vera Spa Streamlit on Cloud Run.

Phase 1 keeps the existing shared dataset-cache API and adds PostgreSQL-primary
storage for low-risk application settings. Google Sheets can stay authoritative
while VERA_DATA_BACKEND=dual mirrors settings into PostgreSQL. After validation,
VERA_DATA_BACKEND=postgres promotes PostgreSQL to the primary setting store.

Supported VERA_DATA_BACKEND values:
- sheets   : current behavior; PostgreSQL dataset cache may still be used.
- dual     : read Google Sheets, mirror settings to PostgreSQL, dual-write saves.
- postgres : read/write settings in PostgreSQL; seed missing settings from Sheets.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


CACHE_TABLE = "vera_dataset_cache"
EVENT_TABLE = "vera_sync_event"
SETTING_TABLE = "vera_app_setting"
SCHEMA_VERSION_TABLE = "vera_schema_version"
PHASE1_SCHEMA_VERSION = 1


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_enabled() -> bool:
    if "VERA_DB_ENABLED" in os.environ:
        return _truthy(os.getenv("VERA_DB_ENABLED"))
    return bool(os.getenv("DATABASE_URL") or os.getenv("DB_NAME"))


def data_backend_mode() -> str:
    """Return sheets/dual/postgres. If PostgreSQL is disabled, force sheets."""
    raw = str(os.getenv("VERA_DATA_BACKEND", "sheets") or "sheets").strip().lower()
    aliases = {
        "sheet": "sheets",
        "google": "sheets",
        "google_sheets": "sheets",
        "pg": "postgres",
        "postgresql": "postgres",
    }
    mode = aliases.get(raw, raw)
    if mode not in {"sheets", "dual", "postgres"}:
        mode = "sheets"
    if mode in {"dual", "postgres"} and not is_enabled():
        return "sheets"
    return mode


def _build_database_url() -> str:
    direct = str(os.getenv("DATABASE_URL", "")).strip()
    if direct:
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
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
            dataset_key TEXT PRIMARY KEY,
            payload JSONB NOT NULL DEFAULT '[]'::jsonb,
            row_count INTEGER NOT NULL DEFAULT 0,
            checksum TEXT NOT NULL DEFAULT '',
            source_version TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{CACHE_TABLE}_expires ON {CACHE_TABLE}(expires_at)",
        f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_dataset_created ON {EVENT_TABLE}(dataset_key, created_at DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {SETTING_TABLE} (
            category TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            value_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source TEXT NOT NULL DEFAULT 'app',
            updated_by TEXT NOT NULL DEFAULT '',
            revision BIGINT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (category, setting_key)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{SETTING_TABLE}_updated ON {SETTING_TABLE}(updated_at DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"""
        INSERT INTO {SCHEMA_VERSION_TABLE}(component, version, updated_at)
        VALUES ('phase1_settings', {PHASE1_SCHEMA_VERSION}, NOW())
        ON CONFLICT (component) DO UPDATE
        SET version = GREATEST({SCHEMA_VERSION_TABLE}.version, EXCLUDED.version),
            updated_at = NOW()
        """,
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def healthcheck() -> tuple[bool, str]:
    if not is_enabled():
        return False, "PostgreSQL chưa bật (VERA_DB_ENABLED=0)."
    try:
        with get_engine().connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar_one()
        mode = data_backend_mode()
        return value == 1, f"PostgreSQL kết nối bình thường · data backend={mode}."
    except Exception as exc:
        return False, f"PostgreSQL lỗi kết nối: {exc}"


# ============================================================
# JSON SANITIZER
# ============================================================
def _sanitize_json_value(value: Any):
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        if bool(pd.isna(value)):
            return None
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            return _sanitize_json_value(value.item())
        except Exception:
            pass
    return value


def _json_default(value):
    safe = _sanitize_json_value(value)
    if safe is not value:
        return safe
    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(
        _sanitize_json_value(value),
        ensure_ascii=False,
        default=_json_default,
        allow_nan=False,
        separators=(",", ":"),
    )


# ============================================================
# PHASE 1: APPLICATION SETTINGS
# ============================================================
def get_setting(category: str, setting_key: str) -> Optional[dict]:
    if not is_enabled():
        return None
    category = str(category or "").strip()
    setting_key = str(setting_key or "").strip()
    if not category or not setting_key:
        return None
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    f"""
                    SELECT category, setting_key, value_json, source, updated_by,
                           revision, created_at, updated_at
                    FROM {SETTING_TABLE}
                    WHERE category=:category AND setting_key=:setting_key
                    """
                ),
                {"category": category, "setting_key": setting_key},
            ).mappings().first()
        if not row:
            return None
        out = dict(row)
        value = out.get("value_json")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass
        out["value"] = value
        out.pop("value_json", None)
        return out
    except Exception:
        return None


def read_setting(category: str, setting_key: str, default: Any = None) -> Any:
    row = get_setting(category, setting_key)
    return default if row is None else row.get("value", default)


def write_setting(
    category: str,
    setting_key: str,
    value: Any,
    updated_by: str = "",
    source: str = "app",
) -> dict:
    if not is_enabled():
        raise RuntimeError("PostgreSQL chưa bật nên không thể lưu setting.")
    category = str(category or "").strip()
    setting_key = str(setting_key or "").strip()
    if not category or not setting_key:
        raise ValueError("category và setting_key không được để trống")
    payload = _json_text(value)
    with get_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {SETTING_TABLE}
                    (category, setting_key, value_json, source, updated_by, revision, created_at, updated_at)
                VALUES
                    (:category, :setting_key, CAST(:payload AS JSONB), :source, :updated_by, 1, NOW(), NOW())
                ON CONFLICT (category, setting_key)
                DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    source = EXCLUDED.source,
                    updated_by = EXCLUDED.updated_by,
                    revision = {SETTING_TABLE}.revision + 1,
                    updated_at = NOW()
                """
            ),
            {
                "category": category,
                "setting_key": setting_key,
                "payload": payload,
                "source": str(source or "app"),
                "updated_by": str(updated_by or ""),
            },
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {EVENT_TABLE}(dataset_key, event_type, detail)
                VALUES (:dataset_key, 'setting_write', :detail)
                """
            ),
            {
                "dataset_key": f"setting:{category}:{setting_key}",
                "detail": f"source={source}; updated_by={updated_by}",
            },
        )
    return get_setting(category, setting_key) or {
        "category": category,
        "setting_key": setting_key,
        "value": _sanitize_json_value(value),
    }


def delete_setting(category: str, setting_key: str, updated_by: str = "") -> bool:
    if not is_enabled():
        return False
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text(f"DELETE FROM {SETTING_TABLE} WHERE category=:c AND setting_key=:k"),
                {"c": str(category), "k": str(setting_key)},
            )
            conn.execute(
                text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:d,'setting_delete',:x)"),
                {"d": f"setting:{category}:{setting_key}", "x": f"updated_by={updated_by}"},
            )
        return bool(result.rowcount)
    except Exception:
        return False


def list_settings(category: Optional[str] = None) -> pd.DataFrame:
    if not is_enabled():
        return pd.DataFrame(columns=["category", "setting_key", "value", "source", "updated_by", "revision", "updated_at"])
    try:
        with get_engine().connect() as conn:
            if category:
                rows = conn.execute(
                    text(f"SELECT * FROM {SETTING_TABLE} WHERE category=:c ORDER BY setting_key"),
                    {"c": str(category)},
                ).mappings().all()
            else:
                rows = conn.execute(text(f"SELECT * FROM {SETTING_TABLE} ORDER BY category, setting_key")).mappings().all()
        out = []
        for row in rows:
            d = dict(row)
            d["value"] = d.pop("value_json", None)
            out.append(d)
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame(columns=["category", "setting_key", "value", "source", "updated_by", "revision", "updated_at"])


# ============================================================
# SHARED DATASET CACHE (existing V75 API preserved)
# ============================================================
def _frame_to_payload(df: pd.DataFrame) -> tuple[str, int, str]:
    if df is None:
        df = pd.DataFrame()
    records = [_sanitize_json_value(row) for row in df.copy().to_dict(orient="records")]
    payload = json.dumps(records, ensure_ascii=False, default=_json_default, allow_nan=False, separators=(",", ":"))
    checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return payload, len(df), checksum


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
            f"""
            SELECT payload,row_count,checksum,source_version,updated_at,expires_at,
                   (expires_at > NOW()) AS is_fresh
            FROM {CACHE_TABLE}
            WHERE dataset_key=:k
            """
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


def _write_dataset_conn(conn, dataset_key: str, df: pd.DataFrame, ttl_seconds: int, source_version: str = "") -> pd.DataFrame:
    payload, row_count, checksum = _frame_to_payload(df)
    ttl_seconds = max(5, int(ttl_seconds))
    conn.execute(
        text(
            f"""
            INSERT INTO {CACHE_TABLE}
                (dataset_key,payload,row_count,checksum,source_version,updated_at,expires_at)
            VALUES
                (:k,CAST(:payload AS JSONB),:row_count,:checksum,:source_version,NOW(),NOW()+(:ttl_seconds*INTERVAL '1 second'))
            ON CONFLICT (dataset_key)
            DO UPDATE SET payload=EXCLUDED.payload,row_count=EXCLUDED.row_count,
                          checksum=EXCLUDED.checksum,source_version=EXCLUDED.source_version,
                          updated_at=NOW(),expires_at=EXCLUDED.expires_at
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
        text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'refresh',:d)"),
        {"k": dataset_key, "d": f"rows={row_count}; checksum={checksum[:12]}"},
    )
    return df


def write_dataset(dataset_key: str, df: pd.DataFrame, ttl_seconds: int = 120, source_version: str = "") -> pd.DataFrame:
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
                text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'invalidate','')"),
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
                row2 = _read_cache_row(conn, dataset_key)
                if row2 and bool(row2["is_fresh"]) and not force_refresh:
                    return _payload_to_frame(row2["payload"])
                fresh = source_loader()
                if fresh is None:
                    fresh = pd.DataFrame()
                return _write_dataset_conn(conn, dataset_key, fresh, ttl_seconds)
            if row:
                return _payload_to_frame(row["payload"])
    except Exception:
        return source_loader()

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
                    f"""
                    SELECT dataset_key,row_count,updated_at,expires_at,
                           (expires_at > NOW()) AS is_fresh
                    FROM {CACHE_TABLE}
                    ORDER BY dataset_key
                    """
                )
            ).mappings().all()
        return pd.DataFrame([dict(row) for row in rows])
    except Exception:
        return pd.DataFrame(columns=["dataset_key", "row_count", "updated_at", "expires_at", "is_fresh"])
