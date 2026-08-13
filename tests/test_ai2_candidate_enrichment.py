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


def test_normalizes_ai2_shortlist_export_columns():
    ai2 = normalize_ai2_enrichment(
        pd.DataFrame(
            [{
                "symbol": "dxcm",
                "execution_decision": "Proceed candidate",
                "latest_eod_date": "2026-08-07",
                "latest_eod_close": 84.75,
                "latest_intraday_price": 83.019996,
                "one_day_return_pct": 2.08,
                "five_day_return_pct": 1.56,
                "eod_volume": 4_505_619,
                "volatility_20d_pct": 3.91,
                "notes": "Clean price-check profile from available EODHD data.; ok:price_checks_clear",
            }]
        ),
        source_file="shortlist.csv",
    )

    row = ai2.iloc[0]
    assert row["symbol"] == "DXCM"
    assert row["ai2_decision_status"] == "proceed"
    assert row["ai2_latest_eod_date"] == "2026-08-07"
    assert row["ai2_latest_eod_close"] == 84.75
    assert row["ai2_latest_intraday_price"] == 83.019996
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
    assert merged.loc[merged["symbol"].eq("ATRC"), "ai2_execution_book"].iloc[0] == "reduced"
    assert merged.loc[merged["symbol"].eq("ATRC"), "ai2_machine_action"].iloc[0] == "ENTER_REDUCED"
    assert merged.loc[merged["symbol"].eq("ATRC"), "ai2_sizing_multiplier"].iloc[0] == 0.35
    assert bool(merged.loc[merged["symbol"].eq("FRPT"), "ai2_auto_open_allowed"].iloc[0]) is False
    assert merged.loc[merged["symbol"].eq("FRPT"), "ai2_block_reason"].iloc[0] == "ai2_refresh_required"
    assert merged.loc[merged["symbol"].eq("FRPT"), "ai2_execution_book"].iloc[0] == "blocked"
    assert merged.loc[merged["symbol"].eq("FRPT"), "ai2_machine_action"].iloc[0] == "REFRESH_AND_RECHECK"


def test_ai2_proceed_is_core_book_full_size():
    merged = apply_ai2_enrichment(
        _base_candidates(),
        pd.DataFrame([{"symbol": "ATRC", "ai2_decision": "Proceed candidate", "ai2_decision_status": "proceed", "ai2_notes": "ok: price_checks_clear"}]),
        config=Ai2EnrichmentConfig(enabled=True),
    )

    row = merged.loc[merged["symbol"].eq("ATRC")].iloc[0]
    assert bool(row["ai2_auto_open_allowed"]) is True
    assert row["ai2_execution_book"] == "core"
    assert row["ai2_machine_action"] == "ENTER"
    assert row["ai2_sizing_multiplier"] == 1.0


def test_ai2_review_large_move_requires_refresh_even_when_review_lane_enabled():
    base = _base_candidates().iloc[[0]].assign(symbol="APPS")
    merged = apply_ai2_enrichment(
        base,
        pd.DataFrame(
            [{
                "symbol": "APPS",
                "ai2_decision": "Review before execution",
                "ai2_decision_status": "review",
                "ai2_return_5d_pct": 20.0,
                "ai2_notes": "warning: large_intraday_move",
            }]
        ),
        config=Ai2EnrichmentConfig(enabled=True, allow_review_for_auto_open=True),
    )

    row = merged.loc[merged["symbol"].eq("APPS")].iloc[0]
    assert bool(row["ai2_auto_open_allowed"]) is False
    assert row["ai2_execution_book"] == "blocked"
    assert row["ai2_machine_action"] == "REFRESH_AND_RECHECK"
    assert row["ai2_block_reason"] == "ai2_refresh_required"


def test_ai2_review_can_only_be_reduced_when_lane_enabled():
    base = _base_candidates().iloc[[0]].assign(symbol="SAFE")
    merged = apply_ai2_enrichment(
        base,
        pd.DataFrame(
            [{
                "symbol": "SAFE",
                "ai2_decision": "Review before execution",
                "ai2_decision_status": "review",
                "ai2_volatility_20d_pct": 8.0,
                "ai2_notes": "warning: high_volatility",
            }]
        ),
        config=Ai2EnrichmentConfig(enabled=True, allow_review_for_auto_open=True),
    )

    row = merged.loc[merged["symbol"].eq("SAFE")].iloc[0]
    assert bool(row["ai2_auto_open_allowed"]) is True
    assert row["ai2_execution_book"] == "reduced"
    assert row["ai2_machine_action"] == "ENTER_REDUCED"
    assert row["ai2_sizing_multiplier"] == 0.25
    assert row["ai2_block_reason"] == ""


def test_ai2_refresh_market_data_phrase_is_refresh_required():
    ai2 = normalize_ai2_enrichment(
        pd.DataFrame(
            [{
                "symbol": "CDNA",
                "execution_decision": "Refresh market data before execution",
            }]
        )
    )

    assert ai2.loc[0, "ai2_decision_status"] == "refresh_required"


def test_ai2_non_execution_decisions_are_classified():
    ai2 = normalize_ai2_enrichment(
        pd.DataFrame(
            [
                {"symbol": "WATCH", "execution_decision": "Research only"},
                {"symbol": "BLOCK", "execution_decision": "Not execution-ready"},
            ]
        )
    )

    assert ai2.set_index("symbol").loc["WATCH", "ai2_decision_status"] == "research_only"
    assert ai2.set_index("symbol").loc["BLOCK", "ai2_decision_status"] == "not_execution_ready"
