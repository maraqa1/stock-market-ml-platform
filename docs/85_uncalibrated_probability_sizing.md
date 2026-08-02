# Uncalibrated Probability Sizing Audit

The execution-ranked candidate layer keeps probability calibration separate from validation quality.

- `probability_calibration_status=uncalibrated` means `calibrated_probability_win` is empty and raw side scores are not treated as win probabilities.
- `probability_usable_for_sizing=false` means the execution-ranked layer must not size from probability.
- `sizing_probability_source` records whether a row is using `calibrated_probability_win`, `config_default`, or `fixed_size` semantics.
- `ranking_score_source=net_expected_return_bps` records that execution rank is ordered by validated expected return after estimated cost.
- `raw_side_score_used_for_sizing=false` and `raw_side_score_used_for_ranking=false` are explicit audit fields.

This pairs with the evidence-scope diagnostic: side-level expected returns may rank candidates, but they must be labeled as side-level evidence and not displayed as ticker-specific forecasts.

Important caveat: legacy trade-quality sizing still receives `side_probability` in its input schema. Ticket 6 does not change that historical sizing formula, because doing so would alter exposure. It makes the execution-ranked path auditable and confirms current executable candidates are not promoted or ranked from raw side probability.
