from stockml.autopilot.position_health import PositionHealthRules, classify_position_health


def test_stale_red_position_becomes_close_candidate():
    result = classify_position_health({"symbol": "AAA", "unrealized_plpc": -0.004, "latest_signal_status": "stale"})

    assert result["position_health_status"] == "close_candidate"
    assert result["position_health_reason"] == "stale_red_position"


def test_unknown_signal_becomes_manual_review():
    result = classify_position_health({"symbol": "AAA", "unrealized_plpc": 0.01, "latest_signal_status": "unknown"})

    assert result["position_health_status"] == "manual_review"
    assert result["position_health_reason"] == "latest_signal_unknown"


def test_hard_stop_becomes_close_now():
    result = classify_position_health({"symbol": "AAA", "unrealized_plpc": -0.041}, PositionHealthRules(hard_stop_loss_pct=4.0))

    assert result["position_health_status"] == "close_now"
    assert result["position_health_reason"] == "hard_stop_hit"


def test_green_winner_becomes_healthy_hold():
    result = classify_position_health({"symbol": "AAA", "unrealized_plpc": 0.02, "latest_signal_status": "fresh"})

    assert result["position_health_status"] == "healthy_hold"
    assert result["position_health_reason"] == "green_position_no_risk_issue"


def test_small_red_above_stop_becomes_watch_loss():
    result = classify_position_health({"symbol": "AAA", "unrealized_plpc": -0.002, "latest_signal_status": "fresh"})

    assert result["position_health_status"] == "watch_loss"
    assert result["position_health_reason"] == "small_red_above_stop"
