from stockml.trading_brain_v2.autopilot.ap_b08_risk_scoring_engine import RiskScoringEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b09_position_sizing_engine import PositionSizingEngineBlock
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction


def _candidate(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "rank": 1,
        "candidate_status": "executable",
        "ai2_status": "proceed",
        "decision_label": "Proceed candidate",
        "approved_notional": 1000.0,
        "qty": 0.0,
        "risk_class": "medium",
        "latest_eod_date": "2026-08-06",
        "close_price": 100.0,
        "expected_return_bps": 31.8,
        "one_day_return": 0.01,
        "five_day_return": 0.05,
        "twenty_day_volatility": 0.02,
        "eod_volume": 750000.0,
        "price_check_clear": True,
        "warning_codes": ("price_checks_clear",),
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
        "source_file": "candidate.csv",
    }
    values.update(overrides)
    return Candidate(**values)


def test_risk_scoring_proceed_medium_risk_normal_volatility():
    profile = RiskScoringEngineBlock().score_candidate(_candidate(), live_price=100.0)

    assert profile.status_multiplier == 1.0
    assert profile.risk_class_multiplier == 0.75
    assert profile.volatility_multiplier == 1.0
    assert profile.momentum_multiplier == 1.0
    assert profile.final_risk_multiplier == 0.75
    assert profile.risk_tier == "normal"


def test_review_high_volatility_is_reduced():
    profile = RiskScoringEngineBlock().score_candidate(
        _candidate(ai2_status="review", risk_class="high", twenty_day_volatility=0.08),
        live_price=100.0,
    )

    assert profile.status_multiplier == 0.35
    assert profile.risk_class_multiplier == 0.25
    assert profile.volatility_multiplier == 0.25
    assert profile.final_risk_multiplier > 0
    assert profile.risk_tier == "minimal"


def test_volatility_above_nine_percent_blocks():
    profile = RiskScoringEngineBlock().score_candidate(_candidate(twenty_day_volatility=0.091), live_price=100.0)
    size = PositionSizingEngineBlock().size_candidate(_candidate(twenty_day_volatility=0.091), live_price=100.0, risk_profile=profile)

    assert profile.volatility_multiplier == 0.0
    assert profile.risk_tier == "blocked"
    assert size.decision == EntryAction.BLOCK.value


def test_five_day_return_above_thirty_percent_blocks():
    profile = RiskScoringEngineBlock().score_candidate(_candidate(five_day_return=0.31), live_price=100.0)
    size = PositionSizingEngineBlock().size_candidate(_candidate(five_day_return=0.31), live_price=100.0, risk_profile=profile)

    assert profile.momentum_multiplier == 0.0
    assert profile.risk_tier == "blocked"
    assert size.decision == EntryAction.BLOCK.value


def test_high_quality_candidate_gets_larger_allocation_than_medium_candidate():
    block = PositionSizingEngineBlock()
    high_quality = _candidate(risk_class="high_quality")
    medium = _candidate(risk_class="medium")

    high_quality_size = block.size_candidate(high_quality, live_price=100.0)
    medium_size = block.size_candidate(medium, live_price=100.0)

    assert high_quality_size.final_notional == 1000.0
    assert medium_size.final_notional == 750.0
    assert high_quality_size.final_notional > medium_size.final_notional


def test_integer_sizing_produces_expected_quantity():
    decision = PositionSizingEngineBlock().size_candidate(_candidate(approved_notional=1000.0, close_price=121.0), live_price=121.0)

    assert decision.final_notional == 750.0
    assert decision.qty == 6
    assert decision.decision == EntryAction.ENTER.value
