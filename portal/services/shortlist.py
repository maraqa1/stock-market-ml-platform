from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.db.connection import get_engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _float(value: Any) -> float:
    try:
        parsed = float(value)
        if pd.isna(parsed):
            return 0.0
        return parsed
    except Exception:
        return 0.0


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _bias(value: Any) -> str:
    text_value = _text(value).lower()
    if text_value in {"long", "buy"}:
        return "long"
    if text_value in {"short", "sell"}:
        return "short"
    return "neutral"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y"}


def _run_id_from_path(path: Path) -> str:
    return path.stem


def _run_date_from_path(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()


def _candidate_files(root: Path) -> list[Path]:
    directory = Path(root) / "data" / "portal_outputs"
    if not directory.exists():
        return []
    return sorted(directory.glob("08_alpaca_paper_candidate_pool_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)


def _available_artifact_dates(root: Path) -> list[str]:
    dates = []
    seen = set()
    for path in _candidate_files(root)[:30]:
        label = _run_date_from_path(path).isoformat()
        if label not in seen:
            dates.append(label)
            seen.add(label)
    return dates


def _latest_path_for_date(root: Path, selected: date | None) -> Path | None:
    paths = _candidate_files(root)
    if selected is None:
        return paths[0] if paths else None
    for path in paths:
        if _run_date_from_path(path) == selected:
            return path
    return None


def _row_from_artifact(row: dict[str, Any], index: int, basket_symbols: set[str]) -> dict[str, Any]:
    symbol = _text(row.get("symbol") or row.get("ticker")).upper()
    rank = int(_float(row.get("candidate_rank") or row.get("rank") or row.get("rank_overall") or index))
    quality_status = _text(row.get("trade_quality_status")).lower()
    in_basket = symbol in basket_symbols or (_bool(row.get("order_eligible")) and quality_status in {"approved", "reduced", "trimmed"})
    return {
        "rank": rank,
        "symbol": symbol,
        "name": _text(row.get("company") or row.get("name") or symbol),
        "bias": _bias(row.get("trade_action") or row.get("side") or row.get("bias")),
        "score": _float(row.get("risk_adjusted_score") or row.get("score") or row.get("model_score")),
        "expected_edge_pct": _float(row.get("expected_trade_return") or row.get("expected_edge") or row.get("probability_edge")),
        "sector": _text(row.get("sector")),
        "in_basket": bool(in_basket),
        "excluded_reason": _text(row.get("trade_quality_reason") or row.get("excluded_reason") or row.get("no_decision_reason")),
    }


def _basket_symbols(root: Path) -> set[str]:
    plan = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv"), nrows=1000)
    if plan.empty or "symbol" not in plan.columns:
        return set()
    return {str(symbol).upper() for symbol in plan["symbol"].dropna()}


def _artifact_payload(root: Path, selected: date | None) -> dict[str, Any]:
    path = _latest_path_for_date(root, selected)
    available_dates = _available_artifact_dates(root)
    if path is None:
        selected_label = selected.isoformat() if selected else (available_dates[0] if available_dates else date.today().isoformat())
        return {"run_id": "", "ran_at": "", "total_candidates": 0, "rows": [], "available_dates": available_dates, "selected_date": selected_label, "sectors": []}
    frame = safe_read_csv(path, nrows=1000)
    basket = _basket_symbols(root)
    rows = [_row_from_artifact(row, index, basket) for index, row in enumerate(frame.fillna("").to_dict("records"), start=1)]
    rows = [row for row in rows if row["symbol"]]
    rows.sort(key=lambda item: item["rank"])
    sectors = sorted({row["sector"] for row in rows if row["sector"]})
    ran_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return {
        "run_id": _run_id_from_path(path),
        "ran_at": ran_at.isoformat(),
        "total_candidates": len(rows),
        "rows": rows,
        "available_dates": available_dates,
        "selected_date": ran_at.date().isoformat(),
        "sectors": sectors,
    }


def _db_payload(selected: date | None) -> dict[str, Any] | None:
    engine = get_engine(required=False)
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            date_rows = conn.execute(
                text(
                    """
                    select distinct cast(started_at as date) as run_date
                    from pipeline_runs pr
                    join shortlist_snapshots ss on ss.run_id = pr.run_id
                    order by run_date desc
                    limit 30
                    """
                )
            ).mappings().all()
            available_dates = [str(row["run_date"]) for row in date_rows if row.get("run_date")]
            if not available_dates:
                return None
            selected_label = selected.isoformat() if selected else available_dates[0]
            run = conn.execute(
                text(
                    """
                    select run_id, started_at
                    from pipeline_runs
                    where cast(started_at as date) = :selected_date
                    order by started_at desc, run_id desc
                    limit 1
                    """
                ),
                {"selected_date": selected_label},
            ).mappings().first()
            if not run:
                return {"run_id": "", "ran_at": "", "total_candidates": 0, "rows": [], "available_dates": available_dates, "selected_date": selected_label, "sectors": []}
            rows = conn.execute(
                text(
                    """
                    select rank, symbol, symbol as name, bias, score, expected_edge, sector, in_basket, excluded_reason
                    from shortlist_snapshots
                    where run_id = :run_id
                    order by rank asc
                    """
                ),
                {"run_id": run["run_id"]},
            ).mappings().all()
        normalized = [
            {
                "rank": int(row["rank"]),
                "symbol": str(row["symbol"]).upper(),
                "name": row.get("name") or str(row["symbol"]).upper(),
                "bias": row.get("bias") or "neutral",
                "score": _float(row.get("score")),
                "expected_edge_pct": _float(row.get("expected_edge")),
                "sector": row.get("sector") or "",
                "in_basket": bool(row.get("in_basket")),
                "excluded_reason": row.get("excluded_reason") or "",
            }
            for row in rows
        ]
        return {
            "run_id": run["run_id"],
            "ran_at": run["started_at"].isoformat() if hasattr(run["started_at"], "isoformat") else str(run["started_at"]),
            "total_candidates": len(normalized),
            "rows": normalized,
            "available_dates": available_dates,
            "selected_date": selected_label,
            "sectors": sorted({row["sector"] for row in normalized if row["sector"]}),
        }
    except Exception:
        return None


def _should_use_database(root: Path) -> bool:
    try:
        return Path(root).resolve() == PROJECT_ROOT.resolve()
    except Exception:
        return False


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, str]) -> list[dict[str, Any]]:
    bias = str(filters.get("bias") or "all").lower()
    sector = str(filters.get("sector") or "all")
    in_basket = str(filters.get("in_basket") or "any").lower()
    output = rows
    if bias != "all":
        output = [row for row in output if row["bias"] == bias]
    if sector != "all":
        output = [row for row in output if row["sector"] == sector]
    if in_basket == "yes":
        output = [row for row in output if row["in_basket"]]
    elif in_basket == "no":
        output = [row for row in output if not row["in_basket"]]
    return output


def get_for_date(root: Path, date_value: str | None = None, filters: dict[str, str] | None = None) -> dict[str, Any]:
    selected = None
    if date_value:
        try:
            selected = datetime.fromisoformat(date_value).date()
        except Exception:
            selected = None
    payload = (_db_payload(selected) if _should_use_database(root) else None) or _artifact_payload(root, selected)
    payload["filters"] = {
        "bias": str((filters or {}).get("bias") or "all"),
        "sector": str((filters or {}).get("sector") or "all"),
        "in_basket": str((filters or {}).get("in_basket") or "any"),
    }
    payload["rows"] = _apply_filters(payload["rows"], payload["filters"])
    return payload
