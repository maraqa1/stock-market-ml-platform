from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy, short_side_block_reason
from stockml.common.paths import PROJECT_ROOT, timestamp


OUTPUT_COLUMNS = [
    "raw_rank",
    "model_rank",
    "research_rank",
    "execution_rank",
    "symbol",
    "side",
    "status",
    "executable",
    "research_only",
    "all_block_reasons",
    "primary_block_reason",
    "risk_tier",
    "volatility_tier",
    "calibration_quality",
    "validated_expected_return_bps",
    "validated_hit_rate",
    "validated_profit_factor",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _symbol(row: pd.Series) -> str:
    return (_text(row.get("symbol")) or _text(row.get("ticker"))).upper()


def _side(row: pd.Series) -> str:
    side = _text(row.get("side")).lower()
    action = _text(row.get("trade_action")).lower()
    if side in {"buy", "long"} or action == "long":
        return "buy"
    if side in {"sell", "short"} or action == "short":
        return "sell"
    return side or action


def _raw_rank(frame: pd.DataFrame) -> pd.Series:
    for column in ["raw_rank", "candidate_rank", "rank_overall", "research_rank"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(range(1, len(frame) + 1), index=frame.index, dtype="float64")


def _model_rank(frame: pd.DataFrame) -> pd.Series:
    for column in ["model_rank", "rank_overall", "candidate_rank"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return _raw_rank(frame)


def _split_reasons(value: Any) -> list[str]:
    text = _text(value)
    if not text or text.lower() == "approved":
        return []
    return [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]


def _append_reason(reasons: list[str], reason: str) -> list[str]:
    if reason and reason not in reasons:
        reasons.append(reason)
    return reasons


def _source_action_reason(row: pd.Series) -> str:
    action = (_text(row.get("source_trade_action")) or _text(row.get("trade_action"))).lower()
    if action in {"long", "short"}:
        return ""
    return "source_trade_action_not_executable"


def _no_decision_reason(row: pd.Series) -> str:
    action = (_text(row.get("trade_action")) or _text(row.get("source_trade_action"))).lower()
    if action in {"no decision", "no_decision", "none", ""}:
        return "no_decision"
    explicit = _text(row.get("no_decision_reason"))
    if explicit and explicit.lower() not in {"nan", "none", "not provided"}:
        return "no_decision"
    return ""


def _calibration_reason(row: pd.Series) -> str:
    quality = _text(row.get("expected_return_quality")).lower()
    calibration = _text(row.get("calibration_quality")).lower()
    if quality in {"usable", "calibrated", "weak_allowed_by_config"} or calibration == "usable":
        return ""
    if quality or calibration:
        return "expected_return_uncalibrated"
    if "expected_return_uncalibrated" in _text(row.get("trade_quality_reason")):
        return "expected_return_uncalibrated"
    if "Expected return uncalibrated" in _text(row.get("message")):
        return "expected_return_uncalibrated"
    return ""


def _status(row: pd.Series, reasons: list[str], *, research_only: bool) -> str:
    if research_only:
        return "research_only"
    current = _text(row.get("trade_quality_status")).lower() or _text(row.get("candidate_status")).lower()
    if current in {"approved", "reduced"} and not reasons:
        return "executable"
    if not reasons and _num(row.get("approved_notional")) and int(_num(row.get("suggested_quantity")) or 0) > 0:
        return "executable"
    return "blocked"


def build_execution_ranked_candidates(
    candidates: pd.DataFrame,
    *,
    short_policy: ShortSidePolicy | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    policy = short_policy or load_short_side_policy()
    frame = candidates.copy()
    frame["raw_rank"] = _raw_rank(frame)
    frame["model_rank"] = _model_rank(frame)
    frame["research_rank"] = frame["raw_rank"]
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        reasons = _split_reasons(row.get("trade_quality_reason"))
        if not reasons:
            reasons = _split_reasons(row.get("message"))
        for reason in [_source_action_reason(row), _no_decision_reason(row), _calibration_reason(row)]:
            _append_reason(reasons, reason)
        short_reason = short_side_block_reason(row, policy)
        research_only = bool(short_reason)
        if short_reason:
            _append_reason(reasons, short_reason)
        status = _status(row, reasons, research_only=research_only)
        executable = status == "executable"
        rows.append(
            {
                "__index": idx,
                "raw_rank": row.get("raw_rank"),
                "model_rank": row.get("model_rank"),
                "research_rank": row.get("research_rank"),
                "execution_rank": pd.NA,
                "symbol": _symbol(row),
                "side": _side(row),
                "status": status,
                "executable": executable,
                "research_only": research_only,
                "all_block_reasons": "|".join(reasons),
                "primary_block_reason": reasons[0] if reasons else "",
                "risk_tier": row.get("risk_tier", ""),
                "volatility_tier": row.get("volatility_tier", ""),
                "calibration_quality": row.get("calibration_quality", ""),
                "validated_expected_return_bps": row.get("validated_expected_return_bps", ""),
                "validated_hit_rate": row.get("validated_hit_rate", ""),
                "validated_profit_factor": row.get("validated_profit_factor", ""),
            }
        )
    out = pd.DataFrame(rows)
    executable_idx = (
        out[out["executable"].eq(True)]
        .sort_values(["raw_rank", "symbol"], ascending=[True, True], kind="mergesort")
        .index
    )
    out.loc[executable_idx, "execution_rank"] = range(1, len(executable_idx) + 1)
    return out.drop(columns=["__index"]).reindex(columns=OUTPUT_COLUMNS)


def latest_candidate_or_plan(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    portal = base / "data" / "portal_outputs"
    patterns = ["08_alpaca_paper_order_plan_*.csv", "08_alpaca_paper_candidate_pool_*.csv"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend([path for path in portal.glob(pattern) if path.is_file()])
    if not files:
        return None, pd.DataFrame()
    path = max(files, key=lambda item: item.stat().st_mtime)
    return path, pd.read_csv(path, low_memory=False)


def write_execution_ranked_candidates(
    candidates: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    short_policy: ShortSidePolicy | None = None,
) -> Path:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "portal_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    ranked = build_execution_ranked_candidates(candidates, short_policy=short_policy)
    path = out_dir / f"execution_ranked_candidates_{run_stamp}.csv"
    ranked.to_csv(path, index=False)
    return path
