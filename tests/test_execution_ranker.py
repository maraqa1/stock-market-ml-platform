from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates, write_execution_ranked_candidates
from stockml.candidates.short_side_policy import ShortSidePolicy


def _row(
    symbol: str,
    rank: int,
    *,
    action: str = "Long",
    status: str = "approved",
    reason: str = "",
    side: str | None = None,
    expected_quality: str = "calibrated",
    **overrides,
) -> dict:
    row = {
        "symbol": symbol,
        "rank_overall": rank,
        "trade_action": action,
        "source_trade_action": action,
        "side": side or ("sell" if action == "Short" else "buy"),
        "trade_quality_status": status,
        "trade_quality_reason": reason,
        "expected_return_quality": expected_quality,
        "calibration_quality": "usable" if expected_quality == "calibrated" else expected_quality,
        "validated_expected_return_bps": 42.0,
        "validated_hit_rate": 0.57,
        "validated_profit_factor": 1.8,
        "ticker_direction_bias": "trust_long" if action != "Short" else "trust_short",
        "ticker_direction_sample_count": 100,
        "risk_tier": "high_quality",
        "volatility_tier": "normal",
        "approved_notional": 100.0,
        "suggested_quantity": 1,
    }
    row.update(overrides)
    return row


def test_approved_long_receives_first_execution_rank_after_rejected_raw_rank_one():
    frame = pd.DataFrame([
        _row("ICCM", 1, status="rejected", reason="price_gate_failed"),
        _row("BNY", 28),
    ])
    ranked = build_execution_ranked_candidates(frame, short_policy=ShortSidePolicy())
    bny = ranked[ranked["symbol"].eq("BNY")].iloc[0]
    iccm = ranked[ranked["symbol"].eq("ICCM")].iloc[0]
    assert bny["execution_rank"] == 1
    assert iccm["status"] == "blocked"
    assert pd.isna(iccm["execution_rank"])


def test_no_decision_never_receives_execution_rank():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([_row("AAA", 1, action="No Decision", side="", status="approved")]),
        short_policy=ShortSidePolicy(),
    )
    assert ranked.iloc[0]["status"] == "research_only"
    assert ranked.iloc[0]["primary_block_reason"] in {"source_trade_action_not_executable", "planner_derived_action_without_source_approval"}
    assert pd.isna(ranked.iloc[0]["execution_rank"])


def test_uncalibrated_expected_return_never_receives_execution_rank():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([_row("AAA", 1, expected_quality="uncalibrated")]),
        short_policy=ShortSidePolicy(),
    )
    assert ranked.iloc[0]["status"] == "blocked"
    assert "expected_return_uncalibrated" in ranked.iloc[0]["all_block_reasons"]
    assert pd.isna(ranked.iloc[0]["execution_rank"])


def test_short_candidate_becomes_research_only_when_policy_disabled():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([_row("CRCL", 1, action="Short")]),
        short_policy=ShortSidePolicy(enabled=False),
    )
    row = ranked.iloc[0]
    assert row["status"] == "research_only"
    assert bool(row["research_only"]) is True
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert pd.isna(row["execution_rank"])


def test_short_candidate_executable_when_policy_enabled_and_gates_pass():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([_row("CRCL", 1, action="Short")]),
        short_policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True),
    )
    row = ranked.iloc[0]
    assert row["status"] == "executable"
    assert row["execution_rank"] == 1


def test_raw_rank_is_preserved_and_execution_rank_is_stable():
    frame = pd.DataFrame([_row("ZZZ", 5), _row("AAA", 5), _row("BBB", 3)])
    ranked = build_execution_ranked_candidates(frame, short_policy=ShortSidePolicy())
    assert ranked["raw_rank"].tolist() == [5, 5, 3]
    ordered = ranked.sort_values("execution_rank", kind="mergesort")["symbol"].tolist()
    assert ordered == ["BBB", "AAA", "ZZZ"]


def test_qualified_volatility_opportunity_reduced_long_receives_execution_rank():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame(
            [
                _row(
                    "CRNX",
                    3,
                    status="reduced",
                    reason="reduced",
                    volatility_tier="extreme",
                    risk_tier="speculative",
                    volatility_opportunity_status="qualified_reduced",
                    volatility_opportunity_reason="volatility_extreme_offset_by_validated_edge",
                    volatility_opportunity_allows_reduced_trade=True,
                )
            ]
        ),
        short_policy=ShortSidePolicy(),
    )

    row = ranked.iloc[0]
    assert row["status"] == "executable"
    assert row["execution_domain"] == "execution_candidate"
    assert row["execution_rank"] == 1
    assert row["all_block_reasons"] == ""
    assert row["volatility_opportunity_status"] == "qualified_reduced"


def test_writer_outputs_expected_schema(tmp_path: Path):
    path = write_execution_ranked_candidates(
        pd.DataFrame([_row("BNY", 1)]),
        output_dir=tmp_path,
        stamp="20260701_120000",
        short_policy=ShortSidePolicy(),
    )
    out = pd.read_csv(path)
    assert path.name == "execution_ranked_candidates_20260701_120000.csv"
    assert {"raw_rank", "execution_rank", "symbol", "status", "primary_block_reason"}.issubset(out.columns)


def test_execution_ranker_does_not_add_live_trading_path():
    source = Path("src/stockml/candidates/execution_ranker.py").read_text(encoding="utf-8")
    assert "submit_order" not in source
    assert "live_trading" not in source
