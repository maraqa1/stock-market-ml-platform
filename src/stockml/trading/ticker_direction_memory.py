from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.common.paths import PROJECT_ROOT


BIAS_TRUST_ORIGINAL = "trust_original"
BIAS_INVERSE_WATCH = "inverse_watch"
BIAS_NO_TRADE = "no_trade"
BIAS_INSUFFICIENT_DATA = "insufficient_data"

MEMORY_COLUMNS = [
    "symbol",
    "sample_count",
    "original_win_rate",
    "inverse_win_rate",
    "avg_original_return_bps",
    "avg_inverse_return_bps",
    "inverse_advantage_bps",
    "ticker_direction_bias",
    "ticker_direction_confidence",
    "ticker_direction_reason",
]


@dataclass(frozen=True)
class TickerDirectionMemoryConfig:
    enabled: bool = True
    min_ticker_samples: int = 5
    min_inverse_advantage_bps: float = 25.0
    min_confidence: float = 0.60
    no_trade_loss_bps: float = -50.0


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _float(value: Any, default: float) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int) -> int:
    try:
        if value is None or pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


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


def load_ticker_direction_memory_config(path: Path | str | None = None) -> TickerDirectionMemoryConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("ticker_direction_memory", {}) if isinstance(payload, dict) else {}
    return TickerDirectionMemoryConfig(
        enabled=_bool(data.get("enabled"), True),
        min_ticker_samples=_int(data.get("min_ticker_samples"), 5),
        min_inverse_advantage_bps=_float(data.get("min_inverse_advantage_bps"), 25.0),
        min_confidence=_float(data.get("min_confidence"), 0.60),
        no_trade_loss_bps=_float(data.get("no_trade_loss_bps"), -50.0),
    )


def _return_bps_from_row(row: pd.Series) -> float | None:
    for column in ["actual_return_bps", "realized_return_bps", "return_bps", "net_return_bps"]:
        value = _num(row.get(column))
        if value is not None:
            return value
    for column in ["actual_plpc", "realized_plpc", "return_pct", "pnl_pct"]:
        value = _num(row.get(column))
        if value is not None:
            return value * 10_000.0 if abs(value) < 2 else value * 100.0
    actual_pl = _num(row.get("actual_pl")) or _num(row.get("realized_pl")) or _num(row.get("pnl"))
    basis = _num(row.get("gross_basis")) or _num(row.get("cost_basis")) or _num(row.get("market_value")) or _num(row.get("entry_value"))
    if actual_pl is not None and basis not in [None, 0]:
        return actual_pl / abs(float(basis)) * 10_000.0
    entry = _num(row.get("entry_price")) or _num(row.get("filled_avg_price"))
    current = _num(row.get("current_price")) or _num(row.get("exit_price")) or _num(row.get("last"))
    side = _text(row.get("actual_side") or row.get("side") or row.get("trade_side")).lower()
    if entry not in [None, 0] and current is not None:
        raw = (current - entry) / entry * 10_000.0
        return -raw if side in {"short", "sell"} else raw
    return None


def normalize_direction_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    """Build per-trade direction outcomes without changing any trading state."""

    if frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol", "original_return_bps", "inverse_return_bps"])
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        symbol = (_text(row.get("symbol")) or _text(row.get("ticker"))).upper()
        if not symbol:
            continue
        original = _return_bps_from_row(row)
        inverse = _num(row.get("inverse_return_bps"))
        if inverse is None:
            inverse_pl = _num(row.get("simulated_opposite_pl")) or _num(row.get("opposite_pl"))
            basis = _num(row.get("gross_basis")) or _num(row.get("cost_basis")) or _num(row.get("market_value")) or _num(row.get("entry_value"))
            if inverse_pl is not None and basis not in [None, 0]:
                inverse = inverse_pl / abs(float(basis)) * 10_000.0
        if inverse is None and original is not None:
            inverse = -original
        if original is None and inverse is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "original_return_bps": original,
                "inverse_return_bps": inverse,
                "source_file": row.get("source_file", ""),
            }
        )
    return pd.DataFrame(rows)


def _confidence(sample_count: int, advantage_bps: float) -> float:
    sample_component = min(0.25, sample_count / 100.0)
    edge_component = min(0.25, abs(advantage_bps) / 500.0)
    return round(min(0.95, 0.45 + sample_component + edge_component), 4)


def summarize_ticker_direction_memory(
    outcomes: pd.DataFrame,
    *,
    config: TickerDirectionMemoryConfig | None = None,
) -> pd.DataFrame:
    cfg = config or TickerDirectionMemoryConfig()
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=MEMORY_COLUMNS)
    frame = outcomes.copy()
    if "original_return_bps" not in frame.columns or "inverse_return_bps" not in frame.columns:
        frame = normalize_direction_outcomes(frame)
    if frame.empty:
        return pd.DataFrame(columns=MEMORY_COLUMNS)
    rows: list[dict[str, Any]] = []
    for symbol, group in frame.groupby("symbol", dropna=False):
        original = pd.to_numeric(group["original_return_bps"], errors="coerce").dropna()
        inverse = pd.to_numeric(group["inverse_return_bps"], errors="coerce").dropna()
        sample_count = int(min(len(original), len(inverse)))
        avg_original = float(original.mean()) if len(original) else 0.0
        avg_inverse = float(inverse.mean()) if len(inverse) else 0.0
        original_win_rate = float((original > 0).mean()) if len(original) else 0.0
        inverse_win_rate = float((inverse > 0).mean()) if len(inverse) else 0.0
        advantage = avg_inverse - avg_original
        confidence = _confidence(sample_count, advantage)
        if sample_count < cfg.min_ticker_samples:
            bias = BIAS_INSUFFICIENT_DATA
            reason = "insufficient_ticker_samples"
        elif advantage >= cfg.min_inverse_advantage_bps and confidence >= cfg.min_confidence and inverse_win_rate > original_win_rate:
            bias = BIAS_INVERSE_WATCH
            reason = "inverse_side_has_ticker_edge"
        elif advantage <= -cfg.min_inverse_advantage_bps and confidence >= cfg.min_confidence and original_win_rate >= inverse_win_rate:
            bias = BIAS_TRUST_ORIGINAL
            reason = "original_side_has_ticker_edge"
        elif avg_original <= cfg.no_trade_loss_bps and avg_inverse <= cfg.no_trade_loss_bps:
            bias = BIAS_NO_TRADE
            reason = "both_sides_negative_for_ticker"
        else:
            bias = BIAS_INSUFFICIENT_DATA
            reason = "ticker_edge_not_decisive"
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "sample_count": sample_count,
                "original_win_rate": round(original_win_rate, 4),
                "inverse_win_rate": round(inverse_win_rate, 4),
                "avg_original_return_bps": round(avg_original, 4),
                "avg_inverse_return_bps": round(avg_inverse, 4),
                "inverse_advantage_bps": round(advantage, 4),
                "ticker_direction_bias": bias,
                "ticker_direction_confidence": confidence,
                "ticker_direction_reason": reason,
            }
        )
    return pd.DataFrame(rows).sort_values(["ticker_direction_bias", "symbol"], kind="mergesort").reindex(columns=MEMORY_COLUMNS)


def apply_ticker_direction_memory(candidates: pd.DataFrame, memory: pd.DataFrame) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return candidates.copy() if candidates is not None else pd.DataFrame()
    out = candidates.copy()
    out["symbol"] = out.get("symbol", out.get("ticker", "")).astype(str).str.upper()

    def has_existing(column: str) -> pd.Series:
        if column not in out.columns:
            return pd.Series(False, index=out.index)
        text = out[column].fillna("").astype(str).str.strip().str.lower()
        return ~text.isin({"", "nan", "none", "null"})

    def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
        if column not in frame.columns:
            return pd.Series(pd.NA, index=frame.index, dtype="float64")
        return pd.to_numeric(frame[column], errors="coerce")

    if memory is None or memory.empty:
        existing_bias = has_existing("ticker_direction_bias")
        if "ticker_direction_bias" not in out.columns:
            out["ticker_direction_bias"] = BIAS_INSUFFICIENT_DATA
        out.loc[~existing_bias, "ticker_direction_bias"] = BIAS_INSUFFICIENT_DATA
        if "ticker_direction_confidence" not in out.columns:
            out["ticker_direction_confidence"] = 0.0
        out["ticker_direction_confidence"] = pd.to_numeric(out["ticker_direction_confidence"], errors="coerce").fillna(0.0)
        if "ticker_direction_sample_count" not in out.columns:
            out["ticker_direction_sample_count"] = 0
        out["ticker_direction_sample_count"] = pd.to_numeric(out["ticker_direction_sample_count"], errors="coerce").fillna(0).astype(int)
        if "ticker_inverse_advantage_bps" not in out.columns:
            out["ticker_inverse_advantage_bps"] = pd.NA
        existing_reason = has_existing("ticker_direction_reason")
        if "ticker_direction_reason" not in out.columns:
            out["ticker_direction_reason"] = "missing_ticker_direction_memory"
        out.loc[~existing_reason, "ticker_direction_reason"] = "missing_ticker_direction_memory"
        return out
    mem = memory.copy()
    mem["symbol"] = mem["symbol"].astype(str).str.upper()
    merged = out.merge(
        mem[
            [
                "symbol",
                "sample_count",
                "inverse_advantage_bps",
                "ticker_direction_bias",
                "ticker_direction_confidence",
                "ticker_direction_reason",
            ]
        ],
        on="symbol",
        how="left",
        suffixes=("", "_memory"),
    )
    for column in ["ticker_direction_bias", "ticker_direction_confidence", "ticker_direction_reason"]:
        existing = merged[column] if column in merged.columns else pd.Series(pd.NA, index=merged.index)
        memory_column = f"{column}_memory"
        fallback = merged[memory_column] if memory_column in merged.columns else pd.Series(pd.NA, index=merged.index)
        merged[column] = existing.combine_first(fallback)

    existing_samples = numeric_series(merged, "ticker_direction_sample_count")
    memory_samples = numeric_series(merged, "sample_count")
    merged["ticker_direction_sample_count"] = existing_samples.where(existing_samples.fillna(0).gt(0), memory_samples).fillna(0).astype(int)

    existing_inverse = numeric_series(merged, "ticker_inverse_advantage_bps")
    memory_inverse = numeric_series(merged, "inverse_advantage_bps")
    merged["ticker_inverse_advantage_bps"] = existing_inverse.combine_first(memory_inverse)

    merged["ticker_direction_bias"] = merged["ticker_direction_bias"].fillna(BIAS_INSUFFICIENT_DATA)
    merged["ticker_direction_confidence"] = pd.to_numeric(merged["ticker_direction_confidence"], errors="coerce").fillna(0.0)
    merged["ticker_direction_reason"] = merged["ticker_direction_reason"].fillna("missing_ticker_direction_memory")
    return merged.drop(
        columns=[
            column
            for column in [
                "sample_count",
                "inverse_advantage_bps",
                "ticker_direction_bias_memory",
                "ticker_direction_confidence_memory",
                "ticker_direction_reason_memory",
            ]
            if column in merged.columns
        ]
    )


def latest_ticker_direction_memory_path(root: Path | str | None = None) -> Path | None:
    base = Path(root) if root else PROJECT_ROOT
    diag = base / "data" / "trading" / "diagnostics"
    files = [path for path in diag.glob("ticker_direction_memory_*.csv") if path.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def load_latest_ticker_direction_memory(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    path = latest_ticker_direction_memory_path(root)
    if path is None:
        return None, pd.DataFrame(columns=MEMORY_COLUMNS)
    try:
        return path, pd.read_csv(path, low_memory=False)
    except Exception:
        return path, pd.DataFrame(columns=MEMORY_COLUMNS)
