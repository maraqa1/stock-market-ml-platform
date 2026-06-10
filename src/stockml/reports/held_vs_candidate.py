from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp


OPEN_ORDER_STATES = {"accepted", "new", "pending_new", "pending_replace", "submitted", "partially_filled", "partial"}
ELIGIBLE_CANDIDATE_STATES = {"approved", "reduced"}


@dataclass(frozen=True)
class HeldVsCandidateOutputs:
    positions_path: Path
    available_path: Path
    summary_path: Path
    position_rows: int
    available_rows: int
    warning_count: int
    missing_inputs: tuple[str, ...] = ()


def build_held_vs_candidate_diagnostic(
    *,
    root: Path | str | None = None,
    stamp: str | None = None,
    output_dir: Path | str | None = None,
    write: bool = True,
    candidate_limit: int = 50,
) -> dict[str, Any]:
    project_root = Path(root or PROJECT_ROOT)
    out_stamp = stamp or timestamp()
    diagnostics_dir = Path(output_dir) if output_dir is not None else project_root / "data" / "trading" / "diagnostics"

    files = {
        "positions": _latest(project_root, "data/portal_outputs", "08_alpaca_paper_positions_*.csv"),
        "candidate_pool": _latest(project_root, "data/portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
        "tracking": _latest(project_root, "data/portal_outputs", "08_alpaca_paper_order_tracking_*.csv"),
        "decisions": _latest(project_root, "data/trading/agent_decisions", "position_decisions_*.csv"),
        "holding_review": _latest(project_root, "data/trading/holding_period", "holding_review_*.csv"),
        "model_signal": _latest(project_root, "data/model_outputs", "advanced_model_signal_table_*.csv"),
    }
    positions = _read(files["positions"], nrows=2000)
    candidates = _read(files["candidate_pool"], nrows=5000)
    tracking = _read(files["tracking"], nrows=2000)
    decisions = _read(files["decisions"], nrows=2000)
    holding = _read(files["holding_review"], nrows=5000)
    model = _read(files["model_signal"], nrows=10000)

    missing = []
    if positions.empty:
        missing.append("positions")
    if candidates.empty:
        missing.append("candidate_pool")

    open_order_symbols = _open_order_symbols(tracking)
    held_symbols = _symbols(positions)
    candidate_norm = _normalize_candidates(candidates)
    held = _build_held_positions(
        positions,
        candidate_norm,
        decisions,
        holding,
        model,
        open_order_symbols=open_order_symbols,
    )
    available = _available_candidates(candidate_norm, held_symbols=held_symbols, open_order_symbols=open_order_symbols, limit=candidate_limit)
    summary = _summary(held, available, positions, open_order_symbols, files, missing)

    positions_path = diagnostics_dir / f"held_vs_candidate_positions_{out_stamp}.csv"
    available_path = diagnostics_dir / f"held_vs_candidate_available_{out_stamp}.csv"
    summary_path = diagnostics_dir / f"held_vs_candidate_summary_{out_stamp}.md"
    if write:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        held.to_csv(positions_path, index=False)
        available.to_csv(available_path, index=False)
        summary_path.write_text(_render_markdown(summary, held, available), encoding="utf-8")

    return {
        "status": "missing_data" if missing else "ok",
        "generated_at": summary["generated_at"],
        "files": {key: str(path) if path else "" for key, path in files.items()},
        "positions_path": str(positions_path),
        "available_path": str(available_path),
        "summary_path": str(summary_path),
        "summary": summary,
        "held_positions": _records(held),
        "available_candidates": _records(available),
        "position_rows": int(len(held)),
        "available_rows": int(len(available)),
        "warning_count": int(pd.to_numeric(held.get("warning_count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not held.empty else 0,
        "missing_inputs": tuple(missing),
    }


def write_held_vs_candidate_diagnostic(
    *,
    root: Path | str | None = None,
    stamp: str | None = None,
    output_dir: Path | str | None = None,
    candidate_limit: int = 50,
) -> HeldVsCandidateOutputs:
    result = build_held_vs_candidate_diagnostic(root=root, stamp=stamp, output_dir=output_dir, write=True, candidate_limit=candidate_limit)
    return HeldVsCandidateOutputs(
        positions_path=Path(result["positions_path"]),
        available_path=Path(result["available_path"]),
        summary_path=Path(result["summary_path"]),
        position_rows=int(result["position_rows"]),
        available_rows=int(result["available_rows"]),
        warning_count=int(result["warning_count"]),
        missing_inputs=tuple(result["missing_inputs"]),
    )


def _latest(root: Path, relative_dir: str, pattern: str) -> Path | None:
    directory = root / relative_dir
    if not directory.exists():
        return None
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def _read(path: Path | None, *, nrows: int | None = None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, nrows=nrows, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _num(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(parsed) else parsed)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _symbol_from(row: pd.Series | dict[str, Any]) -> str:
    return _text(row.get("symbol") or row.get("ticker")).upper()


def _symbols(frame: pd.DataFrame) -> set[str]:
    if frame.empty:
        return set()
    col = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else ""
    if not col:
        return set()
    return {str(symbol).strip().upper() for symbol in frame[col].dropna() if str(symbol).strip()}


def _side_from(row: pd.Series | dict[str, Any]) -> str:
    text = _text(row.get("position_side") or row.get("side") or row.get("trade_action") or row.get("latest_signal")).lower()
    qty = _num(row.get("qty"), default=0.0)
    if text in {"long", "buy", "bullish"}:
        return "long"
    if text in {"short", "sell", "bearish"}:
        return "short"
    if qty < 0:
        return "short"
    if qty > 0:
        return "long"
    return ""


def _direction_sign(side: str) -> float:
    return -1.0 if side == "short" else 1.0


def _as_bps(value: Any) -> float:
    number = _num(value, default=float("nan"))
    if pd.isna(number):
        return float("nan")
    return number * 10000.0 if abs(number) <= 5 else number


def _directional_bps(row: pd.Series | dict[str, Any], columns: list[str], *, side: str) -> float:
    for column in columns:
        if column in row and _text(row.get(column)) != "":
            return _as_bps(row.get(column))
    if "expected_trade_return" in row and _text(row.get("expected_trade_return")) != "":
        return _as_bps(row.get("expected_trade_return")) * _direction_sign(side)
    if "probability_edge" in row and _text(row.get("probability_edge")) != "":
        return _as_bps(row.get("probability_edge")) * _direction_sign(side)
    return float("nan")


def _score_bps(row: pd.Series | dict[str, Any], *, side: str) -> float:
    for column in ["directional_risk_score", "directional_score", "score", "confirmation_score"]:
        if column in row and _text(row.get(column)) != "":
            return _as_bps(row.get(column))
    if "risk_adjusted_score" in row and _text(row.get("risk_adjusted_score")) != "":
        return _as_bps(row.get("risk_adjusted_score")) * _direction_sign(side)
    return float("nan")


def _normalize_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        symbol = _symbol_from(row)
        if not symbol:
            continue
        side = _side_from(row)
        status = _text(row.get("trade_quality_status") or row.get("status")).lower()
        directional_edge = _directional_bps(
            row,
            ["directional_expected_edge_bps", "directional_expected_edge", "expected_directional_edge_bps", "expected_move_bps_calibrated"],
            side=side,
        )
        directional_score = _score_bps(row, side=side)
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "candidate_rank": _num(row.get("candidate_rank"), default=float("nan")),
                "trade_action": _text(row.get("trade_action")),
                "trade_quality_status": status or "unknown",
                "trade_quality_reason": _text(row.get("trade_quality_reason") or row.get("reason")),
                "risk_tier": _text(row.get("risk_tier")),
                "directional_expected_edge_bps": directional_edge,
                "directional_risk_score_bps": directional_score,
                "confidence_score": _num(row.get("confidence_score") or row.get("side_probability"), default=float("nan")),
                "side_probability": _num(row.get("side_probability"), default=float("nan")),
                "expected_trade_return": _num(row.get("expected_trade_return"), default=float("nan")),
                "risk_adjusted_score": _num(row.get("risk_adjusted_score"), default=float("nan")),
                "sector": _text(row.get("sector")),
                "industry": _text(row.get("industry")),
                "source": _text(row.get("source") or row.get("strategy_stream")),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.drop_duplicates("symbol", keep="first")


def _latest_by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {}
    data = frame.copy()
    if "symbol" not in data.columns and "ticker" in data.columns:
        data["symbol"] = data["ticker"]
    if "symbol" not in data.columns:
        return {}
    if "timestamp" in data.columns:
        data = data.sort_values("timestamp")
    elif "generated_at" in data.columns:
        data = data.sort_values("generated_at")
    out: dict[str, dict[str, Any]] = {}
    for record in data.fillna("").to_dict("records"):
        symbol = _text(record.get("symbol")).upper()
        if symbol:
            out[symbol] = record
    return out


def _build_held_positions(
    positions: pd.DataFrame,
    candidates: pd.DataFrame,
    decisions: pd.DataFrame,
    holding: pd.DataFrame,
    model: pd.DataFrame,
    *,
    open_order_symbols: set[str],
) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "position_side",
                "unrealized_pl",
                "unrealized_plpc",
                "trade_quality_status",
                "directional_expected_edge_bps",
                "decision",
                "holding_quality",
                "rotation_flag",
                "warnings",
            ]
        )
    candidate_map = _latest_by_symbol(candidates)
    decision_map = _latest_by_symbol(decisions)
    holding_map = _latest_by_symbol(holding)
    model_map = _latest_by_symbol(model)
    rows: list[dict[str, Any]] = []
    for _, row in positions.iterrows():
        symbol = _symbol_from(row)
        if not symbol:
            continue
        side = _side_from(row)
        candidate = candidate_map.get(symbol, {})
        decision = decision_map.get(symbol, {})
        review = holding_map.get(symbol, {})
        model_row = model_map.get(symbol, {})
        candidate_status = _text(candidate.get("trade_quality_status")) or "missing"
        holding_quality = _text(review.get("holding_quality")) or _text(decision.get("holding_quality"))
        decision_value = _text(decision.get("decision"))
        reason = _text(decision.get("decision_reason"))
        directional_edge = _num(candidate.get("directional_expected_edge_bps"), default=float("nan"))
        warnings = _held_warnings(
            candidate_status=candidate_status,
            directional_edge=directional_edge,
            holding_quality=holding_quality,
            decision_reason=reason,
            unrealized_pl=_num(row.get("unrealized_pl"), default=0.0),
            symbol=symbol,
            open_order_symbols=open_order_symbols,
        )
        rows.append(
            {
                "symbol": symbol,
                "position_side": side,
                "qty": _num(row.get("qty"), default=0.0),
                "avg_entry_price": _num(row.get("avg_entry_price"), default=float("nan")),
                "current_price": _num(row.get("current_price") or row.get("last") or row.get("market_price"), default=float("nan")),
                "market_value": _num(row.get("market_value"), default=0.0),
                "cost_basis": _num(row.get("cost_basis"), default=0.0),
                "unrealized_pl": _num(row.get("unrealized_pl"), default=0.0),
                "unrealized_plpc": _num(row.get("unrealized_plpc"), default=0.0),
                "candidate_rank": candidate.get("candidate_rank", ""),
                "trade_quality_status": candidate_status,
                "trade_quality_reason": candidate.get("trade_quality_reason", ""),
                "directional_expected_edge_bps": directional_edge,
                "directional_risk_score_bps": candidate.get("directional_risk_score_bps", ""),
                "confidence_score": candidate.get("confidence_score", ""),
                "model_latest_signal": _text(model_row.get("trade_action") or model_row.get("latest_signal") or model_row.get("signal")),
                "model_score": _num(model_row.get("model_score") or model_row.get("risk_adjusted_score") or model_row.get("side_probability"), default=float("nan")),
                "decision": decision_value or "missing",
                "recommended_action": _text(decision.get("recommended_action")),
                "decision_reason": reason,
                "holding_quality": holding_quality or "missing",
                "holding_recommended_action": _text(review.get("recommended_action")),
                "holding_gate_reason": _text(review.get("holding_gate_reason")),
                "rotation_flag": _rotation_flag(candidate_status, directional_edge, holding_quality, decision_value, _num(row.get("unrealized_pl"), default=0.0)),
                "warnings": "|".join(warnings),
                "warning_count": len(warnings),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["warning_count", "unrealized_plpc"], ascending=[False, True], na_position="last")
    return out.reset_index(drop=True)


def _held_warnings(
    *,
    candidate_status: str,
    directional_edge: float,
    holding_quality: str,
    decision_reason: str,
    unrealized_pl: float,
    symbol: str,
    open_order_symbols: set[str],
) -> list[str]:
    warnings: list[str] = []
    status = candidate_status.lower()
    quality = holding_quality.lower()
    reason = decision_reason.lower()
    if status in {"rejected", "missing", "unknown"}:
        warnings.append("candidate_not_currently_approved")
    if pd.isna(directional_edge) or directional_edge <= 0:
        warnings.append("weak_or_missing_directional_edge")
    if quality == "avoid":
        warnings.append("holding_review_avoid")
    if "latest_signal_unknown" in reason or "latest_model_signal_missing" in reason:
        warnings.append("latest_signal_unknown")
    if unrealized_pl < 0:
        warnings.append("position_red")
    if symbol in open_order_symbols:
        warnings.append("open_order_pending")
    return warnings


def _rotation_flag(candidate_status: str, directional_edge: float, holding_quality: str, decision: str, unrealized_pl: float) -> str:
    status = candidate_status.lower()
    quality = holding_quality.lower()
    decision_text = decision.lower()
    if decision_text in {"close", "close_now", "replace", "rotate"}:
        return "action_queue"
    if status == "rejected" and unrealized_pl < 0:
        return "review_close"
    if quality == "avoid" and (pd.isna(directional_edge) or directional_edge <= 0):
        return "review_close"
    if quality in {"strong", "watch"} and unrealized_pl >= 0:
        return "watch"
    if pd.isna(directional_edge) or directional_edge <= 0:
        return "compare_replacement"
    return "hold"


def _open_order_symbols(tracking: pd.DataFrame) -> set[str]:
    if tracking.empty or "symbol" not in tracking.columns:
        return set()
    status = tracking.get("alpaca_status", tracking.get("status", pd.Series("", index=tracking.index))).fillna("").astype(str).str.lower()
    return {str(symbol).strip().upper() for symbol in tracking.loc[status.isin(OPEN_ORDER_STATES), "symbol"].dropna() if str(symbol).strip()}


def _available_candidates(candidates: pd.DataFrame, *, held_symbols: set[str], open_order_symbols: set[str], limit: int) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    out = candidates.copy()
    out = out[~out["symbol"].isin(held_symbols | open_order_symbols)].copy()
    status = out["trade_quality_status"].fillna("").astype(str).str.lower()
    out = out[status.isin(ELIGIBLE_CANDIDATE_STATES)].copy()
    if out.empty:
        return out
    out["__edge"] = pd.to_numeric(out["directional_expected_edge_bps"], errors="coerce").fillna(float("-inf"))
    out["__score"] = pd.to_numeric(out["directional_risk_score_bps"], errors="coerce").fillna(float("-inf"))
    out["__confidence"] = pd.to_numeric(out["confidence_score"], errors="coerce").fillna(float("-inf"))
    out = out.sort_values(["__edge", "__score", "__confidence"], ascending=[False, False, False]).drop(columns=["__edge", "__score", "__confidence"])
    return out.head(limit).reset_index(drop=True)


def _summary(
    held: pd.DataFrame,
    available: pd.DataFrame,
    positions: pd.DataFrame,
    open_order_symbols: set[str],
    files: dict[str, Path | None],
    missing: list[str],
) -> dict[str, Any]:
    gross_exposure = float(pd.to_numeric(positions.get("market_value", pd.Series(dtype=float)), errors="coerce").abs().fillna(0).sum()) if not positions.empty else 0.0
    unrealized_pl = float(pd.to_numeric(positions.get("unrealized_pl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not positions.empty else 0.0
    cost_basis = float(pd.to_numeric(positions.get("cost_basis", pd.Series(dtype=float)), errors="coerce").abs().fillna(0).sum()) if not positions.empty else 0.0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "missing_data" if missing else "ok",
        "missing_inputs": ",".join(missing),
        "open_positions": int(len(held)),
        "open_orders": int(len(open_order_symbols)),
        "gross_exposure": gross_exposure,
        "unrealized_pl": unrealized_pl,
        "unrealized_plpc_basis": unrealized_pl / cost_basis if cost_basis else 0.0,
        "held_warning_rows": int((pd.to_numeric(held.get("warning_count", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()) if not held.empty else 0,
        "held_review_close_rows": int((held.get("rotation_flag", pd.Series(dtype=str)).astype(str) == "review_close").sum()) if not held.empty else 0,
        "available_candidates": int(len(available)),
        "top_candidate": str(available.iloc[0]["symbol"]) if not available.empty else "",
        "top_candidate_edge_bps": float(available.iloc[0]["directional_expected_edge_bps"]) if not available.empty and pd.notna(available.iloc[0]["directional_expected_edge_bps"]) else 0.0,
        "source_files": {key: str(path) if path else "" for key, path in files.items()},
    }


def _records(frame: pd.DataFrame, limit: int = 200) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    out = frame.head(limit).copy()
    return [{str(key): _json_value(value) for key, value in row.items()} for row in out.fillna("").to_dict("records")]


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def _render_markdown(summary: dict[str, Any], held: pd.DataFrame, available: pd.DataFrame) -> str:
    lines = [
        "# Held vs Candidate Diagnostic",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- status: {summary['status']}",
        f"- open_positions: {summary['open_positions']}",
        f"- open_orders: {summary['open_orders']}",
        f"- gross_exposure: {summary['gross_exposure']:.2f}",
        f"- unrealized_pl: {summary['unrealized_pl']:.2f}",
        f"- held_warning_rows: {summary['held_warning_rows']}",
        f"- available_candidates: {summary['available_candidates']}",
    ]
    if summary.get("missing_inputs"):
        lines.append(f"- missing_inputs: {summary['missing_inputs']}")
    lines.extend(["", "## Held Positions", ""])
    lines.extend(
        _markdown_table(
            held,
            ["symbol", "position_side", "unrealized_pl", "unrealized_plpc", "trade_quality_status", "directional_expected_edge_bps", "holding_quality", "rotation_flag", "warnings"],
        )
    )
    lines.extend(["", "## Available Candidates", ""])
    lines.extend(
        _markdown_table(
            available,
            ["symbol", "side", "trade_quality_status", "directional_expected_edge_bps", "directional_risk_score_bps", "confidence_score", "candidate_rank"],
        )
    )
    lines.extend(["", "## Notes", "- This report is read-only and does not submit orders or modify trading thresholds."])
    return "\n".join(lines) + "\n"


def _markdown_table(frame: pd.DataFrame, columns: list[str], *, limit: int = 25) -> list[str]:
    if frame.empty:
        return ["No rows."]
    present = [column for column in columns if column in frame.columns]
    rows = ["| " + " | ".join(present) + " |", "| " + " | ".join("---" for _ in present) + " |"]
    for record in frame.head(limit)[present].fillna("").to_dict("records"):
        rows.append("| " + " | ".join(_markdown_cell(record.get(column)) for column in present) + " |")
    return rows


def _markdown_cell(value: Any) -> str:
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        return f"{value:.4f}"
    return str(value).replace("|", "/")
