CREATE TABLE IF NOT EXISTS intraday_promotion_log (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL,
    snapshot_id BIGINT NOT NULL REFERENCES intraday_candidate_snapshots(id),
    symbol TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN (
        'block','watch','promote_to_selection','promote_to_selection_strong'
    )),
    block_reason TEXT,
    nightly_score NUMERIC(5,4),
    intraday_adjustment NUMERIC(5,4),
    promotion_score NUMERIC(5,4),
    contributing TEXT[],
    CONSTRAINT uq_ipl_snapshot_id UNIQUE (snapshot_id)
);

CREATE INDEX IF NOT EXISTS ix_ipl_logged_at
    ON intraday_promotion_log(logged_at DESC);

CREATE INDEX IF NOT EXISTS ix_ipl_symbol_verdict
    ON intraday_promotion_log(symbol, verdict, logged_at DESC);
