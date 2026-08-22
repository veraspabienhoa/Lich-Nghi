-- Vera Spa PostgreSQL schema (Cloud SQL for PostgreSQL)
-- V92.8.0 Phase 3 normalized CRUD + safe reconciliation foundation.
-- Google Sheets remains write-through/fallback during dual mode while PostgreSQL
-- stores durable snapshots plus normalized employees / leave_records mirrors.

CREATE TABLE IF NOT EXISTS vera_dataset_cache (
    dataset_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL DEFAULT '[]'::jsonb,
    row_count INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL DEFAULT '',
    source_version TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vera_dataset_cache_expires
    ON vera_dataset_cache(expires_at);

-- Phase 2 durable dataset store. Unlike vera_dataset_cache, rows here do not
-- expire by TTL. Existing app invalidation calls mark the matching dataset stale;
-- dual/postgres mode then reconciles it from the current source loader.
CREATE TABLE IF NOT EXISTS vera_primary_dataset (
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
);
CREATE INDEX IF NOT EXISTS idx_vera_primary_dataset_updated
    ON vera_primary_dataset(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vera_primary_dataset_stale
    ON vera_primary_dataset(is_stale, updated_at DESC);

CREATE TABLE IF NOT EXISTS vera_sync_event (
    id BIGSERIAL PRIMARY KEY,
    dataset_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vera_sync_event_dataset_created
    ON vera_sync_event(dataset_key, created_at DESC);

-- Row-level mirror used for validation and controlled cutover.
CREATE TABLE IF NOT EXISTS vera_source_row (
    dataset_key TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_row INTEGER NOT NULL DEFAULT 0,
    natural_key TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(dataset_key, source_id, source_row)
);
CREATE INDEX IF NOT EXISTS idx_vera_source_row_natural
    ON vera_source_row(dataset_key, natural_key);

-- Phase 3: normalized employee CRUD mirror.
CREATE TABLE IF NOT EXISTS employees (
    username TEXT PRIMARY KEY,
    stt INTEGER,
    password_value TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'nhanvien',
    full_name TEXT NOT NULL DEFAULT '',
    birth_date TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    bank_account TEXT NOT NULL DEFAULT '',
    bank_name TEXT NOT NULL DEFAULT '',
    monthly_generated NUMERIC NOT NULL DEFAULT 0,
    monthly_leave NUMERIC NOT NULL DEFAULT 0,
    annual_leave NUMERIC NOT NULL DEFAULT 0,
    work_shift TEXT NOT NULL DEFAULT '',
    shift_start_date TEXT NOT NULL DEFAULT '',
    rotation_cycle TEXT NOT NULL DEFAULT '',
    login_locked BOOLEAN NOT NULL DEFAULT FALSE,
    remember_token_hash TEXT NOT NULL DEFAULT '',
    remember_token_expiry TEXT NOT NULL DEFAULT '',
    employment_start_date TEXT NOT NULL DEFAULT '',
    source_sheet_id TEXT NOT NULL DEFAULT 'credentials',
    source_row INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Safe upgrades for databases created by Phase 1/2.
ALTER TABLE employees ADD COLUMN IF NOT EXISTS employment_start_date TEXT NOT NULL DEFAULT '';
ALTER TABLE employees ADD COLUMN IF NOT EXISTS source_sheet_id TEXT NOT NULL DEFAULT 'credentials';
ALTER TABLE employees ADD COLUMN IF NOT EXISTS source_row INTEGER;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_employees_role ON employees(role);
CREATE INDEX IF NOT EXISTS idx_employees_source_row ON employees(source_sheet_id, source_row);

-- Phase 3: normalized leave CRUD mirror. source_sheet_id + source_row preserves
-- the exact Google Sheet row identity used by existing edit/delete business logic.
CREATE TABLE IF NOT EXISTS leave_records (
    id BIGSERIAL PRIMARY KEY,
    source_sheet_id TEXT NOT NULL DEFAULT '',
    source_row INTEGER,
    leave_date DATE,
    employee_name TEXT NOT NULL,
    leave_reason TEXT NOT NULL,
    leave_type TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    calculated_days NUMERIC NOT NULL DEFAULT 0,
    accumulated_leave NUMERIC NOT NULL DEFAULT 0,
    penalty NUMERIC NOT NULL DEFAULT 0,
    update_date TEXT NOT NULL DEFAULT '',
    update_time TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    weekday_label TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_sheet_id, source_row)
);
ALTER TABLE leave_records ADD COLUMN IF NOT EXISTS weekday_label TEXT NOT NULL DEFAULT '';
ALTER TABLE leave_records ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS idx_leave_records_source
    ON leave_records(source_sheet_id, source_row);
CREATE INDEX IF NOT EXISTS idx_leave_records_date_employee
    ON leave_records(leave_date, employee_name);
CREATE INDEX IF NOT EXISTS idx_leave_records_employee_date
    ON leave_records(employee_name, leave_date DESC);

-- Reconciliation state separates a temporarily stale normalized mirror from a
-- confirmed current snapshot. Existing invalidate calls set stale=TRUE; the next
-- successful load clears it after a transactional full reconciliation.
CREATE TABLE IF NOT EXISTS vera_normalized_sync_state (
    dataset_key TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL DEFAULT '',
    revision BIGINT NOT NULL DEFAULT 1,
    is_stale BOOLEAN NOT NULL DEFAULT FALSE,
    last_error TEXT NOT NULL DEFAULT '',
    synced_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vera_normalized_sync_state_stale
    ON vera_normalized_sync_state(is_stale, updated_at DESC);

CREATE TABLE IF NOT EXISTS payroll_history_rows (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    employee_name TEXT NOT NULL DEFAULT '',
    period_start DATE,
    period_end DATE,
    payload JSONB NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payroll_history_batch
    ON payroll_history_rows(batch_id);
CREATE INDEX IF NOT EXISTS idx_payroll_history_employee
    ON payroll_history_rows(employee_name, period_end DESC);

CREATE TABLE IF NOT EXISTS app_config (
    config_group TEXT NOT NULL,
    config_key TEXT NOT NULL,
    config_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(config_group, config_key)
);

CREATE TABLE IF NOT EXISTS sync_outbox (
    id BIGSERIAL PRIMARY KEY,
    target_system TEXT NOT NULL DEFAULT 'google_sheets',
    dataset_key TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_pending
    ON sync_outbox(status, created_at) WHERE status='pending';
