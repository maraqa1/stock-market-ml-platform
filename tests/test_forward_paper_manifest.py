from __future__ import annotations

import csv
import json
from pathlib import Path

from stockml.trading.forward_paper_manifest import write_forward_paper_manifest


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_forward_paper_manifest_writes_even_without_trades(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.trading.forward_paper_manifest.PROJECT_ROOT", tmp_path)
    _write(tmp_path / "config" / "trading.yaml", "alpaca: {}\n")
    manifest = _write(
        tmp_path / "data" / "pipeline_runs" / "run-1" / "manifest.json",
        json.dumps({"run_id": "run-1", "status": "ok"}),
    )

    result = write_forward_paper_manifest(root=tmp_path, pipeline_manifest_path=manifest, pipeline_run_id="run-1", run_date="20260717")

    assert Path(result["path"]).exists()
    with Path(result["path"]).open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["pipeline_run_id"] == "run-1"
    assert row["live_trading_enabled"] == "False"
    assert row["material_change_flag"] == "false"


def test_forward_paper_manifest_detects_config_change(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.trading.forward_paper_manifest.PROJECT_ROOT", tmp_path)
    config = _write(tmp_path / "config" / "trading.yaml", "a: 1\n")

    first = write_forward_paper_manifest(root=tmp_path, run_date="20260717")
    config.write_text("a: 2\n", encoding="utf-8")
    second = write_forward_paper_manifest(root=tmp_path, run_date="20260718")

    assert first["config_hash"] != second["config_hash"]
    assert second["material_change_flag"] == "true"
    assert second["paper_program_status"] == "segmented_by_material_change"


def test_forward_paper_manifest_adds_execution_integrity_metrics(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.trading.forward_paper_manifest.PROJECT_ROOT", tmp_path)
    _write(tmp_path / "config" / "trading.yaml", "alpaca: {}\n")
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260720_144500.csv",
        "symbol,final_execution_side,executable,status\nAAA,LONG,true,executable\nBBB,NONE,false,blocked\n",
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_20260720_144500.csv",
        "symbol,status,alpaca_status,message\nAAA,submitted,filled,\n",
    )

    result = write_forward_paper_manifest(root=tmp_path, run_date="20260720")

    assert result["executable_candidate_count"] == "1"
    assert result["submitted_order_count"] == "1"
    assert result["filled_order_count"] == "1"
    assert result["executable_not_submitted_count"] == "0"
