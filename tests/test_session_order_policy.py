from __future__ import annotations

from datetime import datetime, timezone

from stockml.trading.session_order_policy import session_order_policy


def _asset(**updates):
    out = {"tradable": True, "status": "active", "overnight_tradable": True, "overnight_halted": False}
    out.update(updates)
    return out


def _quote(**updates):
    out = {"spread_bps": 2, "quote_timestamp": "2026-06-18T02:00:00+00:00"}
    out.update(updates)
    return out


def test_overnight_mode_rejects_market_orders():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(),
        requested_order_type="market",
    )
    assert decision.allowed is False
    assert decision.session_mode == "overnight_24_5"
    assert decision.session_reject_reason == "market_orders_not_allowed"


def test_overnight_mode_accepts_limit_for_overnight_tradable_asset():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(),
        requested_order_type="limit",
    )
    assert decision.allowed is True
    assert decision.order_type == "limit"
    assert decision.extended_hours is True
    assert decision.size_multiplier == 0.10


def test_overnight_mode_rejects_non_overnight_tradable_asset():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        asset=_asset(overnight_tradable=False),
        quote=_quote(),
        requested_order_type="limit",
    )
    assert decision.allowed is False
    assert decision.session_reject_reason == "asset_not_overnight_tradable"


def test_weekend_mode_rejects_broker_submission():
    decision = session_order_policy(
        now=datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(),
        requested_order_type="limit",
    )
    assert decision.allowed is False
    assert decision.session_mode == "weekend_closed"
    assert decision.session_reject_reason == "session_order_submission_disabled"


def test_regular_session_allows_market_order_normal_size():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 14, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(quote_timestamp="2026-06-18T14:00:00+00:00"),
        requested_order_type="market",
    )
    assert decision.allowed is True
    assert decision.session_mode == "regular_session"
    assert decision.order_type == "market"
    assert decision.extended_hours is False
    assert decision.size_multiplier == 1.0
