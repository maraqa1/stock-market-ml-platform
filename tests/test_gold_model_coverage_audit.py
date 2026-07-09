from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.gold_model_coverage_audit import build_gold_model_coverage_audit, write_gold_model_coverage_audit


def _csv(path: Path, symbols: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": symbols}).to_csv(path, index=False)
    return path


def test_gold_model_coverage_audit_counts_missing_symbols(tmp_path):
    paths = {
        "universe": _csv(tmp_path / "universe.csv", ["AAA", "BBB", "CCC", "AXON"]),
        "price_history": _csv(tmp_path / "price.csv", ["AAA", "BBB", "CCC", "AXON"]),
        "validated_universe": _csv(tmp_path / "validated.csv", ["AAA", "BBB", "AXON"]),
        "metadata": _csv(tmp_path / "metadata.csv", ["AAA", "BBB", "AXON"]),
        "feature_panel": _csv(tmp_path / "features.csv", ["AAA", "BBB", "AXON"]),
        "gold_v2": _csv(tmp_path / "gold.csv", ["AAA", "AXON"]),
        "model_signal": _csv(tmp_path / "signal.csv", ["AXON"]),
        "candidate_pool": _csv(tmp_path / "candidates.csv", ["AXON"]),
    }

    audit = build_gold_model_coverage_audit(paths=paths, mover_symbols={"AAA", "BBB", "AXON", "MISS"})
    row = audit.iloc[0]

    assert row["tradable_universe_count"] == 4
    assert row["gold_v2_count"] == 2
    assert row["model_signal_count"] == 1
    assert row["missing_from_gold_count"] == 2
    assert row["missing_from_model_signal_count"] == 1
    assert "BBB" in row["top_movers_missing_from_gold"]
    assert "AAA" in row["top_movers_missing_from_model"]


def test_gold_model_coverage_audit_writes_outputs(tmp_path):
    paths = {
        "universe": _csv(tmp_path / "universe.csv", ["AAA"]),
        "gold_v2": _csv(tmp_path / "gold.csv", []),
        "model_signal": _csv(tmp_path / "signal.csv", []),
    }

    output = write_gold_model_coverage_audit(paths=paths, mover_symbols={"AAA"}, output_dir=tmp_path, stamp="20260709_120000")

    assert output.csv_path.exists()
    assert output.summary_path.exists()
    assert "Gold / Model Coverage Audit" in output.summary_path.read_text(encoding="utf-8")
