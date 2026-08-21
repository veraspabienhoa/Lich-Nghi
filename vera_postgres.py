"""Shared PostgreSQL data layer for Vera Spa Streamlit on Cloud Run.

Phase 1 keeps the existing shared dataset-cache API and adds PostgreSQL-primary
storage for low-risk application settings. Phase 2 adds normalized PostgreSQL
business tables for feature permissions, employment status, and registration-role
locks. Google Sheets can stay authoritative while VERA_DATA_BACKEND=dual mirrors
these domains into PostgreSQL. After validation, VERA_DATA_BACKEND=postgres promotes
PostgreSQL to the primary store while Sheets are used only for one-time seeding.

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
DOMAIN_STATE_TABLE = "vera_domain_state"
FEATURE_PERMISSION_TABLE = "vera_feature_permission"
EMPLOYMENT_STATUS_TABLE = "vera_employment_status"
REGISTRATION_LOCK_TABLE = "vera_registration_role_lock"
PHASE1_SCHEMA_VERSION = 1
PHASE2_SCHEMA_VERSION = 2

DOMAIN_FEATURE_PERMISSIONS = "feature_permissions"
DOMAIN_EMPLOYMENT_STATUS = "employment_status"
DOMAIN_REGISTRATION_LOCKS = "registration_role_locks"


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
        CREATE TABLE IF NOT EXISTS {DOMAIN_STATE_TABLE} (
            domain_key TEXT PRIMARY KEY,
            initialized BOOLEAN NOT NULL DEFAULT FALSE,
            row_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {FEATURE_PERMISSION_TABLE} (
            scope TEXT NOT NULL,
            target_key TEXT NOT NULL,
            target_display TEXT NOT NULL DEFAULT '',
            feature_key TEXT NOT NULL,
            allowed BOOLEAN NOT NULL DEFAULT FALSE,
            source TEXT NOT NULL DEFAULT 'app',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (scope, target_key, feature_key)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{FEATURE_PERMISSION_TABLE}_target ON {FEATURE_PERMISSION_TABLE}(scope, target_key)",
        f"CREATE INDEX IF NOT EXISTS idx_{FEATURE_PERMISSION_TABLE}_feature ON {FEATURE_PERMISSION_TABLE}(feature_key)",
        f"""
        CREATE TABLE IF NOT EXISTS {EMPLOYMENT_STATUS_TABLE} (
            employee_key TEXT PRIMARY KEY,
            employee_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'app',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{EMPLOYMENT_STATUS_TABLE}_status ON {EMPLOYMENT_STATUS_TABLE}(status)",
        f"""
        CREATE TABLE IF NOT EXISTS {REGISTRATION_LOCK_TABLE} (
            role TEXT PRIMARY KEY,
            locked BOOLEAN NOT NULL DEFAULT FALSE,
            source TEXT NOT NULL DEFAULT 'app',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
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
        f"""
        INSERT INTO {SCHEMA_VERSION_TABLE}(component, version, updated_at)
        VALUES ('phase2_access_status', {PHASE2_SCHEMA_VERSION}, NOW())
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
# PHASE 2: ACCESS / EMPLOYMENT STATUS / REGISTRATION LOCKS
# ============================================================
def _mark_domain_conn(conn, domain_key: str, row_count: int, source: str = "", updated_by: str = "") -> None:
    conn.execute(
        text(
            f"""
            INSERT INTO {DOMAIN_STATE_TABLE}
                (domain_key, initialized, row_count, source, updated_by, updated_at)
            VALUES (:domain_key, TRUE, :row_count, :source, :updated_by, NOW())
            ON CONFLICT (domain_key)
            DO UPDATE SET initialized=TRUE, row_count=EXCLUDED.row_count,
                          source=EXCLUDED.source, updated_by=EXCLUDED.updated_by,
                          updated_at=NOW()
            """
        ),
        {
            "domain_key": str(domain_key),
            "row_count": max(0, int(row_count or 0)),
            "source": str(source or ""),
            "updated_by": str(updated_by or ""),
        },
    )


def domain_is_initialized(domain_key: str) -> bool:
    if not is_enabled():
        return False
    try:
        with get_engine().connect() as conn:
            value = conn.execute(
                text(f"SELECT initialized FROM {DOMAIN_STATE_TABLE} WHERE domain_key=:k"),
                {"k": str(domain_key)},
            ).scalar()
        return bool(value)
    except Exception:
        return False


def get_phase2_domain_status() -> pd.DataFrame:
    if not is_enabled():
        return pd.DataFrame(columns=["domain_key", "initialized", "row_count", "source", "updated_by", "updated_at"])
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(f"SELECT domain_key,initialized,row_count,source,updated_by,updated_at FROM {DOMAIN_STATE_TABLE} ORDER BY domain_key")
            ).mappings().all()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception:
        return pd.DataFrame(columns=["domain_key", "initialized", "row_count", "source", "updated_by", "updated_at"])


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "x", "co", "có", "locked", "khoa", "khóa"}


# ---------- Feature permissions ----------
def read_feature_permissions() -> pd.DataFrame:
    cols = ["scope", "target_key", "target_display", "feature_key", "allowed", "source", "updated_by", "updated_at"]
    if not is_enabled():
        return pd.DataFrame(columns=cols)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT scope,target_key,target_display,feature_key,allowed,source,updated_by,updated_at
                FROM {FEATURE_PERMISSION_TABLE}
                ORDER BY scope,target_key,feature_key
                """
            )
        ).mappings().all()
    return pd.DataFrame([dict(r) for r in rows], columns=cols)


def sync_feature_permissions(rows, updated_by: str = "Google Sheets mirror", source: str = "google_sheets") -> int:
    clean = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scope = str(row.get("scope", "")).strip().lower()
        target_key = str(row.get("target_key", "")).strip()
        target_display = str(row.get("target_display", row.get("target", "")) or "").strip()
        feature_key = str(row.get("feature_key", "")).strip()
        if scope not in {"role", "account"} or not target_key or not feature_key:
            continue
        clean.append({
            "scope": scope,
            "target_key": target_key,
            "target_display": target_display,
            "feature_key": feature_key,
            "allowed": _bool_value(row.get("allowed", False)),
            "source": str(row.get("source", source) or source),
            "updated_by": str(row.get("updated_by", updated_by) or updated_by),
        })
    with get_engine().begin() as conn:
        conn.execute(text(f"DELETE FROM {FEATURE_PERMISSION_TABLE}"))
        for item in clean:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {FEATURE_PERMISSION_TABLE}
                        (scope,target_key,target_display,feature_key,allowed,source,updated_by,created_at,updated_at)
                    VALUES (:scope,:target_key,:target_display,:feature_key,:allowed,:source,:updated_by,NOW(),NOW())
                    """
                ),
                item,
            )
        _mark_domain_conn(conn, DOMAIN_FEATURE_PERMISSIONS, len(clean), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_sync',:d)"),
            {"k": DOMAIN_FEATURE_PERMISSIONS, "d": f"rows={len(clean)}; source={source}"},
        )
    return len(clean)


def replace_feature_permission_scope(
    scope: str,
    target_key: str,
    target_display: str,
    feature_states: dict,
    updated_by: str = "",
    source: str = "app",
    inherit: bool = False,
) -> int:
    scope = str(scope or "").strip().lower()
    target_key = str(target_key or "").strip()
    target_display = str(target_display or "").strip()
    if scope not in {"role", "account"}:
        raise ValueError("scope phải là role hoặc account")
    if not target_key:
        raise ValueError("target_key không được để trống")
    states = feature_states if isinstance(feature_states, dict) else {}
    with get_engine().begin() as conn:
        conn.execute(
            text(f"DELETE FROM {FEATURE_PERMISSION_TABLE} WHERE scope=:scope AND target_key=:target_key"),
            {"scope": scope, "target_key": target_key},
        )
        count = 0
        if not inherit:
            for feature_key, allowed in states.items():
                feature_key = str(feature_key or "").strip()
                if not feature_key:
                    continue
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {FEATURE_PERMISSION_TABLE}
                            (scope,target_key,target_display,feature_key,allowed,source,updated_by,created_at,updated_at)
                        VALUES (:scope,:target_key,:target_display,:feature_key,:allowed,:source,:updated_by,NOW(),NOW())
                        """
                    ),
                    {
                        "scope": scope,
                        "target_key": target_key,
                        "target_display": target_display,
                        "feature_key": feature_key,
                        "allowed": bool(allowed),
                        "source": str(source or "app"),
                        "updated_by": str(updated_by or ""),
                    },
                )
                count += 1
        total = conn.execute(text(f"SELECT COUNT(*) FROM {FEATURE_PERMISSION_TABLE}")).scalar() or 0
        _mark_domain_conn(conn, DOMAIN_FEATURE_PERMISSIONS, int(total), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_write',:d)"),
            {"k": DOMAIN_FEATURE_PERMISSIONS, "d": f"scope={scope}; target={target_key}; rows={count}; inherit={bool(inherit)}"},
        )
    return count


# ---------- Employment status ----------
def read_employment_statuses() -> pd.DataFrame:
    cols = ["employee_key", "employee_name", "status", "source", "updated_by", "updated_at"]
    if not is_enabled():
        return pd.DataFrame(columns=cols)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT employee_key,employee_name,status,source,updated_by,updated_at
                FROM {EMPLOYMENT_STATUS_TABLE}
                ORDER BY employee_name,employee_key
                """
            )
        ).mappings().all()
    return pd.DataFrame([dict(r) for r in rows], columns=cols)


def sync_employment_statuses(rows, updated_by: str = "Google Sheets mirror", source: str = "google_sheets") -> int:
    by_key = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("employee_key", "")).strip()
        status = str(row.get("status", "")).strip()
        if not key or not status:
            continue
        by_key[key] = {
            "employee_key": key,
            "employee_name": str(row.get("employee_name", "") or "").strip(),
            "status": status,
            "source": str(row.get("source", source) or source),
            "updated_by": str(row.get("updated_by", updated_by) or updated_by),
        }
    clean = list(by_key.values())
    with get_engine().begin() as conn:
        conn.execute(text(f"DELETE FROM {EMPLOYMENT_STATUS_TABLE}"))
        for item in clean:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {EMPLOYMENT_STATUS_TABLE}
                        (employee_key,employee_name,status,source,updated_by,created_at,updated_at)
                    VALUES (:employee_key,:employee_name,:status,:source,:updated_by,NOW(),NOW())
                    """
                ),
                item,
            )
        _mark_domain_conn(conn, DOMAIN_EMPLOYMENT_STATUS, len(clean), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_sync',:d)"),
            {"k": DOMAIN_EMPLOYMENT_STATUS, "d": f"rows={len(clean)}; source={source}"},
        )
    return len(clean)


def upsert_employment_status(employee_key: str, employee_name: str, status: str, updated_by: str = "", source: str = "app") -> None:
    employee_key = str(employee_key or "").strip()
    status = str(status or "").strip()
    if not employee_key or not status:
        raise ValueError("employee_key và status không được để trống")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {EMPLOYMENT_STATUS_TABLE}
                    (employee_key,employee_name,status,source,updated_by,created_at,updated_at)
                VALUES (:employee_key,:employee_name,:status,:source,:updated_by,NOW(),NOW())
                ON CONFLICT (employee_key)
                DO UPDATE SET employee_name=EXCLUDED.employee_name,status=EXCLUDED.status,
                              source=EXCLUDED.source,updated_by=EXCLUDED.updated_by,updated_at=NOW()
                """
            ),
            {
                "employee_key": employee_key,
                "employee_name": str(employee_name or "").strip(),
                "status": status,
                "source": str(source or "app"),
                "updated_by": str(updated_by or ""),
            },
        )
        total = conn.execute(text(f"SELECT COUNT(*) FROM {EMPLOYMENT_STATUS_TABLE}")).scalar() or 0
        _mark_domain_conn(conn, DOMAIN_EMPLOYMENT_STATUS, int(total), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_write',:d)"),
            {"k": DOMAIN_EMPLOYMENT_STATUS, "d": f"employee={employee_key}; status={status}"},
        )


# ---------- Registration role locks ----------
def read_registration_role_locks() -> pd.DataFrame:
    cols = ["role", "locked", "source", "updated_by", "updated_at"]
    if not is_enabled():
        return pd.DataFrame(columns=cols)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT role,locked,source,updated_by,updated_at
                FROM {REGISTRATION_LOCK_TABLE}
                ORDER BY role
                """
            )
        ).mappings().all()
    return pd.DataFrame([dict(r) for r in rows], columns=cols)


def sync_registration_role_locks(rows, updated_by: str = "Google Sheets mirror", source: str = "google_sheets") -> int:
    by_role = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip().lower()
        if not role:
            continue
        by_role[role] = {
            "role": role,
            "locked": _bool_value(row.get("locked", False)),
            "source": str(row.get("source", source) or source),
            "updated_by": str(row.get("updated_by", updated_by) or updated_by),
        }
    clean = list(by_role.values())
    with get_engine().begin() as conn:
        conn.execute(text(f"DELETE FROM {REGISTRATION_LOCK_TABLE}"))
        for item in clean:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {REGISTRATION_LOCK_TABLE}
                        (role,locked,source,updated_by,created_at,updated_at)
                    VALUES (:role,:locked,:source,:updated_by,NOW(),NOW())
                    """
                ),
                item,
            )
        _mark_domain_conn(conn, DOMAIN_REGISTRATION_LOCKS, len(clean), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_sync',:d)"),
            {"k": DOMAIN_REGISTRATION_LOCKS, "d": f"rows={len(clean)}; source={source}"},
        )
    return len(clean)


def upsert_registration_role_lock(role: str, locked: bool, updated_by: str = "", source: str = "app") -> None:
    role = str(role or "").strip().lower()
    if not role:
        raise ValueError("role không được để trống")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {REGISTRATION_LOCK_TABLE}
                    (role,locked,source,updated_by,created_at,updated_at)
                VALUES (:role,:locked,:source,:updated_by,NOW(),NOW())
                ON CONFLICT (role)
                DO UPDATE SET locked=EXCLUDED.locked,source=EXCLUDED.source,
                              updated_by=EXCLUDED.updated_by,updated_at=NOW()
                """
            ),
            {
                "role": role,
                "locked": bool(locked),
                "source": str(source or "app"),
                "updated_by": str(updated_by or ""),
            },
        )
        total = conn.execute(text(f"SELECT COUNT(*) FROM {REGISTRATION_LOCK_TABLE}")).scalar() or 0
        _mark_domain_conn(conn, DOMAIN_REGISTRATION_LOCKS, int(total), source, updated_by)
        conn.execute(
            text(f"INSERT INTO {EVENT_TABLE}(dataset_key,event_type,detail) VALUES (:k,'phase2_write',:d)"),
            {"k": DOMAIN_REGISTRATION_LOCKS, "d": f"role={role}; locked={bool(locked)}"},
        )


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
