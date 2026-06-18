from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR, PORTAL_OUTPUTS_DIR, timestamp
from stockml.diagnostics.common import latest_gold, latest_portal, numeric, safe_read_csv, write_report, DiagnosticOutput
from stockml.diagnostics.side_mapping_audit import inverse_action, normalize_action

REQUIRED_COLUMNS = [
    "symbol", "timestamp", "original_action", "original_side", "inverse_action", "inverse_side", "fill_price", "exit_price", "original_return", "inverse_return", "original_pnl", "inverse_pnl", "spread_cost_estimate", "slippage_cost_estimate", "original_net_pnl", "inverse_net_pnl", "session_mode", "candidate_source", "model_score", "rank_overall", "predicted_rank_pct_by_date", "meta_label_probability", "meta_label_decision", "risk_tier", "liquidity_tier", "sector", "holding_minutes"
]


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _inverse_side(side: object) -> str:
    text = _text(side).lower()
    if text == "buy":
        return "sell"
    if text == "sell":
        return "buy"
    action = inverse_action(side)
    return "buy" if action == "Long" else "sell" if action == "Short" else ""


def _latest_nonempty(pattern: str) -> Path | None:
    files = sorted(PORTAL_OUTPUTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        frame = safe_read_csv(path)
        if not frame.empty:
            return path
    return None


def _recent_order_history(limit_files: int = 300) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    files = sorted(PORTAL_OUTPUTS_DIR.glob("08_alpaca_paper_order_tracking_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit_files]
    if not files:
        files = sorted(PORTAL_OUTPUTS_DIR.glob("08_alpaca_paper_order_results_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit_files]
    for path in files:
        frame = safe_read_csv(path)
        if frame.empty:
            continue
        frame["source_file"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    keys = [c for c in ["order_id", "client_order_id", "symbol", "side", "filled_qty", "filled_avg_price", "submitted_at", "updated_at"] if c in out.columns]
    if keys:
        out = out.drop_duplicates(keys, keep="last")
    return out


def build_inverse_strategy(frame: pd.DataFrame, *, cost_bps: float = 10.0) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    out = frame.copy()
    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    out["symbol"] = out.get("symbol", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    out["timestamp"] = out.get("submitted_at", out.get("date", ""))
    out["original_side"] = out.get("side", out.get("broker_side", "")).astype(str).str.lower()
    out["original_action"] = out.get("trade_action", out.get("directional_action", out["original_side"])).map(normalize_action)
    out["inverse_action"] = out["original_action"].map(inverse_action)
    out["inverse_side"] = out["original_side"].map(_inverse_side)
    out["fill_price"] = numeric(out, "filled_avg_price", default=float("nan"))
    if out["fill_price"].isna().all():
        out["fill_price"] = numeric(out, "limit_price", default=float("nan"))
    out["exit_price"] = numeric(out, "mark_price", default=float("nan"))
    if out["exit_price"].isna().all():
        out["exit_price"] = numeric(out, "current_price", default=float("nan"))
    if out["exit_price"].isna().all():
        out["exit_price"] = numeric(out, "last", default=float("nan"))
    if out["exit_price"].isna().all() and {"symbol", "original_side", "timestamp", "filled_avg_price"}.issubset(out.columns):
        timed = out.copy()
        timed["__time"] = pd.to_datetime(timed["timestamp"], errors="coerce", utc=True)
        timed["__fill"] = pd.to_numeric(timed["filled_avg_price"], errors="coerce")
        exit_prices = []
        for _, row in timed.iterrows():
            same = timed[
                timed["symbol"].eq(row["symbol"])
                & timed["__time"].gt(row["__time"])
                & timed["original_side"].ne(row["original_side"])
                & timed["__fill"].notna()
            ].sort_values("__time")
            exit_prices.append(float(same.iloc[0]["__fill"]) if not same.empty else float("nan"))
        out["exit_price"] = pd.Series(exit_prices, index=out.index)
    side_sign = out["original_side"].map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
    base_return = (out["exit_price"] - out["fill_price"]) / out["fill_price"]
    if base_return.isna().all() and "forward_5d_return" in out.columns:
        base_return = numeric(out, "forward_5d_return", default=float("nan"))
    out["original_return"] = base_return * side_sign
    out["inverse_return"] = -out["original_return"]
    qty = numeric(out, "filled_qty", default=0.0)
    if (qty == 0).all():
        qty = numeric(out, "suggested_quantity", default=1.0).replace(0, 1.0)
    gross = out["fill_price"].fillna(0) * qty.abs().fillna(1.0)
    out["original_pnl"] = out["original_return"].fillna(0) * gross
    out["inverse_pnl"] = -out["original_pnl"]
    out["spread_cost_estimate"] = gross * (cost_bps / 10000.0 / 2.0)
    out["slippage_cost_estimate"] = gross * (cost_bps / 10000.0 / 2.0)
    cost = out["spread_cost_estimate"] + out["slippage_cost_estimate"]
    out["original_net_pnl"] = out["original_pnl"] - cost
    out["inverse_net_pnl"] = out["inverse_pnl"] - cost
    out["session_mode"] = out.get("extended_hours", pd.Series(False, index=out.index)).map(lambda x: "24x5" if str(x).lower() in {"true", "1", "yes"} else "regular")
    for col in ["candidate_source", "model_score", "rank_overall", "predicted_rank_pct_by_date", "meta_label_probability", "meta_label_decision", "risk_tier", "liquidity_tier", "sector", "holding_minutes"]:
        if col not in out.columns:
            out[col] = ""
    return out.reindex(columns=REQUIRED_COLUMNS)


def summarize_inverse(frame: pd.DataFrame) -> dict[str, object]:
    original = pd.to_numeric(frame.get("original_net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0)
    inverse = pd.to_numeric(frame.get("inverse_net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0)
    return {
        "original_total_pnl": float(pd.to_numeric(frame.get("original_pnl", original), errors="coerce").fillna(0).sum()),
        "inverse_total_pnl": float(pd.to_numeric(frame.get("inverse_pnl", inverse), errors="coerce").fillna(0).sum()),
        "original_hit_rate": float((original > 0).mean()) if len(original) else 0.0,
        "inverse_hit_rate": float((inverse > 0).mean()) if len(inverse) else 0.0,
        "original_net_after_cost": float(original.sum()),
        "inverse_net_after_cost": float(inverse.sum()),
        "inverse_beats_original": bool(inverse.sum() > original.sum()) if len(original) else False,
        "statistically_meaningful": bool(len(original) >= 30),
        "polarity_bug_likely": bool(len(original) >= 30 and inverse.sum() > original.sum() * 1.25),
    }


def write_inverse_summary(stamp: str, inverse_frame: pd.DataFrame, polarity_frame: pd.DataFrame | None, path: Path | None = None) -> DiagnosticOutput:
    summary = summarize_inverse(inverse_frame)
    polarity_bug = summary["polarity_bug_likely"] or bool(polarity_frame is not None and "polarity_bug_likely" in polarity_frame.columns and polarity_frame["polarity_bug_likely"].astype(bool).any())
    lines = [
        "# Inverse Strategy Diagnostic",
        "",
        f"original total P&L: {summary['original_total_pnl']:.2f}",
        f"inverse total P&L: {summary['inverse_total_pnl']:.2f}",
        f"original hit rate: {summary['original_hit_rate']:.4f}",
        f"inverse hit rate: {summary['inverse_hit_rate']:.4f}",
        f"original net after cost: {summary['original_net_after_cost']:.2f}",
        f"inverse net after cost: {summary['inverse_net_after_cost']:.2f}",
        f"whether inverse beats original: {summary['inverse_beats_original']}",
        f"whether evidence is statistically meaningful: {summary['statistically_meaningful']}",
        f"whether a polarity bug is likely: {polarity_bug}",
        "recommended next action: Keep production direction unchanged; review this diagnostic with enough filled/outcome sample size before changing strategy direction.",
    ]
    out = path or MODEL_OUTPUTS_DIR / "diagnostics" / f"inverse_strategy_summary_{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return DiagnosticOutput("inverse_strategy_summary", out, 1, "ok")


def build_inverse_strategy_report(stamp: str, *, source_file: Path | None = None) -> DiagnosticOutput:
    path = source_file
    frame = safe_read_csv(path) if path else _recent_order_history()
    out = build_inverse_strategy(frame)
    if out.empty:
        out = pd.DataFrame([{col: "" for col in REQUIRED_COLUMNS}])
        return write_report("inverse_strategy_diagnostic", out, MODEL_OUTPUTS_DIR / "diagnostics" / f"inverse_strategy_diagnostic_{stamp}.csv", missing_inputs=("paper_order_history",))
    return write_report("inverse_strategy_diagnostic", out, MODEL_OUTPUTS_DIR / "diagnostics" / f"inverse_strategy_diagnostic_{stamp}.csv")
