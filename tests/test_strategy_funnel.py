from stockml.strategy.strategy_funnel import _stage


def test_strategy_funnel_stage_rates_are_stable():
    row = _stage("raw_candidates", 10, 4, reasons=["a", "a", "b"])
    assert row["failed_count"] == 6
    assert row["pass_rate"] == 0.4
    assert row["failure_rate"] == 0.6
    assert row["top_failure_reasons"].startswith("a")
