from pathlib import Path

import pytest

from stockml.common.profiles import load_profile, load_profiles
from stockml.pipeline.profile_runner import run_profile


def test_load_pipeline_profiles():
    profiles = load_profiles()
    assert "nasdaq_500" in profiles
    assert profiles["nasdaq_500"]["exchange"] == "NASDAQ"
    assert profiles["nasdaq_500"]["limit_tickers"] == 500
    assert "nyse_full" in profiles
    assert profiles["nyse_full"]["exchange"] == "NYSE"
    assert profiles["nyse_full"]["limit_tickers"] is None
    assert profiles["nyse_full"]["provider"] == "eodhd"
    assert profiles["nyse_full"]["metadata_fallback_provider"] == "yahoo_legacy"
    assert profiles["nyse_full"]["sentiment_provider"] == "eodhd"
    assert profiles["nyse_full"]["model_shards"] == 4
    assert "us_full" in profiles
    assert profiles["us_full"]["exchanges"] == ["NYSE", "NASDAQ"]
    assert profiles["us_full"]["provider"] == "eodhd"
    assert profiles["us_full"]["model_shards"] == 20
    assert profiles["us_full"]["gold_shard_rows"] == 750000
    assert profiles["us_full"]["skip_gold_sentiment"] is False
    assert profiles["us_full"]["live_signal_mode"] is True
    assert profiles["us_full"]["baseline_only"] is True


def test_unknown_profile_has_clear_error():
    with pytest.raises(KeyError, match="Unknown profile"):
        load_profile("does_not_exist")


def test_profile_runner_passes_same_run_artifacts(monkeypatch):
    calls = {}
    validated = Path("validated.csv")
    metadata = Path("metadata.csv")
    features = Path("features.csv")
    sentiment = Path("sentiment.csv")
    gold = Path("gold.csv")

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_us_equity_universe", lambda: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.download_price_history", lambda **kwargs: calls.setdefault("price", kwargs))
    monkeypatch.setattr(
        "stockml.pipeline.profile_runner.build_price_quality_report",
        lambda **kwargs: {"validated_universe": validated},
    )
    monkeypatch.setattr(
        "stockml.pipeline.profile_runner.build_metadata_enriched",
        lambda **kwargs: {"metadata_enriched": metadata},
    )

    def fake_features(**kwargs):
        calls["features"] = kwargs
        return {"feature_panel": features}

    def fake_sentiment(**kwargs):
        calls["sentiment"] = kwargs
        return {"sentiment_panel": sentiment}

    def fake_gold(**kwargs):
        calls["gold"] = kwargs
        return {"gold_dataset": gold}

    def fake_model(**kwargs):
        calls["model"] = kwargs
        return {}

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_feature_panel", fake_features)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_sentiment_panel", fake_sentiment)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_gold_dataset", fake_gold)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_model_outputs", fake_model)

    run_profile("us_full")

    assert calls["features"]["universe_file"] == validated
    assert calls["features"]["metadata_file"] == metadata
    assert calls["gold"]["feature_file"] == features
    assert calls["gold"]["sentiment_file"] == sentiment
    assert calls["gold"]["shard_rows"] == 750000
    assert calls["gold"]["skip_sentiment"] is False
    assert calls["model"]["gold_file"] == gold
    assert calls["model"]["model_shards"] == 20
    assert calls["model"]["live_signal_mode"] is True
    assert calls["model"]["baseline_only"] is True
