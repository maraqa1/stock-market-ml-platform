CREATE TABLE IF NOT EXISTS same_day_candidates (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long','short')),
    continuation_probability DOUBLE PRECISION NOT NULL,
    reversal_probability DOUBLE PRECISION NOT NULL,
    model_id TEXT NOT NULL,
    features_id BIGINT NOT NULL REFERENCES intraday_features(id),
    same_day_confidence DOUBLE PRECISION,
    same_day_reason TEXT NOT NULL,
    strategy_stream TEXT NOT NULL DEFAULT 'same_day_momentum' CHECK (strategy_stream IN ('multi_day_forecast','same_day_momentum')),
    max_hold_days INT NOT NULL DEFAULT 1,
    must_flatten_eod BOOLEAN NOT NULL DEFAULT TRUE,
    arbitration_outcome TEXT,
    CONSTRAINT uq_same_day_candidates_symbol_decision_time UNIQUE (symbol, decision_time)
);

CREATE INDEX IF NOT EXISTS ix_sdc_decision_time ON same_day_candidates(decision_time DESC);
CREATE INDEX IF NOT EXISTS ix_sdc_symbol_decision ON same_day_candidates(symbol, decision_time DESC);

CREATE TABLE IF NOT EXISTS same_day_signal_log (
    id BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long','short')),
    continuation_probability DOUBLE PRECISION NOT NULL,
    reversal_probability DOUBLE PRECISION NOT NULL,
    gate_outcome TEXT NOT NULL,
    block_reason TEXT,
    features_id BIGINT REFERENCES intraday_features(id)
);

CREATE INDEX IF NOT EXISTS ix_sdsl_decision_time ON same_day_signal_log(decision_time DESC);
CREATE INDEX IF NOT EXISTS ix_sdsl_symbol ON same_day_signal_log(symbol, decision_time DESC);
