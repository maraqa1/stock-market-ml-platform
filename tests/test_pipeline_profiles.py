from pathlib import Path

import pandas as pd
import pytest

from stockml.common.profiles import load_profile, load_profiles
from stockml.pipeline.profile_runner import _metadata_quality_gate, run_profile


def test_load_pipeline_profiles():
    profiles = load_profiles()
    assert "nasdaq_500" in profiles
    assert profiles["nasdaq_500"]["exchange"] == "NASDAQ"
    assert profiles["nasdaq_500"]["limit_tickers"] == 500
    assert profiles["nasdaq_500"]["publish_trading_artifacts"] is False
    assert "nyse_full" in profiles
    assert profiles["nyse_full"]["exchange"] == "NYSE"
    assert profiles["nyse_full"]["limit_tickers"] is None
    assert profiles["nyse_full"]["provider"] == "eodhd"
    assert profiles["nyse_full"]["metadata_provider"] == "yahoo_legacy"
    assert profiles["nyse_full"]["metadata_fallback_provider"] == "yahoo_legacy"
    assert profiles["nyse_full"]["sentiment_provider"] == "eodhd"
    assert profiles["nyse_full"]["model_shards"] == 4
    assert profiles["nyse_full"]["publish_trading_artifacts"] is False
    assert "us_full" in profiles
    assert profiles["us_full"]["exchanges"] == ["NYSE", "NASDAQ"]
    assert profiles["us_full"]["provider"] == "eodhd"
    assert profiles["us_full"]["metadata_provider"] == "yahoo_legacy"
    assert profiles["us_full"]["model_shards"] == 20
    assert profiles["us_full"]["gold_shard_rows"] == 750000
    assert profiles["us_full"]["skip_gold_sentiment"] is False
    assert profiles["us_full"]["live_signal_mode"] is True
    assert profiles["us_full"]["baseline_only"] is True
    assert profiles["us_full"]["publish_trading_artifacts"] is True
    assert profiles["us_full"]["run_trading_day_readiness"] is True


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
    def fake_metadata(**kwargs):
        calls["metadata"] = kwargs
        return {"metadata_enriched": metadata}

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_metadata_enriched", fake_metadata)
    monkeypatch.setattr("stockml.pipeline.profile_runner._metadata_quality_gate", lambda *args, **kwargs: None)

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
    monkeypatch.setattr(
        "stockml.pipeline.profile_runner.run_trading_day_readiness_gate",
        lambda *args, **kwargs: calls.setdefault("readiness", {"orders_planned": 10, "args": args, "kwargs": kwargs}),
    )

    run_profile("us_full")

    assert calls["metadata"]["provider_name"] == "yahoo_legacy"
    assert calls["metadata"]["fallback_provider_name"] == "yahoo_legacy"
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
    assert calls["model"]["publish_latest"] is True
    assert calls["readiness"]["orders_planned"] == 10
    assert calls["readiness"]["args"]


def test_limited_profile_does_not_publish_latest_trading_artifacts(monkeypatch):
    calls = {}
    validated = Path("validated.csv")
    metadata = Path("metadata.csv")
    features = Path("features.csv")
    gold = Path("gold.csv")

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_us_equity_universe", lambda: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.download_price_history", lambda **kwargs: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_price_quality_report", lambda **kwargs: {"validated_universe": validated})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_metadata_enriched", lambda **kwargs: {"metadata_enriched": metadata})
    monkeypatch.setattr("stockml.pipeline.profile_runner._metadata_quality_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_feature_panel", lambda **kwargs: {"feature_panel": features})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_sentiment_panel", lambda **kwargs: {})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_gold_dataset", lambda **kwargs: {"gold_dataset": gold})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_model_outputs", lambda **kwargs: calls.setdefault("model", kwargs))

    run_profile("nasdaq_500")

    assert calls["model"]["limit_tickers"] == 500
    assert calls["model"]["publish_latest"] is False


def test_profile_runner_can_reuse_existing_artifacts_without_downloads(monkeypatch):
    calls = {}
    universe = Path("universe.csv")
    validated = Path("validated.csv")
    metadata = Path("metadata.csv")
    sentiment = Path("sentiment.csv")
    features = Path("features.csv")
    gold = Path("gold.csv")

    def fake_latest_file(directory, pattern):
        if pattern.startswith("02_us_tradable_universe_"):
            return universe
        if pattern.startswith("03_us_price_validated_universe_"):
            return validated
        if pattern.startswith("04_us_metadata_enriched_"):
            return metadata
        if pattern.startswith("05_news_sentiment_panel_"):
            return sentiment
        raise AssertionError(f"unexpected latest file lookup: {pattern}")

    monkeypatch.setattr("stockml.pipeline.profile_runner.latest_file", fake_latest_file)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_us_equity_universe", lambda: pytest.fail("universe should be reused"))
    monkeypatch.setattr("stockml.pipeline.profile_runner.download_price_history", lambda **kwargs: pytest.fail("price download should be reused"))
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_price_quality_report", lambda **kwargs: pytest.fail("price validation should be reused"))
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_metadata_enriched", lambda **kwargs: pytest.fail("metadata should be reused"))
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_sentiment_panel", lambda **kwargs: pytest.fail("sentiment should be reused"))
    monkeypatch.setattr("stockml.pipeline.profile_runner._metadata_quality_gate", lambda *args, **kwargs: calls.setdefault("metadata_gate", args))

    def fake_features(**kwargs):
        calls["features"] = kwargs
        return {"feature_panel": features}

    def fake_gold(**kwargs):
        calls["gold"] = kwargs
        return {"gold_dataset": gold}

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_feature_panel", fake_features)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_gold_dataset", fake_gold)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_model_outputs", lambda **kwargs: calls.setdefault("model", kwargs))
    monkeypatch.setattr(
        "stockml.pipeline.profile_runner.run_trading_day_readiness_gate",
        lambda *args, **kwargs: calls.setdefault("readiness", {"orders_planned": 10}),
    )

    run_profile("us_full", reuse_existing_artifacts=True)

    assert calls["metadata_gate"][0] == metadata
    assert calls["metadata_gate"][1] == validated
    assert calls["features"]["universe_file"] == validated
    assert calls["features"]["metadata_file"] == metadata
    assert calls["gold"]["feature_file"] == features
    assert calls["gold"]["sentiment_file"] == sentiment
    assert calls["model"]["gold_file"] == gold


def test_profile_runner_can_skip_price_download_but_rebuild_validation(monkeypatch):
    calls = {}
    validated = Path("validated.csv")
    metadata = Path("metadata.csv")
    features = Path("features.csv")
    gold = Path("gold.csv")

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_us_equity_universe", lambda: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.download_price_history", lambda **kwargs: pytest.fail("price download should be skipped"))
    def fake_price_quality(**kwargs):
        calls["price_quality"] = kwargs
        return {"validated_universe": validated}

    monkeypatch.setattr("stockml.pipeline.profile_runner.build_price_quality_report", fake_price_quality)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_metadata_enriched", lambda **kwargs: {"metadata_enriched": metadata})
    monkeypatch.setattr("stockml.pipeline.profile_runner._metadata_quality_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_feature_panel", lambda **kwargs: {"feature_panel": features})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_sentiment_panel", lambda **kwargs: {})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_gold_dataset", lambda **kwargs: {"gold_dataset": gold})
    monkeypatch.setattr("stockml.pipeline.profile_runner.build_model_outputs", lambda **kwargs: calls.setdefault("model", kwargs))
    monkeypatch.setattr("stockml.pipeline.profile_runner.run_trading_day_readiness_gate", lambda *args, **kwargs: {"orders_planned": 10})

    run_profile("us_full", skip_price_download=True)

    assert calls["price_quality"]["provider_name"] == "eodhd"
    assert calls["model"]["gold_file"] == gold


def test_metadata_quality_gate_rejects_missing_market_caps(tmp_path: Path):
    validated = tmp_path / "validated.csv"
    metadata = tmp_path / "metadata.csv"
    pd.DataFrame([{"yahoo_ticker": "AAA"}, {"yahoo_ticker": "BBB"}]).to_csv(validated, index=False)
    pd.DataFrame([{"ticker": "AAA", "market_cap": pd.NA}, {"ticker": "BBB", "market_cap": pd.NA}]).to_csv(metadata, index=False)

    with pytest.raises(RuntimeError, match="metadata_quality_gate_failed"):
        _metadata_quality_gate(metadata, validated, min_validated_coverage=0.75, min_market_cap_coverage=0.70)


def test_metadata_quality_gate_accepts_fallback_market_caps(tmp_path: Path):
    validated = tmp_path / "validated.csv"
    metadata = tmp_path / "metadata.csv"
    pd.DataFrame([{"yahoo_ticker": "AAA"}, {"yahoo_ticker": "BBB"}]).to_csv(validated, index=False)
    pd.DataFrame(
        [
            {"ticker": "AAA", "market_cap": 1_000_000_000},
            {"ticker": "BBB", "market_cap": 2_000_000_000},
        ]
    ).to_csv(metadata, index=False)

    _metadata_quality_gate(metadata, validated, min_validated_coverage=0.75, min_market_cap_coverage=0.70)
