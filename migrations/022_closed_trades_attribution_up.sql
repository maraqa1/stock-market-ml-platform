CREATE TABLE IF NOT EXISTS closed_trades_attribution (
    position_id BIGINT PRIMARY KEY REFERENCES positions(id),
    symbol TEXT NOT NULL,
    strategy_stream TEXT NOT NULL CHECK (strategy_stream IN ('multi_day_forecast', 'same_day_momentum')),
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    opened_by_signal_id TEXT,
    signal_price NUMERIC(14,4),
    entry_target NUMERIC(14,4),
    entry_fill NUMERIC(14,4),
    exit_target NUMERIC(14,4),
    exit_fill NUMERIC(14,4),
    signal_to_entry_bps NUMERIC(10,2),
    entry_to_exit_bps NUMERIC(10,2),
    exit_slippage_bps NUMERIC(10,2),
    modeled_costs_bps NUMERIC(10,2),
    realized_net_bps NUMERIC(10,2),
    realized_pnl_usd NUMERIC(14,2),
    max_favourable_bps NUMERIC(10,2),
    max_adverse_bps NUMERIC(10,2),
    minutes_to_first_positive INT,
    minutes_to_max_adverse INT,
    close_reason TEXT NOT NULL,
    trigger_source TEXT,
    signal_state_at_close TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_closed_trades_symbol_closed_at ON closed_trades_attribution(symbol, closed_at);
CREATE INDEX IF NOT EXISTS ix_closed_trades_closed_at ON closed_trades_attribution(closed_at);
CREATE INDEX IF NOT EXISTS ix_closed_trades_stream_reason ON closed_trades_attribution(strategy_stream, close_reason);
