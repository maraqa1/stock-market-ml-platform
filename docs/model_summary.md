# Model Summary

This note is based on a full read of `src/stockml/models/ranking_model.py` and
`src/stockml/models/meta_labeling.py`, with target construction checked against
`src/stockml/models/meta_label_targets.py`.

## Primary Ranking Model

The primary model is built in `train_predict_from_gold()` in
`src/stockml/models/ranking_model.py`. Its target is derived by
`construct_ranking_targets()` from `target_return_5d`, or from a 5-trading-day
forward close-to-close return when that target column is absent.

The actual LightGBM training target is not the raw return itself. The code maps
the cross-sectional 5-day forward return into:

- `target_rank_pct_5d`: within-day percentile rank of `target_return_5d`
- `target_top_quintile_5d`: binary top-quintile marker
- `target_bottom_quintile_5d`: binary bottom-quintile marker
- `target_relevance_5d`: 0 for bottom quintile, 1 for middle, 2 for top quintile

The primary model objective is LambdaRank. `_load_lgbm_ranker()` constructs an
`LGBMRanker` with `objective="lambdarank"` and `metric="ndcg"`.
`_prepare_xy()` feeds the integer `target_relevance_5d` target into that ranker,
with group sizes generated per date by `_group_sizes()`.

The primary output is `model_score`, produced by `ranker.predict()` in
`_train_final_scores()`. In untransformed units this is a LightGBM ranking score:
it is an ordering score used to sort symbols within a cross-section. It is not a
probability, not a calibrated expected return, and not a regression estimate of
5-day return.

The model code also fits an optional helper classifier in `_fit_classifier()`.
That classifier is a scikit-learn `LogisticRegression` trained on
`target_top_quintile_5d`, and its output is written as
`predicted_top_quintile_probability`. Probability language is appropriate for
that helper classifier output only. It is not appropriate for the primary
`model_score` ranker output.

Under no-edge or no-signal conditions, the primary `model_score` should be
interpreted only by relative ordering. Its absolute values do not have a stable
zero, 0.5, or percent-return meaning. If the ranker has no edge, daily rank
correlation with future 5-day returns should be near zero, top-bucket realized
returns should look like the cross-sectional baseline after costs, and the score
distribution may still have arbitrary spread because LambdaRank scores are not
calibrated probabilities.

## Meta-Label Model

The meta-label model is separate from the primary ranker. It is trained by
`train_meta_label_model()` in `src/stockml/models/meta_labeling.py` using a
`HistGradientBoostingClassifier`.

Its target is created in `add_meta_label_targets()` in
`src/stockml/models/meta_label_targets.py`. Rows become trade examples only when
`trade_action` is `Long` or `Short`. The target `meta_label` is 1 when the
directional realized 5-day gain after transaction cost is positive, and 0
otherwise:

- Long: `target_return_5d - cost > 0`
- Short: `-target_return_5d - cost > 0`

`trade_examples()` filters meta-label training to only those historical primary
signals. That means meta-label training rows are a subset of rows from the same
gold-derived history, but they do not include every row used by the primary
ranker. They overlap where the primary system produced a Long or Short signal;
non-trade rows are excluded from the meta-label classifier.

`predict_meta_label_probability()` returns `predict_proba()[:, 1]`, so
probability language is appropriate for `meta_label_probability`. It is a
separate classifier probability of a candidate trade being profitable after
costs, not the primary ranker score.

This model output is best interpreted as: a cross-sectional LambdaRank ordering score, with separate classifier probabilities only where explicitly labeled as probabilities.
