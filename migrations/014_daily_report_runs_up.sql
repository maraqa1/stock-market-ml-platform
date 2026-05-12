CREATE TABLE IF NOT EXISTS daily_report_runs (
    session_date DATE PRIMARY KEY,
    computed_at TIMESTAMPTZ NOT NULL,
    starting_equity NUMERIC(12,2) NOT NULL,
    ending_equity NUMERIC(12,2) NOT NULL,
    realized_pnl NUMERIC(10,2) NOT NULL,
    unrealized_pnl_delta NUMERIC(10,2) NOT NULL,
    total_pnl NUMERIC(10,2) NOT NULL,
    net_pnl_pct NUMERIC(7,4) NOT NULL,
    win_rate NUMERIC(5,2),
    total_trades INT NOT NULL,
    details JSONB NOT NULL
);
