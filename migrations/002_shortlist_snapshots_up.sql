CREATE TABLE IF NOT EXISTS shortlist_snapshots (
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    rank INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    bias TEXT NOT NULL CHECK (bias IN ('long', 'short', 'neutral')),
    score DOUBLE PRECISION NOT NULL,
    expected_edge DOUBLE PRECISION,
    sector TEXT,
    in_basket BOOLEAN NOT NULL DEFAULT FALSE,
    excluded_reason TEXT,
    PRIMARY KEY (run_id, symbol)
);

CREATE INDEX IF NOT EXISTS ix_shortlist_run_rank
    ON shortlist_snapshots(run_id, rank);
