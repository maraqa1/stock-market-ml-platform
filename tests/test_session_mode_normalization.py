from stockml.trading.lifecycle_ids import normalize_lineage, normalize_session_mode


def test_session_mode_aliases_are_normalized():
    assert normalize_session_mode("regular") == ("regular_session", "inconsistent_session_mode")
    assert normalize_session_mode("24x5") == ("overnight_24_5", "inconsistent_session_mode")
    assert normalize_session_mode("overnight") == ("overnight_24_5", "inconsistent_session_mode")
    assert normalize_session_mode("pre_market") == ("pre_market", "")
    assert normalize_session_mode("weekend_closed") == ("weekend_closed", "")


def test_invalid_session_mode_produces_warning_without_fabricating_mode():
    lineage = normalize_lineage({"session_mode": "mystery"})
    assert lineage.values["session_mode"] == ""
    assert lineage.values["lineage_warning"] == "inconsistent_session_mode"
