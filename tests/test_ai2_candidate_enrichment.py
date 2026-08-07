from __future__ import annotations

import pandas as pd

from stockml.ai2.candidate_enrichment import Ai2EnrichmentConfig, apply_ai2_enrichment, normalize_ai2_enrichment


def _base_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "ATRC",
                "execution_rank": 1,
                "status": "executable",
                "executable": True,
                "order_eligible": True,
                "order_ready": True,
                "execution_domain": "execution_candidate",
                "final_execution_side": "LONG",
            },
            {
                "symbol": "JUNK",
                "execution_rank": "",
                "status": "blocked",
                "executable": False,
                "order_eligible": False,
                "order_ready": False,
                "execution_domain": "blocked_candidate",
                "final_execution_side": "NONE",
            },
        ]
    )


def test_normalizes_ai2_chat_report_columns():
    ai2 = normalize_ai2_enrichment(
        pd.DataFrame(
            [{
                "Symbol": " atrc ",
                "Decision": "Proceed candidate",
                "Latest EOD date/close": "2026-08-06 / 39.49",
                "Latest intraday": "40.12",
                "1D return": "1.2%",
                "5D return": "-0.5%",
                "EOD volume": "753,722",
                "20D vol.": "3.2%",
                "Why / notes": "ok: price_checks_clear",
            }]
        ),
        source_file="ai2.csv",
    )

    row = ai2.iloc[0]
    assert row["symbol"] == "ATRC"
    assert row["ai2_decision_status"] == "proceed"
    assert row["ai2_latest_eod_date"] == "2026-08-06"
    assert row["ai2_latest_eod_close"] == 39.49
    assert row["ai2_latest_intraday_price"] == 40.12
    assert row["ai2_price_check_status"] == "clean"


def test_ai2_bridge_disabled_is_inert():
    merged = apply_ai2_enrichment(
        _base_candidates(),
        pd.DataFrame([{"symbol": "ATRC", "ai2_decision": "Proceed candidate", "ai2_decision_status": "proceed"}]),
    )

    assert bool(merged.loc[merged["symbol"].eq("ATRC"), "executable"].iloc[0]) is True
    assert bool(merged.loc[merged["symbol"].eq("ATRC"), "ai2_auto_open_allowed"].iloc[0]) is False
    assert merged.loc[merged["symbol"].eq("ATRC"), "ai2_block_reason"].iloc[0] == "ai2_bridge_disabled"


def test_ai2_proceed_can_only_confirm_existing_stockml_executable_candidate():
    merged = apply_ai2_enrichment(
        _base_candidates(),
        pd.DataFrame(
            [
                {"symbol": "ATRC", "ai2_decision": "Proceed candidate", "ai2_decision_status": "proceed"},
                {"symbol": "JUNK", "ai2_decision": "Proceed candidate", "ai2_decision_status": "proceed"},
                {"symbol": "NEW", "ai2_decision": "Proceed candidate", "ai2_decision_status": "proceed"},
            ]
        ),
        config=Ai2EnrichmentConfig(enabled=True),
    )

    assert bool(merged.loc[merged["symbol"].eq("ATRC"), "ai2_auto_open_allowed"].iloc[0]) is True
    assert bool(merged.loc[merged["symbol"].eq("JUNK"), "ai2_auto_open_allowed"].iloc[0]) is False
    assert merged.loc[merged["symbol"].eq("JUNK"), "ai2_block_reason"].iloc[0] == "stockml_execution_gate_not_passed"
    assert "NEW" not in set(merged["symbol"])


def test_ai2_review_and_refresh_do_not_auto_open_by_default():
    base = pd.concat([_base_candidates().iloc[[0]], _base_candidates().iloc[[0]].assign(symbol="FRPT")])
    merged = apply_ai2_enrichment(
        base,
        pd.DataFrame(
            [
                {"symbol": "ATRC", "ai2_decision": "Review before execution", "ai2_decision_status": "review"},
                {"symbol": "FRPT", "ai2_decision": "Do not execute until refreshed", "ai2_decision_status": "refresh_required"},
            ]
        ),
        config=Ai2EnrichmentConfig(enabled=True),
    )

    assert bool(merged.loc[merged["symbol"].eq("ATRC"), "ai2_auto_open_allowed"].iloc[0]) is False
    assert merged.loc[merged["symbol"].eq("ATRC"), "ai2_block_reason"].iloc[0] == "ai2_review_required"
    assert bool(merged.loc[merged["symbol"].eq("FRPT"), "ai2_auto_open_allowed"].iloc[0]) is False
    assert merged.loc[merged["symbol"].eq("FRPT"), "ai2_block_reason"].iloc[0] == "ai2_refresh_required"
