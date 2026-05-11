CREATE TABLE IF NOT EXISTS promotion_evaluations (
    evaluated_at TIMESTAMPTZ PRIMARY KEY,
    gate_version TEXT NOT NULL,
    criteria_met BOOLEAN NOT NULL,
    criteria_results JSONB NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS promotion_dry_runs (
    id BIGSERIAL PRIMARY KEY,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    operator_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    notes TEXT NOT NULL
);
