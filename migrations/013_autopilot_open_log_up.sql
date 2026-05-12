CREATE TABLE IF NOT EXISTS autopilot_open_log (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    promotion_score NUMERIC(5,4),
    size_usd NUMERIC(10,2),
    verdict TEXT NOT NULL CHECK (verdict IN ('opened','blocked','failed')),
    block_reason TEXT,
    order_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_autopilot_open_logged_at
    ON autopilot_open_log(logged_at DESC);
