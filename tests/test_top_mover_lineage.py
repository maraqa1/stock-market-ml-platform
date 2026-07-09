from __future__ import annotations

import pandas as pd

from stockml.diagnostics.top_mover_lineage import build_top_mover_lineage, normalize_movers, write_top_mover_lineage


def _frames() -> dict[str, pd.DataFrame]:
    return {
        "universe": pd.DataFrame({"symbol": ["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"]}),
        "price_history": pd.DataFrame({"symbol": ["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"], "close": [10, 20, 100, 30, 40, 50, 100]}),
        "validated_universe": pd.DataFrame({"symbol": ["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"]}),
        "metadata": pd.DataFrame({"symbol": ["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"]}),
        "feature_panel": pd.DataFrame({"symbol": ["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"], "date": ["2026-07-09"] * 7}),
        "gold_v2": pd.DataFrame(
            {
                "symbol": ["MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PRICE"],
                "date": ["2026-07-09"] * 6,
                "close": [20, 100, 30, 40, 50, 100],
                "ticker_direction_bias": ["trust_long", "trust_long", "trust_long", "trust_long", "trust_long", "trust_long"],
                "ticker_direction_sample_count": [60, 60, 60, 60, 60, 60],
            }
        ),
        "model_signal": pd.DataFrame(
            {
                "symbol": ["AXON", "FLEX", "BLOCK", "EXEC", "PRICE"],
                "trade_action": ["No Decision", "No Decision", "Long", "Long", "No Decision"],
                "source_trade_action": ["No Decision", "No Decision", "Long", "Long", "No Decision"],
                "rank_overall": [32, 306, 10, 1, 20],
                "model_score": [0.9, 0.5, 0.8, 0.95, 0.8],
                "meta_label_probability": [0.8, 0.8, 0.8, 0.8, 0.8],
                "ticker_direction_bias": ["trust_long", "trust_long", "trust_long", "trust_long", "trust_long"],
                "ticker_direction_sample_count": [60, 60, 60, 60, 60],
            }
        ),
        "candidate_pool": pd.DataFrame({"symbol": ["BLOCK", "EXEC"], "primary_block_reason": ["risk_gate_failed", ""]}),
        "execution_ranked": pd.DataFrame(
            {
                "symbol": ["BLOCK", "EXEC"],
                "execution_domain": ["blocked_candidate", "execution_candidate"],
                "final_execution_side": ["NONE", "LONG"],
                "primary_block_reason": ["risk_gate_failed", ""],
                "all_block_reasons": ["risk_gate_failed", ""],
            }
        ),
        "order_plan": pd.DataFrame({"symbol": ["EXEC"], "approved_notional": [1000], "suggested_quantity": [10]}),
    }


def test_top_mover_lineage_assigns_root_causes_and_flags_special_cases():
    movers = normalize_movers(["MISS_GOLD", "MISS_MODEL", "AXON", "FLEX", "BLOCK", "EXEC", "PARA", "PRICE"])
    movers.loc[movers["requested_symbol"].eq("FLEX"), "screenshot_direction"] = "up"
    movers.loc[movers["requested_symbol"].eq("PRICE"), "screenshot_price"] = 500

    detail = build_top_mover_lineage(movers, frames=_frames(), alias_map={"PARA": "PSKY"})
    rows = detail.set_index("normalized_symbol")

    assert rows.loc["MISS_GOLD", "root_cause_stage"] == "gold_v2"
    assert rows.loc["MISS_MODEL", "root_cause_stage"] == "model_signal"
    assert rows.loc["AXON", "strong_long_missed_by_source_action"] is True or rows.loc["AXON", "strong_long_missed_by_source_action"] == True
    assert rows.loc["AXON", "recommended_follow_up"] == "investigate_source_trade_action_threshold_or_model_score_missing"
    assert rows.loc["FLEX", "long_mover_memory_aligned_but_no_decision"] is True or rows.loc["FLEX", "long_mover_memory_aligned_but_no_decision"] == True
    assert rows.loc["PARA", "ticker_mapping_status"] == "missing_alias_required"
    assert rows.loc["AXON", "candidate_exclusion_reason"] == "source_trade_action_no_decision"
    assert rows.loc["BLOCK", "root_cause_stage"] == "execution_domain"
    assert rows.loc["EXEC", "root_cause_stage"] == "complete"
    assert rows.loc["PRICE", "price_sanity_status"] == "possible_split_or_scale_issue"


def test_top_mover_lineage_writes_outputs_without_orders(tmp_path):
    movers = normalize_movers(["AXON"])

    output = write_top_mover_lineage(movers, frames=_frames(), output_dir=tmp_path, stamp="20260709_120000")

    assert output.detail_path.exists()
    assert output.summary_path.exists()
    assert "Top Mover Lineage" in output.summary_path.read_text(encoding="utf-8")
    assert not any(path.name.startswith("08_alpaca_paper_order_results") for path in tmp_path.iterdir())
