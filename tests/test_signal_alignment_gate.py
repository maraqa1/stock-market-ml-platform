from stockml.trading.signal_alignment_gate import evaluate_entry_signal_alignment


def test_new_entry_blocked_when_latest_signal_unknown():
    result = evaluate_entry_signal_alignment({"side": "buy", "latest_signal_status": "unknown"})

    assert result.allowed is False
    assert result.reason == "latest_signal_unknown_blocks_entry"


def test_new_entry_blocked_when_stale():
    result = evaluate_entry_signal_alignment({"side": "buy", "latest_signal_status": "stale"})

    assert result.allowed is False
    assert result.reason == "stale_signal_blocks_entry"


def test_new_entry_allowed_when_signal_fresh_and_aligned():
    result = evaluate_entry_signal_alignment(
        {
            "side": "sell",
            "latest_signal_status": "fresh",
            "latest_signal_direction": "short",
            "model_status": "decision_grade",
        }
    )

    assert result.allowed is True
    assert result.reason == ""


def test_new_entry_blocked_when_signal_direction_mismatches():
    result = evaluate_entry_signal_alignment(
        {
            "side": "buy",
            "latest_signal_status": "fresh",
            "latest_signal_direction": "short",
            "model_status": "decision_grade",
        }
    )

    assert result.allowed is False
    assert result.reason == "signal_direction_mismatch"


def test_new_entry_blocked_when_model_not_decision_grade():
    result = evaluate_entry_signal_alignment(
        {
            "side": "buy",
            "latest_signal_status": "fresh",
            "latest_signal_direction": "long",
            "model_status": "diagnostic_only",
        }
    )

    assert result.allowed is False
    assert result.reason == "model_not_decision_grade"
