CREATE TABLE IF NOT EXISTS intraday_candidate_snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_at TIMESTAMPTZ NOT NULL,
    bar_close_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    nightly_score NUMERIC(5,4),
    nightly_bias TEXT CHECK (nightly_bias IN ('long','short','neutral')),
    is_held BOOLEAN NOT NULL DEFAULT FALSE,
    bid NUMERIC(12,4),
    ask NUMERIC(12,4),
    last_price NUMERIC(12,4),
    spread_bps NUMERIC(7,2),
    quote_age_sec INT,
    dollar_volume_today NUMERIC(14,2),
    liquidity_ratio NUMERIC(6,3),
    trend_5m_pct NUMERIC(7,4),
    trend_15m_pct NUMERIC(7,4),
    trend_30m_pct NUMERIC(7,4),
    vwap_today NUMERIC(12,4),
    distance_from_vwap_bps NUMERIC(8,2),
    intraday_range_position NUMERIC(5,2),
    volatility_burst BOOLEAN NOT NULL DEFAULT FALSE,
    sector_etf_trend_5m_pct NUMERIC(7,4),
    market_aligned BOOLEAN,
    status TEXT NOT NULL CHECK (status IN ('ok','data_unavailable','provider_error')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_ics_symbol_bar_close_at UNIQUE (symbol, bar_close_at)
);

CREATE INDEX IF NOT EXISTS ix_ics_snapshot_at
    ON intraday_candidate_snapshots(snapshot_at DESC);

CREATE INDEX IF NOT EXISTS ix_ics_symbol_snapshot
    ON intraday_candidate_snapshots(symbol, snapshot_at DESC);
