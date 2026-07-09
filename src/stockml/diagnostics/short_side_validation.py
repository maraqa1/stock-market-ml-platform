from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.execution_ranker import latest_candidate_or_plan
from stockml.common.paths import PROJECT_ROOT, timestamp


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"
MIN_SHORT_SAMPLES = 50
MIN_PROFIT_FACTOR = 1.10
MIN_WIN_RATE = 0.45
DEFAULT_COST_BPS = 10.0


@dataclass(frozen=True)
class ShortSideValidationOutput:
    csv_path: Path
    markdown_path: Path
    frame: pd.DataFrame
    summary: dict[str, Any]


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


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


def _numeric(frame: pd.DataFrame, names: list[str], default: float = float("nan")) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(default, index=frame.index, dtype="float64")


def _side(frame: pd.DataFrame) -> pd.Series:
    source = frame.get("source_trade_action", frame.get("trade_action", frame.get("side", pd.Series("", index=frame.index))))
    side = frame.get("side", pd.Series("", index=frame.index))
    text = source.fillna("").astype(str).str.lower().str.replace("_", " ", regex=False)
    side_text = side.fillna("").astype(str).str.lower()
    out = pd.Series("unknown", index=frame.index, dtype="object")
    out.loc[text.isin(["short", "sell"]) | side_text.isin(["short", "sell"])] = "short"
    out.loc[text.isin(["long", "buy"]) | side_text.isin(["long", "buy"])] = "long"
    return out


def _source_approved_short(frame: pd.DataFrame) -> pd.Series:
    source = frame.get("source_trade_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower().str.replace("_", " ", regex=False)
    return source.eq("short")


def _short_return_bps(frame: pd.DataFrame, *, borrow_cost_bps: float = 0.0, transaction_cost_bps: float = DEFAULT_COST_BPS) -> pd.Series:
    for name in ["realized_return_bps", "realised_return_bps", "net_return_bps", "return_bps", "gain_after_cost_bps"]:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce") - float(borrow_cost_bps)
    if "validated_expected_return_bps" in frame.columns:
        return pd.to_numeric(frame["validated_expected_return_bps"], errors="coerce") - float(transaction_cost_bps) - float(borrow_cost_bps)
    forward = _numeric(frame, ["forward_5d_return", "target_return_5d"])
    return -forward * 10000.0 - float(transaction_cost_bps) - float(borrow_cost_bps)


def _profit_factor(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    wins = clean[clean.gt(0)]
    losses = clean[clean.lt(0)]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return 999.0 if gross_profit > 0 else 0.0


def _aggregate(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["section", "bucket", "sample_count", "win_rate", "avg_return_bps", "expected_value_after_cost_bps", "profit_factor"])
    if by not in frame.columns:
        buckets = pd.Series("unknown", index=frame.index)
    else:
        buckets = frame[by].fillna("unknown").astype(str).replace({"": "unknown"})
    rows: list[dict[str, Any]] = []
    for bucket, group in frame.groupby(buckets, dropna=False):
        returns = pd.to_numeric(group["short_return_after_cost_bps"], errors="coerce").dropna()
        rows.append(
            {
                "section": f"by_{by}",
                "bucket": bucket,
                "sample_count": int(len(group)),
                "win_rate": float(returns.gt(0).mean()) if len(returns) else 0.0,
                "avg_return_bps": float(returns.mean()) if len(returns) else 0.0,
                "expected_value_after_cost_bps": float(returns.mean()) if len(returns) else 0.0,
                "profit_factor": _profit_factor(returns),
            }
        )
    return pd.DataFrame(rows)


def _market_regime(frame: pd.DataFrame) -> pd.Series:
    for name in ["market_regime", "regime", "spy_regime"]:
        if name in frame.columns:
            return frame[name].fillna("unknown").astype(str)
    market = _numeric(frame, ["spy_return_5d", "market_return_5d", "forward_5d_alpha_vs_spy"], default=float("nan"))
    out = pd.Series("unknown", index=frame.index, dtype="object")
    out.loc[market.gt(0.01)] = "risk_on"
    out.loc[market.lt(-0.01)] = "risk_off"
    out.loc[market.between(-0.01, 0.01, inclusive="both")] = "neutral"
    return out


def _squeeze_flag(frame: pd.DataFrame) -> pd.Series:
    reasons = frame.get("all_block_reasons", frame.get("trade_quality_reason", pd.Series("", index=frame.index))).fillna("").astype(str).str.lower()
    risk = frame.get("short_squeeze_risk_tier", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    volatility = frame.get("volatility_tier", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    return reasons.str.contains("squeeze", na=False) | risk.isin(["high", "severe"]) | volatility.eq("extreme")


def build_short_side_validation_report(
    candidates: pd.DataFrame,
    *,
    borrow_cost_bps: float = 0.0,
    transaction_cost_bps: float = DEFAULT_COST_BPS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if candidates is None:
        candidates = pd.DataFrame()
    frame = candidates.copy()
    if frame.empty:
        summary = {
            "short_candidate_count": 0,
            "source_approved_short_count": 0,
            "short_win_rate": 0.0,
            "short_average_return_bps": 0.0,
            "short_expected_value_after_cost_bps": 0.0,
            "short_profit_factor": 0.0,
            "short_execution_allowed": False,
            "minimum_evidence_needed": f">={MIN_SHORT_SAMPLES} samples, EV > 0 bps, PF > {MIN_PROFIT_FACTOR}, walk-forward pass, no severe squeeze risk",
            "decision": "shorts_disabled_insufficient_data",
        }
        return pd.DataFrame([{"section": "summary", **summary}]), summary

    frame["diagnostic_side"] = _side(frame)
    shorts = frame[frame["diagnostic_side"].eq("short")].copy()
    source_approved = _source_approved_short(frame)
    if shorts.empty:
        summary = {
            "short_candidate_count": 0,
            "source_approved_short_count": int(source_approved.sum()),
            "short_win_rate": 0.0,
            "short_average_return_bps": 0.0,
            "short_expected_value_after_cost_bps": 0.0,
            "short_profit_factor": 0.0,
            "short_execution_allowed": False,
            "minimum_evidence_needed": f">={MIN_SHORT_SAMPLES} samples, EV > 0 bps, PF > {MIN_PROFIT_FACTOR}, walk-forward pass, no severe squeeze risk",
            "decision": "shorts_disabled_no_short_candidates",
        }
        return pd.DataFrame([{"section": "summary", **summary}]), summary

    shorts["short_return_after_cost_bps"] = _short_return_bps(shorts, borrow_cost_bps=borrow_cost_bps, transaction_cost_bps=transaction_cost_bps)
    shorts["short_squeeze_risk_flag"] = _squeeze_flag(shorts)
    shorts["market_regime"] = _market_regime(shorts)
    returns = pd.to_numeric(shorts["short_return_after_cost_bps"], errors="coerce").dropna()
    win_rate = float(returns.gt(0).mean()) if len(returns) else 0.0
    avg_return = float(returns.mean()) if len(returns) else 0.0
    profit_factor = _profit_factor(returns)
    high_squeeze = int(shorts["short_squeeze_risk_flag"].fillna(False).astype(bool).sum())
    walk_forward_status = _text(shorts.get("walk_forward_status", pd.Series("", index=shorts.index)).mode().iloc[0] if "walk_forward_status" in shorts.columns and not shorts["walk_forward_status"].dropna().empty else "")
    walk_forward_pass = walk_forward_status.lower() in {"pass", "passed", "ok"} if walk_forward_status else False
    adequate_sample = len(returns) >= MIN_SHORT_SAMPLES
    allowed = bool(avg_return > 0 and profit_factor > MIN_PROFIT_FACTOR and adequate_sample and walk_forward_pass and high_squeeze == 0)
    blockers: list[str] = []
    if avg_return <= 0:
        blockers.append("expected_return_not_positive_after_cost")
    if profit_factor <= MIN_PROFIT_FACTOR:
        blockers.append("profit_factor_below_1_1")
    if not adequate_sample:
        blockers.append("insufficient_sample_size")
    if not walk_forward_pass:
        blockers.append("walk_forward_not_proven")
    if high_squeeze:
        blockers.append("severe_short_squeeze_risk")

    summary = {
        "short_candidate_count": int(len(shorts)),
        "source_approved_short_count": int(source_approved.sum()),
        "short_win_rate": round(win_rate, 6),
        "short_average_return_bps": round(avg_return, 4),
        "short_expected_value_after_cost_bps": round(avg_return, 4),
        "short_profit_factor": round(profit_factor, 6),
        "borrow_cost_sensitivity_bps": float(borrow_cost_bps),
        "short_squeeze_risk_flags": high_squeeze,
        "walk_forward_status": walk_forward_status or "missing",
        "short_execution_allowed": allowed,
        "minimum_evidence_needed": f">={MIN_SHORT_SAMPLES} samples, EV > 0 bps, PF > {MIN_PROFIT_FACTOR}, walk-forward pass, no severe squeeze risk",
        "decision": "shorts_eligible_for_paper_validation" if allowed else "shorts_disabled_" + "_and_".join(blockers),
    }

    sections = [pd.DataFrame([{"section": "summary", "bucket": "all_shorts", **summary}])]
    if "sector" not in shorts.columns and "industry" in shorts.columns:
        shorts["sector"] = shorts["industry"]
    sections.append(_aggregate(shorts, "sector"))
    sections.append(_aggregate(shorts, "volatility_tier"))
    sections.append(_aggregate(shorts, "market_regime"))
    detail_cols = [
        "symbol",
        "source_trade_action",
        "short_return_after_cost_bps",
        "short_squeeze_risk_flag",
        "sector",
        "volatility_tier",
        "market_regime",
        "primary_block_reason",
        "all_block_reasons",
    ]
    detail = shorts[[col for col in detail_cols if col in shorts.columns]].copy()
    detail.insert(0, "section", "candidate_detail")
    sections.append(detail)
    return pd.concat(sections, ignore_index=True, sort=False), summary


def _latest(patterns: list[str], base: Path) -> Path | None:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in base.glob(pattern) if path.is_file())
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _read(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def run_short_side_validation(
    candidates: pd.DataFrame | None = None,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> ShortSideValidationOutput:
    if candidates is None:
        _, candidates = latest_candidate_or_plan()
        if candidates is None or candidates.empty:
            path = _latest(["execution_ranked_candidates_*.csv", "08_alpaca_paper_candidate_pool_*.csv", "08_alpaca_paper_order_plan_*.csv"], PROJECT_ROOT / "data" / "portal_outputs")
            candidates = _read(path)
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    frame, summary = build_short_side_validation_report(candidates)
    csv_path = out_dir / f"short_side_validation_{run_stamp}.csv"
    md_path = out_dir / f"short_side_validation_{run_stamp}.md"
    frame.to_csv(csv_path, index=False)
    md_path.write_text(
        "\n".join(
            [
                "# Short Side Validation",
                "",
                f"- short_candidate_count: {summary['short_candidate_count']}",
                f"- source_approved_short_count: {summary['source_approved_short_count']}",
                f"- short_win_rate: {summary['short_win_rate']}",
                f"- short_average_return_bps: {summary['short_average_return_bps']}",
                f"- short_expected_value_after_cost_bps: {summary['short_expected_value_after_cost_bps']}",
                f"- short_profit_factor: {summary['short_profit_factor']}",
                f"- short_squeeze_risk_flags: {summary.get('short_squeeze_risk_flags', 0)}",
                f"- minimum_evidence_needed: {summary['minimum_evidence_needed']}",
                f"- short_execution_allowed: {summary['short_execution_allowed']}",
                f"- decision: {summary['decision']}",
                "",
                "Short execution remains disabled unless positive after-cost expected return, profit factor above 1.1, adequate samples, walk-forward survival, and no severe short-squeeze risk are all proven.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ShortSideValidationOutput(csv_path=csv_path, markdown_path=md_path, frame=frame, summary=summary)
