from __future__ import annotations

from datetime import datetime, timezone, timedelta

from stockml.trading.overnight_quote_quality import evaluate_quote_quality


def test_quote_quality_computes_spread_from_bid_ask():
    result = evaluate_quote_quality({"bid": 99.95, "ask": 100.05}, max_spread_bps=20)
    assert result.ok is True
    assert 9.0 < result.spread_bps < 11.0


def test_quote_quality_rejects_wide_spread():
    result = evaluate_quote_quality({"bid": 99.0, "ask": 101.0}, max_spread_bps=8)
    assert result.ok is False
    assert result.reason == "spread_too_wide"


def test_quote_quality_rejects_stale_quote():
    now = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
    result = evaluate_quote_quality(
        {"spread_bps": 2, "quote_timestamp": now - timedelta(minutes=30)},
        max_spread_bps=8,
        max_freshness_seconds=900,
        now=now,
    )
    assert result.ok is False
    assert result.reason == "quote_stale"


def test_quote_quality_rejects_missing_timestamp_when_fresh_quote_required():
    result = evaluate_quote_quality(
        {"spread_bps": 2},
        max_spread_bps=8,
        require_fresh_quote=True,
    )
    assert result.ok is False
    assert result.reason == "quote_timestamp_missing"
