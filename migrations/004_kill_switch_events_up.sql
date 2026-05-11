CREATE TABLE IF NOT EXISTS kill_switch_events (
    id SERIAL PRIMARY KEY,
    switch_name TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('tripped', 'resumed')),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL,
    operator_id TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS ix_kse_switch_occurred
    ON kill_switch_events(switch_name, occurred_at DESC);
