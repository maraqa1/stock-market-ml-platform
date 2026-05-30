CREATE TABLE IF NOT EXISTS same_day_promotion_evaluations (
    evaluated_at TIMESTAMPTZ PRIMARY KEY,
    criteria_met BOOLEAN NOT NULL,
    criteria_results JSONB NOT NULL,
    activated BOOLEAN NOT NULL DEFAULT FALSE
);
