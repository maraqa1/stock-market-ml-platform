CREATE TABLE IF NOT EXISTS arbitration_conflicts (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    multi_day_action TEXT,
    same_day_action TEXT,
    resolution TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_arbitration_conflicts_logged_at
    ON arbitration_conflicts(logged_at DESC);

CREATE INDEX IF NOT EXISTS ix_arbitration_conflicts_symbol_logged_at
    ON arbitration_conflicts(symbol, logged_at DESC);
