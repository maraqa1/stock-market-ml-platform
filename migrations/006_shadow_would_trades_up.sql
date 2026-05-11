CREATE TABLE IF NOT EXISTS shadow_would_trades (
    id BIGSERIAL PRIMARY KEY,
    decision_id BIGINT NOT NULL REFERENCES intraday_decisions(id),
    decided_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    entry_price NUMERIC(12,4) NOT NULL,
    estimated_entry_slippage_bps NUMERIC(6,2) NOT NULL,
    nightly_score NUMERIC(5,4),
    gate_version TEXT NOT NULL,
    evaluation_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','evaluated','superseded','cancelled'))
);

CREATE INDEX IF NOT EXISTS ix_shadow_wt_pending
    ON shadow_would_trades(evaluation_date)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_shadow_wt_symbol_decided
    ON shadow_would_trades(symbol, decided_at DESC);
