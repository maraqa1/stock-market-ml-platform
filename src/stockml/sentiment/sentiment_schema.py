from __future__ import annotations

SENTIMENT_COLUMNS = [
    "date",
    "ticker",
    "article_count",
    "sentiment_score_mean",
    "sentiment_score_min",
    "sentiment_score_max",
    "sentiment_positive_count",
    "sentiment_negative_count",
    "sentiment_neutral_count",
    "sentiment_source",
    "sentiment_status",
]

SENTIMENT_GOLD_COLUMNS = SENTIMENT_COLUMNS + [
    "sentiment_momentum_3d",
    "sentiment_momentum_7d",
    "sentiment_volume_spike_flag",
    "news_attention_score",
]

