# Target Engineering

Gold targets are ranking-first:

- `target_return_5d`
- `target_return_10d`
- `target_sector_relative_return_5d`
- `target_sector_relative_return_10d`
- `target_rank_pct_by_date_5d`
- `target_top_quintile_5d`
- `target_bottom_quintile_5d`
- `target_trade_label_5d`

`Long` means top-quintile future 5-day return with positive absolute and sector-relative return.

`Short` means bottom-quintile future 5-day return with negative absolute and sector-relative return.

`Neutral` is the default when evidence is insufficient.

Targets are generated after features and must never be used as model input features.
