CREATE TABLE IF NOT EXISTS intraday_features (
    id BIGSERIAL PRIMARY KEY,
    computed_at TIMESTAMPTZ NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    bar_close_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok','data_unavailable','provider_error','halted','out_of_universe')),
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_intraday_features_symbol_decision_time UNIQUE (symbol, decision_time)
);

CREATE INDEX IF NOT EXISTS ix_if_decision_time
    ON intraday_features(decision_time DESC);

CREATE INDEX IF NOT EXISTS ix_if_symbol_decision
    ON intraday_features(symbol, decision_time DESC);
