CREATE TABLE IF NOT EXISTS intraday_decisions (
    id SERIAL PRIMARY KEY,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    bar_close_at TIMESTAMPTZ NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('allow_long', 'allow_short', 'hold', 'block', 'data_unavailable')),
    block_reason TEXT,
    gate_version TEXT NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    nightly_signal JSONB,
    features JSONB NOT NULL,
    contributing JSONB
);

CREATE INDEX IF NOT EXISTS ix_intraday_decisions_decided_at
    ON intraday_decisions(decided_at DESC);

CREATE INDEX IF NOT EXISTS ix_intraday_decisions_symbol_decided_at
    ON intraday_decisions(symbol, decided_at DESC);

CREATE INDEX IF NOT EXISTS ix_intraday_decisions_verdict
    ON intraday_decisions(verdict, decided_at DESC);

CREATE INDEX IF NOT EXISTS ix_intraday_decisions_block_reason
    ON intraday_decisions(block_reason, decided_at DESC)
    WHERE block_reason IS NOT NULL;
