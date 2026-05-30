CREATE TABLE IF NOT EXISTS same_day_missed_opportunities (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    intraday_move_pct DOUBLE PRECISION NOT NULL,
    in_universe BOOLEAN NOT NULL,
    exclusion_reason TEXT,
    signal_log_count INT NOT NULL,
    max_continuation_probability DOUBLE PRECISION,
    first_blocking_gate TEXT,
    hypothetical_pnl_bps DOUBLE PRECISION,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_same_day_missed_opportunities_session_symbol UNIQUE (session_date, symbol)
);

CREATE INDEX IF NOT EXISTS ix_sdmo_session_date ON same_day_missed_opportunities(session_date DESC);
CREATE INDEX IF NOT EXISTS ix_sdmo_symbol_session ON same_day_missed_opportunities(symbol, session_date DESC);
