CREATE TABLE IF NOT EXISTS shadow_outcomes (
    would_trade_id BIGINT PRIMARY KEY REFERENCES shadow_would_trades(id),
    evaluated_at TIMESTAMPTZ NOT NULL,
    exit_price NUMERIC(12,4) NOT NULL,
    raw_return_pct NUMERIC(7,4) NOT NULL,
    cost_bps NUMERIC(6,2) NOT NULL,
    net_return_pct NUMERIC(7,4) NOT NULL,
    spy_return_pct NUMERIC(7,4) NOT NULL,
    net_excess_pct NUMERIC(7,4) NOT NULL,
    outperformed BOOLEAN NOT NULL
);
