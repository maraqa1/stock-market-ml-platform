CREATE TABLE IF NOT EXISTS forecast_cap_log (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    field_name TEXT NOT NULL,
    pre_cap_value NUMERIC(12,4) NOT NULL,
    cap_applied NUMERIC(12,4) NOT NULL,
    reason TEXT NOT NULL,
    forecast_run_id TEXT
);

CREATE INDEX IF NOT EXISTS ix_fcl_logged_at ON forecast_cap_log(logged_at DESC);
