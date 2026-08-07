from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
try:
    import yaml
except Exception:  # pragma: no cover - optional in lightweight runtimes
    yaml = None

from stockml.common.paths import PROJECT_ROOT, timestamp


CONFIG_PATH = PROJECT_ROOT / "config" / "ai2_enrichment.yaml"
OUTPUT_DIR = PROJECT_ROOT / "data" / "portal_outputs"

PROCEED_DECISIONS = {"proceed candidate", "proceed", "execute", "clean"}
REVIEW_DECISIONS = {"review before execution", "review", "watch"}
REFRESH_DECISIONS = {"do not execute until refreshed", "refresh_required", "refresh required"}

AI2_OUTPUT_COLUMNS = [
    "ai2_source_file",
    "ai2_decision",
    "ai2_decision_status",
    "ai2_price_check_status",
    "ai2_latest_eod_date",
    "ai2_latest_eod_close",
    "ai2_latest_intraday_price",
    "ai2_return_1d_pct",
    "ai2_return_5d_pct",
    "ai2_eod_volume",
    "ai2_volatility_20d_pct",
    "ai2_notes",
    "ai2_auto_open_allowed",
    "ai2_block_reason",
]


@dataclass(frozen=True)
class Ai2EnrichmentConfig:
    enabled: bool = False
    require_proceed_for_auto_open: bool = True
    allow_review_for_auto_open: bool = False
    block_refresh_required: bool = True


def load_ai2_enrichment_config(path: Path | str | None = None) -> Ai2EnrichmentConfig:
    source = Path(path) if path else CONFIG_PATH
    if not source.exists():
        return Ai2EnrichmentConfig()
    if yaml is None:
        return Ai2EnrichmentConfig()
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    section = payload.get("ai2_enrichment") if isinstance(payload, dict) else {}
    if not isinstance(section, dict):
        section = {}
    return Ai2EnrichmentConfig(
        enabled=bool(section.get("enabled", False)),
        require_proceed_for_auto_open=bool(section.get("require_proceed_for_auto_open", True)),
        allow_review_for_auto_open=bool(section.get("allow_review_for_auto_open", False)),
        block_refresh_required=bool(section.get("block_refresh_required", True)),
    )


def latest_ai2_enrichment_path(root: Path | str | None = None) -> Path | None:
    base = Path(root) if root else PROJECT_ROOT
    candidates: list[Path] = []
    for directory in (
        base / "data" / "ai2",
        base / "data" / "portal_outputs",
        base / "data" / "trading" / "exports",
    ):
        if directory.exists():
            candidates.extend(path for path in directory.glob("*ai2*_candidate*.csv") if path.is_file())
            candidates.extend(path for path in directory.glob("*candidate*_ai2*.csv") if path.is_file())
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def load_ai2_enrichment(path: Path | str) -> pd.DataFrame:
    source = Path(path)
    frame = pd.read_csv(source, low_memory=False)
    return normalize_ai2_enrichment(frame, source_file=source)


def normalize_ai2_enrichment(frame: pd.DataFrame, *, source_file: Path | str | None = None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_enrichment(source_file)

    out = pd.DataFrame(index=frame.index)
    out["symbol"] = _column(frame, "symbol", "Symbol").astype(str).str.upper().str.strip()
    out = out[out["symbol"].ne("")].copy()

    decision = _column(frame, "ai2_decision", "Decision", "decision").map(_clean_text)
    out["ai2_decision"] = decision.reindex(out.index).fillna("")
    out["ai2_decision_status"] = out["ai2_decision"].map(ai2_decision_status)
    out["ai2_price_check_status"] = _price_check_status(frame).reindex(out.index).fillna("")
    out["ai2_latest_eod_date"] = _latest_eod_date(frame).reindex(out.index).fillna("")
    out["ai2_latest_eod_close"] = _latest_eod_close(frame).reindex(out.index)
    out["ai2_latest_intraday_price"] = _num_column(frame, "ai2_latest_intraday_price", "Latest intraday", "latest_intraday").reindex(out.index)
    out["ai2_return_1d_pct"] = _num_column(frame, "ai2_return_1d_pct", "1D return", "1d_return").reindex(out.index)
    out["ai2_return_5d_pct"] = _num_column(frame, "ai2_return_5d_pct", "5D return", "5d_return").reindex(out.index)
    out["ai2_eod_volume"] = _num_column(frame, "ai2_eod_volume", "EOD volume", "eod_volume").reindex(out.index)
    out["ai2_volatility_20d_pct"] = _num_column(frame, "ai2_volatility_20d_pct", "20D vol.", "20D vol", "20d_volatility").reindex(out.index)
    out["ai2_notes"] = _column(frame, "ai2_notes", "Why / notes", "Checks / notes", "notes").map(_clean_text).reindex(out.index).fillna("")
    out["ai2_source_file"] = str(source_file or "")

    out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return out[["symbol", *AI2_OUTPUT_COLUMNS[:-2]]]


def ai2_decision_status(value: Any) -> str:
    text = _clean_text(value).lower()
    if text in PROCEED_DECISIONS:
        return "proceed"
    if text in REVIEW_DECISIONS:
        return "review"
    if text in REFRESH_DECISIONS:
        return "refresh_required"
    if not text:
        return "missing"
    return "unknown"


def apply_ai2_enrichment(
    candidates: pd.DataFrame,
    ai2: pd.DataFrame,
    *,
    config: Ai2EnrichmentConfig | None = None,
) -> pd.DataFrame:
    cfg = config or Ai2EnrichmentConfig()
    base = candidates.copy() if candidates is not None else pd.DataFrame()
    if base.empty:
        return base
    if "symbol" not in base.columns:
        raise ValueError("candidate frame must include symbol")

    base["symbol"] = base["symbol"].astype(str).str.upper().str.strip()
    enrichment = normalize_ai2_enrichment(ai2) if not set(AI2_OUTPUT_COLUMNS).intersection(ai2.columns) else ai2.copy()
    if "symbol" in enrichment.columns:
        enrichment["symbol"] = enrichment["symbol"].astype(str).str.upper().str.strip()
    merged = base.merge(enrichment, on="symbol", how="left", validate="many_to_one")

    for column in AI2_OUTPUT_COLUMNS:
        if column not in merged.columns:
            merged[column] = ""

    status = merged["ai2_decision_status"].fillna("").astype(str).str.lower()
    existing_ready = _bool_series(merged, "executable") & _bool_series(merged, "order_eligible", default=True)
    if "execution_domain" in merged.columns:
        existing_ready &= merged["execution_domain"].fillna("").astype(str).str.lower().eq("execution_candidate")
    if "final_execution_side" in merged.columns:
        existing_ready &= merged["final_execution_side"].fillna("").astype(str).str.upper().isin({"LONG", "SHORT"})
    if "order_ready" in merged.columns:
        existing_ready &= _bool_series(merged, "order_ready")

    allowed_by_ai2 = status.eq("proceed")
    if cfg.allow_review_for_auto_open:
        allowed_by_ai2 |= status.eq("review")
    if not cfg.enabled:
        merged["ai2_auto_open_allowed"] = False
        merged["ai2_block_reason"] = "ai2_bridge_disabled"
        return merged

    merged["ai2_auto_open_allowed"] = existing_ready & allowed_by_ai2
    merged["ai2_block_reason"] = ""
    merged.loc[~existing_ready, "ai2_block_reason"] = "stockml_execution_gate_not_passed"
    merged.loc[existing_ready & status.eq("missing"), "ai2_block_reason"] = "ai2_evidence_missing"
    merged.loc[existing_ready & status.eq("unknown"), "ai2_block_reason"] = "ai2_decision_unknown"
    merged.loc[existing_ready & status.eq("review") & ~cfg.allow_review_for_auto_open, "ai2_block_reason"] = "ai2_review_required"
    merged.loc[existing_ready & status.eq("refresh_required") & cfg.block_refresh_required, "ai2_block_reason"] = "ai2_refresh_required"
    merged.loc[merged["ai2_auto_open_allowed"], "ai2_block_reason"] = ""
    return merged


def write_ai2_enriched_candidates(
    candidates: pd.DataFrame,
    ai2: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    config: Ai2EnrichmentConfig | None = None,
    stamp: str | None = None,
) -> Path:
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = apply_ai2_enrichment(candidates, ai2, config=config)
    path = out_dir / f"ai2_enriched_execution_ranked_candidates_{stamp or timestamp()}.csv"
    merged.to_csv(path, index=False)
    return path


def _empty_enrichment(source_file: Path | str | None = None) -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", *AI2_OUTPUT_COLUMNS[:-2]]).assign(ai2_source_file=str(source_file or ""))


def _column(frame: pd.DataFrame, *names: str) -> pd.Series:
    lookup = {str(column).strip().lower(): column for column in frame.columns}
    for name in names:
        column = lookup.get(name.strip().lower())
        if column is not None:
            return frame[column]
    return pd.Series("", index=frame.index)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _to_number(value: Any) -> float | None:
    text = _clean_text(value).replace(",", "").replace("$", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _num_column(frame: pd.DataFrame, *names: str) -> pd.Series:
    return _column(frame, *names).map(_to_number)


def _latest_eod_date(frame: pd.DataFrame) -> pd.Series:
    direct = _column(frame, "ai2_latest_eod_date", "latest_eod_date")
    if direct.astype(str).str.strip().ne("").any():
        return direct.map(_clean_text)
    combined = _column(frame, "Latest EOD date/close", "latest_eod_date_close")
    return combined.map(lambda value: _clean_text(value).split("/", 1)[0].strip())


def _latest_eod_close(frame: pd.DataFrame) -> pd.Series:
    direct = _num_column(frame, "ai2_latest_eod_close", "latest_eod_close")
    if direct.notna().any():
        return direct
    combined = _column(frame, "Latest EOD date/close", "latest_eod_date_close")
    return combined.map(lambda value: _to_number(_clean_text(value).split("/", 1)[1]) if "/" in _clean_text(value) else None)


def _price_check_status(frame: pd.DataFrame) -> pd.Series:
    direct = _column(frame, "ai2_price_check_status", "price_check_status")
    if direct.astype(str).str.strip().ne("").any():
        return direct.map(_clean_text)
    notes = _column(frame, "Why / notes", "Checks / notes", "notes").map(_clean_text)
    return notes.map(lambda text: "clean" if "ok: price_checks_clear" in text.lower() else ("warning" if "warning:" in text.lower() else ""))


def _bool_series(frame: pd.DataFrame, column: str, *, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index)
    return frame[column].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"} if value not in [None, ""] else default)
