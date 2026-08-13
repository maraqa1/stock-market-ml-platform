from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import pandas as pd
try:
    import yaml
except Exception:  # pragma: no cover - optional in lightweight runtimes
    yaml = None

from stockml.common.paths import DATA_DIR, PROJECT_ROOT, data_root, timestamp


CONFIG_PATH = PROJECT_ROOT / "config" / "ai2_enrichment.yaml"
OUTPUT_DIR = DATA_DIR / "portal_outputs"

PROCEED_DECISIONS = {"proceed candidate", "proceed", "execute", "clean"}
REVIEW_DECISIONS = {"review before execution", "review", "watch"}
REFRESH_DECISIONS = {
    "do not execute until refreshed",
    "refresh market data before execution",
    "refresh_required",
    "refresh required",
}
RESEARCH_ONLY_DECISIONS = {"research only", "research_only"}
NOT_READY_DECISIONS = {"not execution-ready", "not execution ready", "not_ready"}

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
    "ai2_warning_codes",
    "ai2_execution_book",
    "ai2_machine_action",
    "ai2_sizing_multiplier",
    "ai2_auto_open_allowed",
    "ai2_block_reason",
]


@dataclass(frozen=True)
class Ai2EnrichmentConfig:
    enabled: bool = False
    require_proceed_for_auto_open: bool = True
    allow_review_for_auto_open: bool = False
    block_refresh_required: bool = True
    api_enabled: bool = False
    endpoint_url: str = ""
    api_key_env: str = "AI2_API_KEY"
    timeout_seconds: int = 30
    candidate_limit: int = 300
    auto_refresh_before_autopilot_tick: bool = False
    max_enrichment_age_minutes: int = 60
    require_fresh_enrichment_for_auto_open: bool = False

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "")


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
        api_enabled=bool(section.get("api_enabled", False)),
        endpoint_url=str(section.get("endpoint_url") or ""),
        api_key_env=str(section.get("api_key_env") or "AI2_API_KEY"),
        timeout_seconds=int(section.get("timeout_seconds", 30)),
        candidate_limit=int(section.get("candidate_limit", 300)),
        auto_refresh_before_autopilot_tick=bool(section.get("auto_refresh_before_autopilot_tick", False)),
        max_enrichment_age_minutes=int(section.get("max_enrichment_age_minutes", 60)),
        require_fresh_enrichment_for_auto_open=bool(section.get("require_fresh_enrichment_for_auto_open", False)),
    )


def latest_ai2_enrichment_path(root: Path | str | None = None) -> Path | None:
    candidates: list[Path] = []
    base = data_root(root)
    for directory in (
        base / "ai2",
        base / "portal_outputs",
        base / "trading" / "exports",
    ):
        if directory.exists():
            candidates.extend(path for path in directory.glob("*ai2*_candidate*.csv") if path.is_file())
            candidates.extend(path for path in directory.glob("*candidate*_ai2*.csv") if path.is_file())
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def latest_ai2_merged_candidates_path(root: Path | str | None = None) -> Path | None:
    directory = data_root(root) / "portal_outputs"
    files = [path for path in directory.glob("ai2_enriched_execution_ranked_candidates_*.csv") if path.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


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

    decision = _column(frame, "ai2_decision", "Decision", "decision", "execution_decision").map(_clean_text)
    out["ai2_decision"] = decision.reindex(out.index).fillna("")
    out["ai2_decision_status"] = out["ai2_decision"].map(ai2_decision_status)
    out["ai2_price_check_status"] = _price_check_status(frame).reindex(out.index).fillna("")
    out["ai2_latest_eod_date"] = _latest_eod_date(frame).reindex(out.index).fillna("")
    out["ai2_latest_eod_close"] = _latest_eod_close(frame).reindex(out.index)
    out["ai2_latest_intraday_price"] = _num_column(frame, "ai2_latest_intraday_price", "Latest intraday", "latest_intraday", "latest_intraday_price").reindex(out.index)
    out["ai2_return_1d_pct"] = _num_column(frame, "ai2_return_1d_pct", "1D return", "1d_return", "one_day_return_pct").reindex(out.index)
    out["ai2_return_5d_pct"] = _num_column(frame, "ai2_return_5d_pct", "5D return", "5d_return", "five_day_return_pct").reindex(out.index)
    out["ai2_eod_volume"] = _num_column(frame, "ai2_eod_volume", "EOD volume", "eod_volume").reindex(out.index)
    out["ai2_volatility_20d_pct"] = _num_column(frame, "ai2_volatility_20d_pct", "20D vol.", "20D vol", "20d_volatility", "volatility_20d_pct").reindex(out.index)
    out["ai2_notes"] = _column(frame, "ai2_notes", "Why / notes", "Checks / notes", "notes").map(_clean_text).reindex(out.index).fillna("")
    out["ai2_warning_codes"] = out.apply(_warning_codes, axis=1)
    policy = out.apply(_execution_policy_row, axis=1, result_type="expand")
    out["ai2_execution_book"] = policy["ai2_execution_book"]
    out["ai2_machine_action"] = policy["ai2_machine_action"]
    out["ai2_sizing_multiplier"] = policy["ai2_sizing_multiplier"]
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
    if text in RESEARCH_ONLY_DECISIONS:
        return "research_only"
    if text in NOT_READY_DECISIONS:
        return "not_execution_ready"
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

    if "ai2_machine_action" not in merged.columns or merged["ai2_machine_action"].fillna("").astype(str).str.strip().eq("").any():
        policy = merged.apply(_execution_policy_row, axis=1, result_type="expand")
        merged["ai2_execution_book"] = policy["ai2_execution_book"]
        merged["ai2_machine_action"] = policy["ai2_machine_action"]
        merged["ai2_sizing_multiplier"] = policy["ai2_sizing_multiplier"]
    if "ai2_warning_codes" not in merged.columns or merged["ai2_warning_codes"].fillna("").astype(str).str.strip().eq("").any():
        merged["ai2_warning_codes"] = merged.apply(_warning_codes, axis=1)

    machine_action = merged["ai2_machine_action"].fillna("").astype(str).str.upper()
    allowed_by_ai2 = status.eq("proceed") & machine_action.eq("ENTER")
    if cfg.allow_review_for_auto_open:
        allowed_by_ai2 |= status.eq("review") & machine_action.eq("ENTER_REDUCED")
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
    merged.loc[existing_ready & status.eq("review") & cfg.allow_review_for_auto_open & ~machine_action.eq("ENTER_REDUCED"), "ai2_block_reason"] = "ai2_review_not_tradable"
    merged.loc[existing_ready & status.eq("refresh_required") & cfg.block_refresh_required, "ai2_block_reason"] = "ai2_refresh_required"
    merged.loc[existing_ready & machine_action.eq("REFRESH_AND_RECHECK"), "ai2_block_reason"] = "ai2_refresh_required"
    merged.loc[existing_ready & machine_action.eq("BLOCK") & merged["ai2_block_reason"].eq(""), "ai2_block_reason"] = "ai2_policy_blocked"
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
    out_dir = Path(output_dir) if output_dir else data_root() / "portal_outputs"
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


def _warning_codes(row: pd.Series) -> str:
    notes = _clean_text(row.get("ai2_notes")).lower()
    codes: list[str] = []
    if "high_volatility" in notes or "high volatility" in notes:
        codes.append("high_volatility")
    if "large_1d_move" in notes or "large 1-day" in notes or "large 1d" in notes:
        codes.append("large_1d_move")
    if "large_intraday_move" in notes or "large intraday" in notes:
        codes.append("large_intraday_move")
    if "extended momentum" in notes or "extended_5d_momentum" in notes:
        codes.append("extended_5d_momentum")
    if "price_checks_clear" in notes or "clean price-check" in notes:
        codes.append("price_checks_clear")
    if "price_check_failed" in notes or "price check failed" in notes:
        codes.append("price_check_failed")
    return ";".join(dict.fromkeys(codes))


def _execution_policy_row(row: pd.Series) -> dict[str, Any]:
    status = _clean_text(row.get("ai2_decision_status")).lower()
    warnings = set(str(row.get("ai2_warning_codes") or _warning_codes(row)).split(";")) - {""}
    vol = _to_number(row.get("ai2_volatility_20d_pct"))
    five_day = _to_number(row.get("ai2_return_5d_pct"))

    if status == "proceed":
        return {"ai2_execution_book": "core", "ai2_machine_action": "ENTER", "ai2_sizing_multiplier": 1.0}
    if status == "refresh_required":
        return {"ai2_execution_book": "blocked", "ai2_machine_action": "REFRESH_AND_RECHECK", "ai2_sizing_multiplier": 0.0}
    if status == "review":
        if "price_check_failed" in warnings:
            return {"ai2_execution_book": "blocked", "ai2_machine_action": "BLOCK", "ai2_sizing_multiplier": 0.0}
        if "large_intraday_move" in warnings or "large_1d_move" in warnings:
            return {"ai2_execution_book": "blocked", "ai2_machine_action": "REFRESH_AND_RECHECK", "ai2_sizing_multiplier": 0.0}
        if five_day is not None and abs(five_day) > 30:
            return {"ai2_execution_book": "blocked", "ai2_machine_action": "BLOCK", "ai2_sizing_multiplier": 0.0}
        if (vol is not None and vol > 7) or "high_volatility" in warnings:
            return {"ai2_execution_book": "reduced", "ai2_machine_action": "ENTER_REDUCED", "ai2_sizing_multiplier": 0.25}
        return {"ai2_execution_book": "reduced", "ai2_machine_action": "ENTER_REDUCED", "ai2_sizing_multiplier": 0.35}
    return {"ai2_execution_book": "blocked", "ai2_machine_action": "BLOCK", "ai2_sizing_multiplier": 0.0}


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
    return notes.map(lambda text: "clean" if "ok: price_checks_clear" in text.lower() or "ok:price_checks_clear" in text.lower() else ("warning" if "warning:" in text.lower() else ""))


def _bool_series(frame: pd.DataFrame, column: str, *, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index)
    return frame[column].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"} if value not in [None, ""] else default)
