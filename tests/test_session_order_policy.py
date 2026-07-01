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


def test_after_hours_rejects_missing_quote_timestamp():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 20, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote={"spread_bps": 2},
        requested_order_type="limit",
        config={
            "session_modes": {
                "after_hours": {
                    "enabled": True,
                    "allow_market_orders": False,
                    "max_spread_bps": 10,
                    "position_size_multiplier": 0.25,
                }
            }
        },
    )
    assert decision.allowed is False
    assert decision.session_mode == "after_hours"
    assert decision.session_reject_reason == "quote_timestamp_missing"


def test_after_hours_rejects_quote_reference_price_dislocation():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 20, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote={
            "side": "buy",
            "bid": 142,
            "ask": 155,
            "candidate_reference_price": 143,
            "quote_timestamp": "2026-06-18T20:00:00+00:00",
        },
        requested_order_type="limit",
        config={
            "session_modes": {
                "after_hours": {
                    "enabled": True,
                    "allow_market_orders": False,
                    "max_spread_bps": 2000,
                    "max_executable_deviation_bps": 100,
                    "position_size_multiplier": 0.25,
                }
            }
        },
    )
    assert decision.allowed is False
    assert decision.session_reject_reason == "quote_reference_price_dislocated"
    assert decision.executable_price == 155
    assert decision.reference_price == 143
    assert decision.executable_price_deviation_bps is not None


def test_overnight_mode_allows_wide_spread_only_when_expected_edge_is_strong():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(spread_bps=40, expected_move_bps=250, quote_timestamp="2026-06-18T02:00:00+00:00"),
        requested_order_type="limit",
        config={
            "session_modes": {
                "overnight_24_5": {
                    "enabled": True,
                    "allow_order_submission": True,
                    "allow_market_orders": False,
                    "require_overnight_tradable": True,
                    "require_not_overnight_halted": True,
                    "max_spread_bps": 8,
                    "estimated_cost_bps": 10,
                    "min_edge_to_spread_ratio": 3.0,
                    "min_expected_net_edge_bps": 25,
                    "position_size_multiplier": 0.10,
                }
            }
        },
    )
    assert decision.allowed is True
    assert decision.spread_gate_decision == "wide_spread_edge_supported"
    assert decision.expected_net_edge_bps == 200


def test_overnight_mode_rejects_wide_spread_when_expected_edge_is_weak():
    decision = session_order_policy(
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        asset=_asset(),
        quote=_quote(spread_bps=40, expected_move_bps=80, quote_timestamp="2026-06-18T02:00:00+00:00"),
        requested_order_type="limit",
        config={
            "session_modes": {
                "overnight_24_5": {
                    "enabled": True,
                    "allow_order_submission": True,
                    "allow_market_orders": False,
                    "require_overnight_tradable": True,
                    "require_not_overnight_halted": True,
                    "max_spread_bps": 8,
                    "estimated_cost_bps": 10,
                    "min_edge_to_spread_ratio": 3.0,
                    "min_expected_net_edge_bps": 25,
                    "position_size_multiplier": 0.10,
                }
            }
        },
    )
    assert decision.allowed is False
    assert decision.session_reject_reason == "spread_too_wide"
    assert decision.spread_gate_decision == "wide_spread_edge_insufficient"
