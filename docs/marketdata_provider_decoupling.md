# Market Data Provider Decoupling

SPEC 69 starts by separating provider access from downstream feature, gold,
model, trading, and portal code.

## Current Boundary

Downstream code must consume canonical artifacts and schemas:

- price history store: `date`, `ticker`, `open`, `high`, `low`, `close`,
  `adj_close`, `volume`, `source`, `download_timestamp`
- fundamentals metadata: `ticker`, `company`, `exchange`, `sector`, `industry`,
  `market_cap`, `beta`, `trailing_pe`, `forward_pe`, `price_to_book`,
  `dividend_yield`, `average_volume`, `quote_type`, `currency`, `country`,
  `metadata_status`, `metadata_error`

Provider SDK calls belong only under `src/stockml/marketdata/providers/`.

## Phase 1

The existing Yahoo/yfinance behavior is wrapped by
`YahooLegacyProvider`. This is intentionally a no-behavior-change adapter:
it preserves current price and metadata schemas while creating a stable seam for
replacement.

The old modules still exist as compatibility wrappers:

- `stockml.prices.download_price_history`
- `stockml.metadata.yahoo_metadata`

They now call the provider adapter instead of importing yfinance directly.

## Future Provider Replacement

The next provider should implement `MarketDataProvider` and return the same
canonical schemas. A future config switch can then choose:

```yaml
marketdata:
  primary_provider: alpha_vantage
  fallback_providers: []
```

Before disabling Yahoo, run a coverage comparison:

- requested universe count
- symbols with valid price history
- symbols with market cap
- symbols reaching gold
- symbols reaching latest model signals
- provider failures by reason

Portal code must remain provider-independent.
