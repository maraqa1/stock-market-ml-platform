from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, TRADING_DIR, timestamp
from stockml.diagnostics.broker_fill_reconciliation import latest_file, read_csv

REPORT_COLUMNS = [
    "status",
    "baseline_symbol",
    "baseline_side",
    "baseline_source",
    "baseline_pnl",
    "baseline_score",
    "candidate_symbol",
    "candidate_side",
    "candidate_rank",
    "candidate_source",
    "candidate_id",
    "client_order_id",
    "cycle_id",
    "trade_quality_status",
    "order_eligible",
    "risk_tier",
    "expected_trade_return",
    "risk_adjusted_score",
    "model_score",
    "directional_expected_edge_bps",
    "edge_gap_bps",
    "why_not_traded",
    "diagnostic_decision",
]

ELIGIBLE_STATUSES = {"approved", "reduced", ""}
FALSE_TEXT = {"false", "0", "no", "n", "rejected", "blocked"}


@dataclass(frozen=True)
class MissedBetterCandidatesResult:
    frame: pd.DataFrame
    summary: dict[str, Any]
    report_path: Path | None = None
    summary_path: Path | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _num(value: Any, default: float = 0.0) -> float:
    text = _text(value)
    if not text:
        return default
    try:
        return float(text.replace(",", ""))
    except Exception:
        return default


def _symbol(row: dict[str, Any]) -> str:
    return _text(row.get("symbol") or row.get("ticker")).upper()


def _side(row: dict[str, Any]) -> str:
    raw = _text(row.get("side") or row.get("trade_action") or row.get("order_intent")).lower()
    qty = _num(row.get("qty"), 0.0)
    if raw in {"buy", "long", "open_long"}:
        return "long"
    if raw in {"sell", "short", "open_short"}:
        return "short"
    if qty < 0:
        return "short"
    if qty > 0:
        return "long"
    return ""


def _candidate_score(row: dict[str, Any]) -> float:
    for column in ["directional_expected_edge_bps", "expected_move_bps_calibrated", "model_score", "risk_adjusted_score", "expected_trade_return", "side_probability"]:
        value = _text(row.get(column))
        if not value:
            continue
        number = _num(value, float("nan"))
        if pd.isna(number):
            continue
        if column in {"expected_trade_return", "risk_adjusted_score", "side_probability"} and abs(number) <= 1:
            return number * 10000.0
        return number
    rank = _num(row.get("candidate_rank"), float("nan"))
    return -rank if not pd.isna(rank) else float("nan")


def _baseline_score(row: dict[str, Any]) -> float:
    for column in ["model_score", "risk_adjusted_score", "expected_trade_return", "unrealised_pnl", "unrealized_pnl", "realised_pnl", "realized_pnl"]:
        value = _text(row.get(column))
        if value:
            number = _num(value, float("nan"))
            if not pd.isna(number):
                return number * 10000.0 if column in {"model_score", "risk_adjusted_score", "expected_trade_return"} and abs(number) <= 1 else number
    return 0.0


def normalize_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in candidates.fillna("").to_dict("records") if not candidates.empty else []:
        symbol = _symbol(row)
        if not symbol:
            continue
        status = _text(row.get("trade_quality_status") or row.get("status")).lower()
        raw_eligible = row.get("order_eligible") if "order_eligible" in row else row.get("eligible") if "eligible" in row else "true"
        eligible = _text(raw_eligible).lower() or "true"
        if status not in ELIGIBLE_STATUSES:
            continue
        if eligible in FALSE_TEXT:
            continue
        score = _candidate_score(row)
        rows.append(
            {
                "candidate_symbol": symbol,
                "candidate_side": _side(row),
                "candidate_rank": _num(row.get("candidate_rank"), float("nan")),
                "candidate_source": _text(row.get("candidate_source") or row.get("source") or row.get("strategy_stream")),
                "candidate_id": _text(row.get("candidate_id")),
                "client_order_id": _text(row.get("client_order_id")),
                "cycle_id": _text(row.get("cycle_id")),
                "trade_quality_status": status or "unknown",
                "order_eligible": eligible or "true",
                "risk_tier": _text(row.get("risk_tier")),
                "expected_trade_return": _num(row.get("expected_trade_return"), float("nan")),
                "risk_adjusted_score": _num(row.get("risk_adjusted_score"), float("nan")),
                "model_score": _num(row.get("model_score") or row.get("side_probability"), float("nan")),
                "directional_expected_edge_bps": score,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=[c for c in REPORT_COLUMNS if c.startswith("candidate_") or c in {"trade_quality_status", "order_eligible", "risk_tier", "expected_trade_return", "risk_adjusted_score", "model_score", "directional_expected_edge_bps"}])
    out = out.sort_values(["directional_expected_edge_bps", "candidate_rank"], ascending=[False, True], na_position="last")
    return out.drop_duplicates("candidate_symbol", keep="first").reset_index(drop=True)


def normalize_baseline(ledger: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in positions.fillna("").to_dict("records") if not positions.empty else []:
        symbol = _symbol(row)
        if symbol:
            rows.append({"baseline_symbol": symbol, "baseline_side": _side(row), "baseline_source": "open_position", "baseline_pnl": _num(row.get("unrealized_pl") or row.get("unrealised_pnl"), 0.0), "baseline_score": _baseline_score(row)})
    for row in ledger.fillna("").to_dict("records") if not ledger.empty else []:
        symbol = _symbol(row)
        if symbol:
            pnl = _num(row.get("realised_pnl") or row.get("realized_pnl") or row.get("unrealised_pnl") or row.get("unrealized_pnl"), 0.0)
            rows.append({"baseline_symbol": symbol, "baseline_side": _side(row), "baseline_source": _text(row.get("position_status")) or "trade_ledger", "baseline_pnl": pnl, "baseline_score": _baseline_score(row)})
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["baseline_symbol", "baseline_side", "baseline_source", "baseline_pnl", "baseline_score"])
    return out.drop_duplicates(["baseline_symbol", "baseline_source"], keep="last").reset_index(drop=True)


def find_missed_better_candidates(ledger: pd.DataFrame, positions: pd.DataFrame, candidates: pd.DataFrame, *, limit: int = 25) -> MissedBetterCandidatesResult:
    normalized_candidates = normalize_candidates(candidates)
    baseline = normalize_baseline(ledger, positions)
    if normalized_candidates.empty:
        frame = pd.DataFrame([{"status": "insufficient_data", "why_not_traded": "candidate_pool_missing_or_no_eligible_candidates", "diagnostic_decision": "insufficient_data"}], columns=REPORT_COLUMNS)
        return MissedBetterCandidatesResult(frame, summarize(frame, baseline, normalized_candidates))
    held_or_traded = set(baseline.get("baseline_symbol", pd.Series(dtype=str)).astype(str).str.upper()) if not baseline.empty else set()
    available = normalized_candidates[~normalized_candidates["candidate_symbol"].isin(held_or_traded)].copy()
    if available.empty:
        frame = pd.DataFrame([{"status": "ok", "why_not_traded": "no_stronger_nonheld_candidates", "diagnostic_decision": "no_action"}], columns=REPORT_COLUMNS)
        return MissedBetterCandidatesResult(frame, summarize(frame, baseline, normalized_candidates))
    rows: list[dict[str, Any]] = []
    if baseline.empty:
        for candidate in available.head(limit).to_dict("records"):
            rows.append({"status": "insufficient_data", "baseline_symbol": "", "baseline_side": "", "baseline_source": "none", "baseline_pnl": 0.0, "baseline_score": 0.0, **candidate, "edge_gap_bps": candidate.get("directional_expected_edge_bps", 0.0), "why_not_traded": "no_open_or_ledger_baseline_to_compare", "diagnostic_decision": "review_candidate"})
    else:
        weakest = baseline.sort_values(["baseline_score", "baseline_pnl"], ascending=[True, True]).head(max(1, min(5, len(baseline))))
        for _, base in weakest.iterrows():
            base_payload = base.to_dict()
            for candidate in available.head(limit).to_dict("records"):
                gap = _num(candidate.get("directional_expected_edge_bps"), 0.0) - _num(base_payload.get("baseline_score"), 0.0)
                if gap <= 0:
                    continue
                rows.append({"status": "ok", **base_payload, **candidate, "edge_gap_bps": gap, "why_not_traded": "eligible_candidate_ranked_above_weak_baseline_but_not_held_or_traded", "diagnostic_decision": "review_candidate"})
    frame = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    if frame.empty:
        frame = pd.DataFrame([{"status": "ok", "why_not_traded": "no_positive_edge_gap", "diagnostic_decision": "no_action"}], columns=REPORT_COLUMNS)
    else:
        frame = frame.sort_values(["edge_gap_bps", "directional_expected_edge_bps"], ascending=[False, False], na_position="last").head(limit).reset_index(drop=True)
    return MissedBetterCandidatesResult(frame, summarize(frame, baseline, normalized_candidates))


def summarize(frame: pd.DataFrame, baseline: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, Any]:
    status = "insufficient_data" if not frame.empty and frame["status"].astype(str).eq("insufficient_data").any() else "ok"
    return {
        "status": status,
        "baseline_rows": int(len(baseline)),
        "eligible_candidate_rows": int(len(candidates)),
        "reported_rows": int(len(frame)),
        "review_candidate_rows": int(frame.get("diagnostic_decision", pd.Series(dtype=str)).astype(str).eq("review_candidate").sum()) if not frame.empty else 0,
        "top_candidate": str(frame.iloc[0].get("candidate_symbol", "")) if not frame.empty else "",
        "max_edge_gap_bps": float(pd.to_numeric(frame.get("edge_gap_bps", pd.Series(dtype=float)), errors="coerce").max()) if not frame.empty else 0.0,
    }


def latest_inputs(root: Path = PROJECT_ROOT) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    diagnostics = root / "data" / "trading" / "diagnostics"
    portal = root / "data" / "portal_outputs"
    ledger = read_csv(latest_file(diagnostics, "trade_ledger_*.csv"))
    positions = read_csv(latest_file(portal, "08_alpaca_paper_positions_*.csv"))
    plan = read_csv(latest_file(portal, "08_alpaca_paper_order_plan_*.csv"))
    pool = read_csv(latest_file(portal, "08_alpaca_paper_candidate_pool_*.csv"))
    candidates = pd.concat([frame for frame in [plan, pool] if not frame.empty], ignore_index=True) if (not plan.empty or not pool.empty) else pd.DataFrame()
    return ledger, positions, candidates


def build_latest_missed_better_candidates(root: Path = PROJECT_ROOT, *, limit: int = 25) -> MissedBetterCandidatesResult:
    ledger, positions, candidates = latest_inputs(root)
    return find_missed_better_candidates(ledger, positions, candidates, limit=limit)


def write_missed_better_candidates(result: MissedBetterCandidatesResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> MissedBetterCandidatesResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    report = out / f"missed_better_candidates_{stamp}.csv"
    summary = out / f"missed_better_candidates_summary_{stamp}.md"
    result.frame.to_csv(report, index=False)
    summary.write_text("# Missed Better Candidates Diagnostic\n\n" + "\n".join(f"- {key}: {value}" for key, value in result.summary.items()) + "\n\nThis report is read-only and does not submit orders or change gates.\n", encoding="utf-8")
    return MissedBetterCandidatesResult(result.frame, result.summary, report, summary)
