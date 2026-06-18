from __future__ import annotations

from stockml.autopilot.position_lifecycle_guard import evaluate_exit_request


def test_stale_signal_alone_does_not_close():
    result = evaluate_exit_request({"symbol": "AAA", "unrealized_plpc": 0.01}, reason="signal_stale")
    assert result == {"allowed": False, "reason": "stale_signal_not_exit_reason"}


def test_unknown_signal_alone_does_not_close():
    result = evaluate_exit_request({"symbol": "AAA", "unrealized_plpc": 0.01}, reason="latest_signal_unknown")
    assert result == {"allowed": False, "reason": "unknown_signal_not_exit_reason"}


def test_defensive_close_requires_loss_or_risk_breach():
    result = evaluate_exit_request({"symbol": "AAA", "unrealized_plpc": 0.02}, reason="defensive_close")
    assert result == {"allowed": False, "reason": "defensive_close_requires_loss_or_risk_breach"}


def test_defensive_close_allowed_with_loss():
    result = evaluate_exit_request({"symbol": "AAA", "unrealized_plpc": -0.03}, reason="defensive_close")
    assert result == {"allowed": True, "reason": "allowed"}


def test_defensive_close_allowed_with_risk_breach():
    result = evaluate_exit_request({"symbol": "AAA", "basket_risk_breach": True}, reason="defensive_close")
    assert result == {"allowed": True, "reason": "allowed"}
