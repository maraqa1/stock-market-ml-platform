from stockml.autopilot.action_queue_policy import classify_action_queue_item
from stockml.autopilot.position_health import PositionHealthRules


RULES = PositionHealthRules(max_position_loss_pct=2.0, hard_stop_loss_pct=4.0)


def test_fwrd_like_stale_position_at_loss_threshold_becomes_close_candidate():
    item = {
        "symbol": "FWRD",
        "decision": "watch",
        "decision_reason": "signal_stale",
        "unrealized_plpc": -0.0233,
        "unrealized_pl": -13.61,
    }

    result = classify_action_queue_item(item, held_symbols={"FWRD"}, rules=RULES)

    assert result["decision"] == "close_candidate"
    assert result["operator_call_label"] == "Review close"
    assert result["action_button_label"] == "Review close"
    assert "loss_threshold_breached" in result["position_health_reason"]
    assert "signal_stale" in result["position_health_reason"]


def test_watch_only_rows_get_acknowledge_button_label():
    item = {
        "symbol": "AAA",
        "decision": "watch",
        "decision_reason": "signal_stale",
        "unrealized_plpc": -0.005,
    }

    result = classify_action_queue_item(item, held_symbols={"AAA"}, rules=RULES)

    assert result["decision"] == "watch_loss"
    assert result["operator_call_label"] == "Watch only"
    assert result["action_button_label"] == "Acknowledge"
    assert result["operator_apply_enabled"] is False


def test_close_candidate_rows_get_review_close_label():
    item = {
        "symbol": "AAA",
        "decision": "watch",
        "decision_reason": "latest_signal_unknown",
        "unrealized_plpc": -0.021,
    }

    result = classify_action_queue_item(item, held_symbols={"AAA"}, rules=RULES)

    assert result["decision"] == "close_candidate"
    assert result["operator_call_label"] == "Review close"
    assert result["action_button_label"] == "Review close"


def test_cstl_like_open_candidate_requires_fresh_rescore():
    item = {
        "symbol": "CSTL",
        "side": "long",
        "decision": "open_candidate",
        "decision_reason": "candidate_slot_available",
        "latest_signal_status": "stale",
    }

    result = classify_action_queue_item(item, held_symbols=set(), rules=RULES)

    assert result["operator_call_label"] == "Review open"
    assert result["operator_apply_enabled"] is False
    assert "requires_fresh_rescore" in result["decision_reason"]
    assert result["action_button_label"] == "Review"
