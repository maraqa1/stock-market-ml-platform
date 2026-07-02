from pathlib import Path

from stockml.diagnostics.ai_diagnosis_pack import build_ai_diagnosis_pack


def test_ai_diagnosis_pack_includes_core_files():
    result = build_ai_diagnosis_pack(start="2026-06-01", end="2026-07-02")
    pack = Path(result["pack_dir"])
    assert (pack / "manifest.json").exists()
    assert (pack / "README.md").exists()
    assert (pack / "ai_summary.md").exists()
    assert (pack / "data_dictionary.csv").exists()
    assert (pack / "recommended_questions_for_ai.md").exists()
    for name in [
        "strategy_funnel.csv",
        "gate_registry.csv",
        "gate_attribution.csv",
        "position_gate_degradation.csv",
        "strategy_failure_diagnosis.csv",
    ]:
        assert (pack / name).exists()
    assert "strategy_funnel.csv" in result["manifest"]["files_included"]
