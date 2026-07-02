from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, latest_file
from stockml.diagnostics.gate_attribution import build_gate_attribution
from stockml.diagnostics.position_gate_degradation import build_position_gate_degradation
from stockml.diagnostics.strategy_failure_diagnosis import build_strategy_failure_diagnosis
from stockml.strategy.gate_registry import registry_frame
from stockml.strategy.strategy_funnel import build_strategy_funnel


DIAGNOSTICS_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


def _copy_csv(source: Path | None, dest: Path) -> tuple[int, list[str]]:
    if source is None or not source.exists() or source.stat().st_size == 0:
        pd.DataFrame().to_csv(dest, index=False)
        return 0, []
    try:
        frame = pd.read_csv(source, low_memory=False)
    except Exception:
        frame = pd.DataFrame()
    frame.to_csv(dest, index=False)
    return len(frame), list(frame.columns)


def _write_dictionary(path: Path, schemas: dict[str, list[str]]) -> None:
    rows = []
    meanings = {
        "symbol": "Ticker symbol",
        "gate_name": "Registered gate or block reason",
        "suggested_position_action": "Diagnostics-only hold/reduce/close/manual-review suggestion",
        "diagnostics_only": "True means report does not submit or close orders",
        "recommendation": "Gate attribution recommendation",
        "failure_area": "High-level strategy failure area",
    }
    for file_name, columns in schemas.items():
        for column in columns:
            rows.append({"file_name": file_name, "column_name": column, "meaning": meanings.get(column, ""), "type": "string_or_numeric", "allowed_values": "", "notes": ""})
    pd.DataFrame(rows).to_csv(path, index=False)


def build_ai_diagnosis_pack(start: str = "", end: str = "", now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    out_dir = DIAGNOSTICS_DIR / f"ai_pack_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    funnel = build_strategy_funnel(now)
    gate_attr = build_gate_attribution(now)
    degradation = build_position_gate_degradation(now)
    failure = build_strategy_failure_diagnosis(now)
    files: dict[str, Path] = {}
    schemas: dict[str, list[str]] = {}
    row_counts: dict[str, int] = {}

    outputs = {
        "strategy_funnel.csv": funnel["csv_path"],
        "gate_attribution.csv": gate_attr["csv_path"],
        "position_gate_degradation.csv": degradation["csv_path"],
        "strategy_failure_diagnosis.csv": failure["csv_path"],
    }
    registry = registry_frame()
    registry_path = out_dir / "gate_registry.csv"
    registry.to_csv(registry_path, index=False)
    files["gate_registry.csv"] = registry_path
    schemas["gate_registry.csv"] = list(registry.columns)
    row_counts["gate_registry.csv"] = len(registry)

    for name, path in outputs.items():
        dest = out_dir / name
        count, columns = _copy_csv(Path(path), dest)
        files[name] = dest
        schemas[name] = columns
        row_counts[name] = count

    optional = {
        "open_positions_snapshot.csv": latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv"),
        "execution_ranked_candidates.csv": latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
        "closed_trades_attribution.csv": latest_file(DIAGNOSTICS_DIR, "closed_trades_attribution_*.csv"),
    }
    missing = []
    for name, path in optional.items():
        dest = out_dir / name
        count, columns = _copy_csv(path, dest)
        files[name] = dest
        schemas[name] = columns
        row_counts[name] = count
        if not path:
            missing.append(name)

    _write_dictionary(out_dir / "data_dictionary.csv", schemas)
    questions = [
        "Which gates are protecting the strategy?",
        "Which gates may be overblocking?",
        "Which open positions have degraded most severely?",
        "Should new entries be blocked until open-book risk is reduced?",
        "Are shorts still unsafe?",
        "Is 24/5 execution hurting?",
        "Is position management the main failure?",
        "Which gate should be tuned only after attribution?",
    ]
    (out_dir / "recommended_questions_for_ai.md").write_text("# Recommended Questions\n\n" + "\n".join(f"- {q}" for q in questions) + "\n", encoding="utf-8")
    (out_dir / "README.md").write_text(
        "# StockML AI Diagnosis Pack\n\n"
        "This folder contains read-only diagnostics for strategy review. Do not infer live-trading readiness from these files.\n\n"
        "Core files: strategy_funnel.csv, gate_registry.csv, gate_attribution.csv, position_gate_degradation.csv, strategy_failure_diagnosis.csv.\n\n"
        "Known limitations: forward-return attribution may be insufficient; missing files are listed in manifest.json.\n",
        encoding="utf-8",
    )
    top_degraded = degradation["frame"].sort_values("gate_degradation_score", ascending=False).head(5) if "gate_degradation_score" in degradation["frame"].columns else pd.DataFrame()
    (out_dir / "ai_summary.md").write_text(
        "# AI Strategy Diagnosis Summary\n\n"
        f"Generated: {now.isoformat()}\n"
        f"Date range: {start or 'not specified'} to {end or 'not specified'}\n\n"
        "High-confidence conclusions:\n"
        "- Gate removal is not recommended.\n"
        "- Position-management and gate-degradation diagnosis should precede strategy changes.\n"
        "- Shorts and 24/5 execution require separate attribution.\n\n"
        f"Top degraded symbols: {', '.join(top_degraded.get('symbol', pd.Series(dtype=str)).astype(str).tolist())}\n",
        encoding="utf-8",
    )
    manifest = {
        "generated_at": now.isoformat(),
        "date_range": {"start": start, "end": end},
        "files_included": sorted([*files, "README.md", "ai_summary.md", "data_dictionary.csv", "recommended_questions_for_ai.md"]),
        "row_counts": row_counts,
        "schema_columns": schemas,
        "missing_files": missing,
        "data_quality_warnings": ["diagnostics_only", "do_not_infer_live_trading_readiness"],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {"pack_dir": out_dir, "manifest": manifest, "row_counts": row_counts}
