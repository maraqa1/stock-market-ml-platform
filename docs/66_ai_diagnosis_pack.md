# AI Diagnosis Pack

`scripts/build_ai_diagnosis_pack.py` creates an uploadable folder under `data/trading/diagnostics/ai_pack_YYYYMMDD_HHMMSS/`.

The pack includes:

- `README.md`
- `ai_summary.md`
- `manifest.json`
- `strategy_funnel.csv`
- `gate_registry.csv`
- `gate_attribution.csv`
- `position_gate_degradation.csv`
- `strategy_failure_diagnosis.csv`
- optional open positions, candidate pool, and closed trade files
- `data_dictionary.csv`
- `recommended_questions_for_ai.md`

The pack is read-only and intended for strategy diagnosis in ChatGPT, Claude, Python, or R. It must not be treated as live-trading approval.
