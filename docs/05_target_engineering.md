# Target Engineering

Gold targets are ranking-first:

- `target_return_5d`
- `target_return_10d`
- `target_sector_relative_return_5d`
- `target_sector_relative_return_10d`
- `target_realized_volatility_5d`
- `target_vol_adjusted_return_5d`
- `target_decay_weighted_return_5d`
- `target_rank_pct_by_date_5d`
- `target_rank_pct_by_date_decay_5d`
- `target_top_quintile_5d`
- `target_bottom_quintile_5d`
- `target_trade_label_5d`
- `target_trade_label_tier_5d`

`Long` means top-quintile future 5-day return with positive absolute and sector-relative return.

`Short` means bottom-quintile future 5-day return with negative absolute and sector-relative return.

`Neutral` is the default when evidence is insufficient.

`target_trade_label_tier_5d` adds richer target diagnostics without replacing the conservative production label:

- `Strong Long`: top quintile, positive absolute return, and positive sector-relative return.
- `Weak Long`: top quintile plus one of positive absolute or sector-relative return.
- `Strong Short`: bottom quintile, negative absolute return, and negative sector-relative return.
- `Weak Short`: bottom quintile plus one of negative absolute or sector-relative return.
- `Neutral`: otherwise.

`target_vol_adjusted_return_5d` divides sector-relative forward return by realized forward 5-day volatility, producing a Sharpe-like training/diagnostic target.

`target_decay_weighted_return_5d` uses forward daily return weights `[0.50, 0.25, 0.15, 0.07, 0.03]` to reward faster follow-through.

Targets are generated after features and must never be used as model input features.
