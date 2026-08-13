from stockml.trading_brain_v2.autopilot.ap_b06_refresh_gate import RefreshGateBlock
from stockml.trading_brain_v2.autopilot.ap_b07_tradability_gate import TradabilityGateBlock
from stockml.trading_brain_v2.shared.config import TradingBrainConfig
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction


def _candidate(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "rank": 1,
        "candidate_status": "executable",
        "ai2_status": "proceed",
        "decision_label": "Proceed candidate",
        "approved_notional": 250.0,
        "qty": 6.0,
        "risk_class": "medium",
        "latest_eod_date": "2026-08-06",
        "close_price": 100.0,
        "expected_return_bps": 31.8,
        "one_day_return": 0.01,
        "five_day_return": 0.02,
        "twenty_day_volatility": 0.03,
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


def test_refresh_required_candidate_triggers_refresh_and_recheck():
    decision = RefreshGateBlock().evaluate_candidate(_candidate(ai2_status="refresh_required"), live_price=100.0)

    assert decision.decision == EntryAction.REFRESH_AND_RECHECK.value
    assert decision.reason == "ai2_refresh_required"


def test_refresh_gate_blocks_six_percent_live_gap():
    decision = RefreshGateBlock().evaluate_candidate(_candidate(), live_price=106.0)

    assert decision.decision == EntryAction.BLOCK.value
    assert decision.reason == "live_price_gap_block"


def test_refresh_gate_refreshes_three_percent_live_gap():
    decision = RefreshGateBlock().evaluate_candidate(_candidate(), live_price=103.0)

    assert decision.decision == EntryAction.REFRESH_AND_RECHECK.value
    assert decision.reason == "live_price_gap_refresh"


def test_refresh_gate_clean_candidate_passes():
    decision = RefreshGateBlock().evaluate_candidate(
        _candidate(),
        live_price=101.0,
        expected_latest_eod_date="2026-08-06",
    )

    assert decision.decision == "PASS"
    assert decision.reason == "refresh_gate_pass"


def test_refresh_gate_stale_eod_refreshes():
    decision = RefreshGateBlock().evaluate_candidate(
        _candidate(latest_eod_date="2026-08-05"),
        live_price=100.0,
        expected_latest_eod_date="2026-08-06",
    )

    assert decision.decision == EntryAction.REFRESH_AND_RECHECK.value
    assert decision.reason == "latest_eod_stale"


def test_tradability_gate_missing_live_price_blocks():
    decision = TradabilityGateBlock().evaluate_candidate(_candidate(), market_snapshot={})

    assert decision.decision == EntryAction.BLOCK.value
    assert decision.reason == "live_price_missing"


def test_tradability_gate_zero_live_price_blocks():
    decision = TradabilityGateBlock().evaluate_candidate(_candidate(), market_snapshot={"live_price": 0})

    assert decision.decision == EntryAction.BLOCK.value
    assert decision.reason == "live_price_non_positive"


def test_tradability_gate_halted_candidate_blocks():
    decision = TradabilityGateBlock().evaluate_candidate(_candidate(), market_snapshot={"live_price": 100.0, "halted": True})

    assert decision.decision == EntryAction.BLOCK.value
    assert decision.reason == "halted"


def test_tradability_gate_broker_not_tradable_blocks():
    decision = TradabilityGateBlock().evaluate_candidate(_candidate(), market_snapshot={"live_price": 100.0, "tradable": False})

    assert decision.decision == EntryAction.BLOCK.value
    assert decision.reason == "broker_not_tradable"


def test_tradability_gate_clean_candidate_passes():
    decision = TradabilityGateBlock().evaluate_candidate(_candidate(), market_snapshot={"live_price": 100.0, "tradable": True})

    assert decision.decision == "PASS"
    assert decision.reason == "tradability_gate_pass"


def test_tradability_gate_low_volume_uses_policy_action():
    decision = TradabilityGateBlock().evaluate_candidate(
        _candidate(),
        market_snapshot={"live_price": 100.0, "volume": 10},
        config=TradingBrainConfig(min_volume=1000, low_volume_action=EntryAction.ENTER_REDUCED.value),
    )

    assert decision.decision == EntryAction.ENTER_REDUCED.value
    assert decision.reason == "volume_below_minimum_reduced"
