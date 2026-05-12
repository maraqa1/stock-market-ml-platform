CREATE TABLE IF NOT EXISTS rotation_recommendation_log (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL,
    replace_symbol TEXT NOT NULL,
    with_symbol TEXT NOT NULL,
    replace_position_id TEXT,
    promotion_score NUMERIC(5,4),
    held_score NUMERIC(5,4),
    score_delta NUMERIC(5,4),
    reason TEXT NOT NULL CHECK (reason IN (
        'HIGHER_PROMOTION_SCORE',
        'HELD_SIGNAL_STALE',
        'HELD_NEGATIVE_TREND',
        'HELD_DROPPED_FROM_SHORTLIST'
    )),
    verdict TEXT NOT NULL CHECK (verdict IN (
        'proposed','confirmed','overridden','expired','blocked'
    )),
    operator_id TEXT,
    operator_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_rrl_logged_at
    ON rotation_recommendation_log(logged_at DESC);
