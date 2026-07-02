from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy
from stockml.common.paths import PROJECT_ROOT, timestamp


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


@dataclass(frozen=True)
class ShortSideGuardOutputs:
    csv_path: Path
    markdown_path: Path
    frame: pd.DataFrame


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num_series(frame: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(0.0, index=frame.index, dtype="float64")


def _side_series(frame: pd.DataFrame) -> pd.Series:
    for name in ["direction", "side", "trade_side", "position_side"]:
        if name in frame.columns:
            values = frame[name].fillna("").astype(str).str.lower()
            return values.replace({"sell": "short", "buy": "long"})
    return pd.Series("", index=frame.index, dtype="object")


def _quality_warning(frame: pd.DataFrame) -> tuple[str, str]:
    warnings: list[str] = []
    if "opened_by_signal_id" in frame.columns and frame["opened_by_signal_id"].fillna("").astype(str).str.strip().eq("").all():
        warnings.append("opened_by_signal_id_missing")
    if "trigger_source" in frame.columns and frame["trigger_source"].fillna("").astype(str).str.lower().eq("position_snapshot_reconstruction").any():
        warnings.append("trigger_source_position_snapshot_reconstruction")
    if "signal_state_at_close" in frame.columns and frame["signal_state_at_close"].fillna("").astype(str).str.lower().str.contains("estimated", na=False).any():
        warnings.append("estimated_close_state")
    for column in ["max_favourable_bps", "max_adverse_bps"]:
        if column in frame.columns and pd.to_numeric(frame[column], errors="coerce").fillna(0).eq(0).all():
            warnings.append(f"{column}_unavailable")
    if warnings:
        warnings.append("short_side_decision_based_on_reconstructed_attribution")
        return "reconstructed", "|".join(dict.fromkeys(warnings))
    return "direct", ""


def evaluate_short_side_performance(
    closed_trades: pd.DataFrame,
    *,
    policy: ShortSidePolicy | None = None,
) -> pd.DataFrame:
    active_policy = policy or load_short_side_policy()
    if closed_trades is None:
        closed_trades = pd.DataFrame()
    frame = closed_trades.copy()
    if frame.empty:
        quality, warning = "missing", "closed_trade_attribution_missing"
        return pd.DataFrame(
            [
                {
                    "closed_trades": 0,
                    "closed_short_trades": 0,
                    "short_winners": 0,
                    "short_losers": 0,
                    "short_win_rate": 0.0,
                    "short_realised_pnl": 0.0,
                    "short_average_pnl": 0.0,
                    "short_average_return_bps": 0.0,
                    "short_profit_factor": 0.0,
                    "worst_short_symbols": "",
                    "short_loss_concentration": 0.0,
                    "short_policy_decision": "SHORTS_DISABLED_INSUFFICIENT_DATA",
                    "attribution_quality": quality,
                    "warnings": warning,
                }
            ]
        )

    side = _side_series(frame)
    pnl = _num_series(frame, ["realised_pnl", "realized_pnl", "realized_pnl_usd", "realised_pnl_usd", "pnl", "realized_pl", "realised_pl"])
    returns = _num_series(frame, ["net_return_bps", "return_bps", "net_bps", "realized_return_bps", "realized_net_bps", "realised_net_bps"])
    shorts = frame[side.eq("short")].copy()
    short_pnl = pnl.loc[shorts.index] if not shorts.empty else pd.Series(dtype="float64")
    short_returns = returns.loc[shorts.index] if not shorts.empty else pd.Series(dtype="float64")

    closed_short_trades = int(len(shorts))
    winners = int(short_pnl.gt(0).sum()) if closed_short_trades else 0
    losers = int(short_pnl.lt(0).sum()) if closed_short_trades else 0
    total_pnl = float(short_pnl.sum()) if closed_short_trades else 0.0
    gross_profit = float(short_pnl[short_pnl.gt(0)].sum()) if closed_short_trades else 0.0
    gross_loss = float(abs(short_pnl[short_pnl.lt(0)].sum())) if closed_short_trades else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    win_rate = winners / closed_short_trades if closed_short_trades else 0.0
    average_pnl = total_pnl / closed_short_trades if closed_short_trades else 0.0
    average_return = float(short_returns.mean()) if len(short_returns) else 0.0

    worst_symbols = ""
    concentration = 0.0
    if closed_short_trades and "symbol" in shorts.columns:
        by_symbol = short_pnl.groupby(shorts["symbol"].fillna("").astype(str).str.upper()).sum().sort_values()
        worst_symbols = ";".join(f"{symbol}:{value:.2f}" for symbol, value in by_symbol.head(5).items())
        total_loss = abs(float(by_symbol[by_symbol.lt(0)].sum()))
        concentration = abs(float(by_symbol.iloc[0])) / total_loss if total_loss > 0 and len(by_symbol) else 0.0

    reasons: list[str] = []
    if closed_short_trades < active_policy.min_closed_short_trades_for_enablement:
        reasons.append("INSUFFICIENT_DATA")
    if total_pnl < 0:
        reasons.append("NEGATIVE_EDGE")
    if win_rate < active_policy.min_short_win_rate_for_enablement:
        reasons.append("LOW_WIN_RATE")
    if profit_factor < active_policy.min_short_profit_factor_for_enablement:
        reasons.append("LOW_PROFIT_FACTOR")

    if reasons:
        decision = "SHORTS_DISABLED_" + "_AND_".join(reasons)
    elif not active_policy.enabled or not active_policy.allow_shorts_in_validation:
        decision = "SHORTS_RESEARCH_ONLY"
    else:
        decision = "SHORTS_ELIGIBLE_FOR_PAPER_ONLY"

    quality, warning = _quality_warning(frame)
    return pd.DataFrame(
        [
            {
                "closed_trades": int(len(frame)),
                "closed_short_trades": closed_short_trades,
                "short_winners": winners,
                "short_losers": losers,
                "short_win_rate": round(win_rate, 6),
                "short_realised_pnl": round(total_pnl, 4),
                "short_average_pnl": round(average_pnl, 4),
                "short_average_return_bps": round(average_return, 4),
                "short_profit_factor": round(profit_factor, 6),
                "worst_short_symbols": worst_symbols,
                "short_loss_concentration": round(concentration, 6),
                "short_policy_decision": decision,
                "attribution_quality": quality,
                "warnings": warning,
            }
        ]
    )


def read_closed_trades(path: Path | str) -> pd.DataFrame:
    source = Path(path)
    if not source.exists():
        return pd.DataFrame()
    return pd.read_csv(source, low_memory=False)


def write_short_side_performance_guard(
    closed_trades: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    policy: ShortSidePolicy | None = None,
) -> ShortSideGuardOutputs:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    frame = evaluate_short_side_performance(closed_trades, policy=policy)
    csv_path = out_dir / f"short_side_performance_guard_{run_stamp}.csv"
    md_path = out_dir / f"short_side_performance_guard_{run_stamp}.md"
    frame.to_csv(csv_path, index=False)
    row = frame.iloc[0].to_dict()
    md_path.write_text(
        "\n".join(
            [
                "# Short Side Performance Guard",
                "",
                f"- closed_trades: {row['closed_trades']}",
                f"- closed_short_trades: {row['closed_short_trades']}",
                f"- short_realised_pnl: {row['short_realised_pnl']}",
                f"- short_win_rate: {row['short_win_rate']}",
                f"- short_profit_factor: {row['short_profit_factor']}",
                f"- decision: {row['short_policy_decision']}",
                f"- attribution_quality: {row['attribution_quality']}",
                f"- warnings: {row['warnings']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ShortSideGuardOutputs(csv_path=csv_path, markdown_path=md_path, frame=frame)
