from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.evidence_scope import enrich_candidate_evidence_scope, split_candidate_pools
from stockml.candidates.execution_ranker import build_execution_ranked_candidates
from stockml.diagnostics.candidate_evidence_scope import run_candidate_evidence_scope


def _candidate(**overrides):
    row = {
        "raw_rank": 1,
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "Long",
        "trade_action": "Long",
        "trade_quality_status": "approved",
        "approved_notional": 100.0,
        "suggested_quantity": 1,
        "validated_expected_return_bps": 41.8,
        "validated_hit_rate": 0.55,
        "validated_profit_factor": 1.15,
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
    }
    row.update(overrides)
    return row


def test_sample_count_one_inverse_advantage_is_not_actionable():
    frame = pd.DataFrame([
        _candidate(ticker_direction_sample_count=1, ticker_inverse_advantage_bps=200.0, ticker_direction_bias="insufficient_data")
    ])
    out = enrich_candidate_evidence_scope(frame, min_ticker_samples=20)

    assert out.iloc[0]["ticker_direction_memory_status"] == "insufficient_samples"
    assert out.iloc[0]["inverse_warning_status"] == "present_insufficient_samples"
    assert bool(out.iloc[0]["inverse_warning_actionable"]) is False


def test_expected_return_repeated_across_buy_rows_is_marked_side_level():
    frame = pd.DataFrame([_candidate(symbol="AAA"), _candidate(symbol="BBB", raw_rank=2)])
    out = enrich_candidate_evidence_scope(frame)

    assert set(out["expected_return_scope"]) == {"side"}


def test_expected_return_repeated_across_sell_rows_is_marked_side_level():
    frame = pd.DataFrame(
        [
            _candidate(symbol="AAA", side="sell", source_trade_action="Short", trade_action="Short", validated_expected_return_bps=-29.7),
            _candidate(symbol="BBB", side="sell", source_trade_action="Short", trade_action="Short", validated_expected_return_bps=-29.7),
        ]
    )
    out = enrich_candidate_evidence_scope(frame)

    assert set(out["expected_return_scope"]) == {"side"}


def test_research_only_rows_are_separated_from_execution_pool():
    frame = pd.DataFrame(
        [
            {"symbol": "EXEC", "status": "executable", "executable": True, "research_only": False},
            {"symbol": "RESEARCH", "status": "research_only", "executable": False, "research_only": True},
            {"symbol": "WATCH", "status": "watch", "executable": False, "research_only": False},
            {"symbol": "BLOCK", "status": "blocked", "executable": False, "research_only": False},
        ]
    )
    splits = split_candidate_pools(frame)

    assert splits["execution"]["symbol"].tolist() == ["EXEC"]
    assert splits["shadow"]["symbol"].tolist() == ["RESEARCH"]
    assert splits["watch"]["symbol"].tolist() == ["WATCH"]
    assert splits["blocked"]["symbol"].tolist() == ["BLOCK"]


def test_short_candidates_with_negative_expected_return_remain_blocked():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([
            _candidate(
                symbol="SHORT",
                side="sell",
                source_trade_action="Short",
                trade_action="Short",
                ticker_direction_bias="trust_short",
                validated_expected_return_bps=-29.7,
            )
        ])
    )

    row = ranked.iloc[0]
    assert row["status"] == "blocked"
    assert bool(row["research_only"]) is False
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert row["final_execution_side"] == "NONE"
    assert row["direction_decision"] == "direction_block"
    assert row["expected_return_scope"] in {"unknown", "side", "global"}


def test_bny_style_executable_candidate_allowed_but_labelled_missing_ticker_memory():
    ranked = build_execution_ranked_candidates(pd.DataFrame([_candidate(symbol="BNY")]))
    scoped = enrich_candidate_evidence_scope(ranked, min_ticker_samples=20)

    row = scoped.iloc[0]
    assert row["symbol"] == "BNY"
    assert bool(row["executable"]) is False
    assert row["status"] == "research_only"
    assert row["ticker_direction_memory_status"] == "missing"
    assert row["final_execution_side"] == "NONE"


def test_explicit_ticker_scope_is_corrected_when_metric_is_repeated_by_side():
    frame = pd.DataFrame(
        [
            _candidate(symbol="AAA", expected_return_scope="ticker", hit_rate_scope="ticker", profit_factor_scope="ticker"),
            _candidate(symbol="BBB", raw_rank=2, expected_return_scope="ticker", hit_rate_scope="ticker", profit_factor_scope="ticker"),
        ]
    )
    out = build_execution_ranked_candidates(frame)

    assert set(out["expected_return_scope"]) == {"side"}
    assert set(out["hit_rate_scope"]) == {"side"}
    assert set(out["profit_factor_scope"]) == {"side"}


def test_candidate_evidence_scope_runner_writes_outputs(tmp_path: Path):
    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True)
    frame = pd.DataFrame(
        [
            _candidate(symbol="BNY", status="executable", executable=True, research_only=False),
            _candidate(symbol="NOPE", status="research_only", executable=False, research_only=True, source_trade_action="No Decision"),
            _candidate(symbol="BLOCK", status="blocked", executable=False, research_only=False, primary_block_reason="price_below_minimum"),
        ]
    )
    frame.to_csv(portal / "execution_ranked_candidates_20260702_120000.csv", index=False)

    result = run_candidate_evidence_scope(root=tmp_path, stamp="20260702_121500")

    assert result["status"] == "ok"
    assert Path(result["csv_path"]).exists()
    assert Path(result["markdown_path"]).exists()
    assert Path(result["split_paths"]["execution_candidate_pool"]).exists()


def test_candidate_evidence_scope_reports_execution_candidates_with_non_ticker_evidence(tmp_path: Path):
    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True)
    frame = pd.DataFrame(
        [
            {
                **_candidate(symbol="GCT", status="executable", executable=True, research_only=False),
                "execution_domain": "execution_candidate",
                "execution_eligible": True,
                "source_approved_direction": "LONG",
                "final_execution_side": "LONG",
                "order_ready": True,
                "expected_return_scope": "side",
            }
        ]
    )
    frame.to_csv(portal / "execution_ranked_candidates_20260702_120000.csv", index=False)

    result = run_candidate_evidence_scope(root=tmp_path, stamp="20260702_121500")

    assert result["execution_non_ticker_evidence_count"] == 1
    text = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert "Execution candidates using non-ticker expected-return evidence: `1`" in text
    assert "symbol=GCT" in text
