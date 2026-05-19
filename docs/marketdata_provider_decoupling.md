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

Provider SDK/API calls belong only in provider adapters:

- market data: `src/stockml/marketdata/providers/`
- news sentiment: `src/stockml/sentiment/*_provider.py`

Pipeline, feature, gold, model, trading, and portal code should choose providers
through factories and consume canonical artifacts. They should not import vendor
SDKs or call vendor URLs directly.

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

Provider adapters implement `MarketDataProvider` and return the same canonical
schemas. The initial alternative provider is EOD Historical Data, available as
`eodhd`.

```yaml
marketdata:
  primary_provider: eodhd
  fallback_providers: []
  eodhd:
    default_exchange_suffix: US
```

The EODHD adapter reads `EODHD_API_KEY` from the environment and keeps provider
symbols such as `SEDG.US` internal to the adapter. Downstream artifacts still use
canonical tickers such as `SEDG`.

Price download can be run explicitly with:

```bash
PYTHONPATH=src python -m stockml.prices.download_price_history --provider eodhd --limit 20 --force-full
```

The price store is bootstrapped once per provider, then updated by delta runs.
The downloader uses the `source` column to decide whether that provider already
has history in the canonical store. For example, a Yahoo-populated store does
not count as an EODHD bootstrap; the first EODHD production run downloads the
complete requested universe from `start_date`, and later EODHD runs request only
the missing window with a small overlap for corrections.

The full NYSE EODHD profile is:

```bash
PYTHONPATH=src python scripts/run_profile_pipeline.py --profile nyse_full
```

The NYSE profile uses EODHD for both prices and sentiment:

```yaml
nyse_full:
  provider: eodhd
  sentiment_provider: eodhd
```

Metadata can be run explicitly with:

```bash
PYTHONPATH=src python -m stockml.metadata.build_metadata_enriched --provider eodhd --limit 20
```

Before disabling Yahoo, run a coverage comparison:

- requested universe count
- symbols with valid price history
- symbols with market cap
- symbols reaching gold
- symbols reaching latest model signals
- provider failures by reason

Portal code must remain provider-independent.

## Adding Another Provider

To add a new EOD/fundamentals provider:

1. Implement `MarketDataProvider` in `src/stockml/marketdata/providers/`.
2. Return exactly the canonical `PRICE_COLUMNS` and `FUNDAMENTAL_COLUMNS`.
3. Keep vendor symbols internal to the adapter and emit canonical tickers.
4. Register aliases in `stockml.marketdata.providers.factory.provider_from_name`.
5. Add provider schema/normalization tests.

To add a new sentiment provider:

1. Implement `NewsProviderBase` in `src/stockml/sentiment/`.
2. Return article dictionaries with `title`, `summary`, `providerPublishTime`,
   `link`, and optionally `providerSentiment`.
3. Register aliases in
   `stockml.sentiment.provider_factory.sentiment_providers_from_name`.
4. Reuse `build_sentiment_panel` so output remains `SENTIMENT_COLUMNS`.
5. Add provider normalization and factory tests.
