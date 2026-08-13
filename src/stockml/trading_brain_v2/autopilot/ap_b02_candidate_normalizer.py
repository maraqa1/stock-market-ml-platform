from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd

from stockml.trading_brain_v2.shared.models import Candidate
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class CandidateNormalizationIssue:
    source: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class CandidateNormalizationResult:
    candidates: list[Candidate]
    invalid_records: list[CandidateNormalizationIssue]


AI2_STATUS_MAP = {
    "proceed": "proceed",
    "proceed candidate": "proceed",
    "ok": "proceed",
    "price_checks_clear": "proceed",
    "review": "review",
    "review before execution": "review",
    "warning": "review",
    "refresh": "refresh_required",
    "refresh_required": "refresh_required",
    "do not execute until refreshed": "refresh_required",
    "research only": "research_only",
    "research_only": "research_only",
    "not execution-ready": "blocked",
    "not execution ready": "blocked",
    "blocked": "blocked",
}

SIDE_MAP = {
    "long": "LONG",
    "buy": "LONG",
    "bullish": "LONG",
    "short": "SHORT",
    "sell": "SHORT",
    "bearish": "SHORT",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _first_text(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _text(row.get(name))
        if value:
            return value
    return ""


def _float(row: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        value = row.get(name)
        if _text(value) == "":
            continue
        clean = str(value).replace(",", "").replace("%", "").replace("$", "").strip()
        parsed = pd.to_numeric(clean, errors="coerce")
        if not pd.isna(parsed):
            return float(parsed)
    return default


def _int(row: dict[str, Any], *names: str, default: int = 0) -> int:
    return int(_float(row, *names, default=float(default)))


def normalize_ai2_status(value: Any) -> str:
    text = _text(value).lower().replace("_", " ")
    return AI2_STATUS_MAP.get(text, "unknown")


def normalize_side(row: dict[str, Any]) -> str:
    raw = _first_text(row, "final_execution_side", "side", "side_action", "Side/action", "trade_action", "source_trade_action", "decision_side")
    text = raw.lower().replace("_", " ")
    if text in {"none", "no decision", "no trade"}:
        return ""
    if text in SIDE_MAP:
        return SIDE_MAP[text]
    if "long" in text or "buy" in text:
        return "LONG"
    if "short" in text or "sell" in text:
        return "SHORT"
    return ""


def normalize_warning_codes(row: dict[str, Any]) -> tuple[str, ...]:
    raw_values = [
        row.get("warning_codes"),
        row.get("warnings"),
        row.get("Checks / notes"),
        row.get("Why / notes"),
        row.get("notes"),
        row.get("ai2_notes"),
        row.get("ai2_price_check_status"),
        row.get("all_block_reasons"),
    ]
    parts: list[str] = []
    saw_note_text = False
    for raw in raw_values:
        text = _text(raw)
        if not text:
            continue
        saw_note_text = True
        folded_text = text.lower().replace("-", "_")
        if "5_day move" in folded_text and "extended momentum" in folded_text and "extended_5d_momentum" not in parts:
            parts.append("extended_5d_momentum")
        for piece in text.replace(",", "|").replace(";", "|").split("|"):
            lower_piece = piece.strip().lower()
            prefixed = lower_piece.startswith("warning:") or lower_piece.startswith("ok:")
            clean = lower_piece.replace("warning:", "").replace("ok:", "").strip()
            clean = clean.replace("-", "_").replace(" ", "_")
            if "5_day_move" in clean and "extended_momentum" in clean:
                clean = "extended_5d_momentum"
            elif not prefixed and clean not in {
                "high_volatility",
                "large_intraday_move",
                "large_1d_move",
                "extended_5d_momentum",
                "price_checks_clear",
                "price_check_failed",
                "expected_return_unavailable",
                "stale_eod",
            }:
                continue
            if clean and clean not in parts:
                parts.append(clean)
    return tuple(parts or (["unknown_warning"] if saw_note_text else []))


def _price_check_clear(row: dict[str, Any], warnings: tuple[str, ...]) -> bool:
    explicit = _first_text(row, "price_check_clear", "price_checks_clear", "ai2_price_check_status", "order_ready")
    if explicit.lower() in {"clean", "clear", "ok", "price_checks_clear"}:
        return True
    if explicit.lower() in {"failed", "fail", "price_check_failed", "blocked"}:
        return False
    if explicit.lower() in {"true", "1", "yes", "y"}:
        return True
    if explicit.lower() in {"false", "0", "no", "n"}:
        return False
    if "price_checks_clear" in warnings:
        return True
    status = _first_text(row, "session_reject_reason", "primary_block_reason", "order_ready_reason")
    return status == "" or status.lower() in {"order_ready", "none"}


def _return_value(row: dict[str, Any], pct_name: str, *decimal_names: str, default: float = 0.0) -> float:
    if _text(row.get(pct_name)):
        return _float(row, pct_name, default=default * 100.0) / 100.0
    return _float(row, *decimal_names, default=default)


def _stable_id(prefix: str, row: dict[str, Any], *parts: str) -> str:
    values = [_first_text(row, part) for part in parts]
    seed = "|".join(value for value in values if value)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16] if seed else ""
    return f"{prefix}-{digest}" if digest else ""


class CandidateNormalizerBlock(PlaceholderBlock):
    block_id = "AP-B02"
    name = "Candidate Normalizer"

    def evaluate(self, payload: dict | None = None) -> BrainBlockResult:
        result = self.normalize_records((payload or {}).get("records") or [])
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="NO_ACTION",
            reason="candidate_normalization_complete",
            details={"candidates": len(result.candidates), "invalid_records": len(result.invalid_records)},
        )

    def normalize_record(self, row: dict[str, Any]) -> Candidate:
        warnings = normalize_warning_codes(row)
        source_file = _first_text(row, "source_file", "Source file", "execution_ranked_source_path") or "unknown_source"
        ai2_status = normalize_ai2_status(_first_text(row, "ai2_decision", "ai2_decision_status", "ai2_status", "Decision", "execution_decision", "decision", "Candidate status", "candidate_status"))
        candidate_status = _first_text(row, "candidate_status", "Candidate status", "status", "execution_domain")
        latest_eod = _first_text(row, "ai2_latest_eod_date", "latest_eod_date", "Latest EOD date/close", "eod_date", "date")
        if " / " in latest_eod:
            latest_eod = latest_eod.split(" / ", 1)[0].strip()
        rank = _int(row, "shortlist_rank", "execution_rank", "rank", "Rank", "Source rank", "candidate_rank", default=0)
        source_rank = _int(row, "source_rank", "Source rank", default=0)
        notes = _first_text(row, "ai2_notes", "notes", "Checks / notes", "Why / notes")
        identity_parts = ("source_file", "symbol", "Symbol", "shortlist_rank", "source_rank", "ai2_decision", "execution_decision", "Decision")
        return Candidate(
            symbol=_first_text(row, "symbol", "Symbol", "ticker"),
            side=normalize_side(row),
            rank=rank,
            candidate_status=candidate_status,
            ai2_status=ai2_status,
            decision_label=_first_text(row, "decision_label", "ai2_decision", "Decision", "execution_decision", "ai2_status"),
            approved_notional=_float(row, "approved_notional", "Approved notional", "notional", default=0.0),
            qty=_float(row, "qty", "suggested_quantity", "quantity", default=0.0),
            risk_class=_first_text(row, "risk_class", "risk_tier", "Risk tier"),
            latest_eod_date=latest_eod,
            close_price=_float(row, "close_price", "close", "ai2_latest_eod_close", "Latest EOD close", "latest_eod_close", default=0.0),
            intraday_price=_float(row, "intraday_price", "ai2_latest_intraday_price", "latest_intraday_price", "Latest intraday", "current_price", default=0.0),
            expected_return_bps=_float(row, "expected_return_bps", "validated_expected_return_bps", "net_expected_return_bps", default=0.0),
            one_day_return=_return_value(row, "ai2_return_1d_pct", "one_day_return_pct", "one_day_return", "1D return", "return_1d", default=0.0),
            five_day_return=_return_value(row, "ai2_return_5d_pct", "five_day_return_pct", "five_day_return", "5D return", "return_5d", default=0.0),
            twenty_day_volatility=_return_value(row, "ai2_volatility_20d_pct", "volatility_20d_pct", "twenty_day_volatility", "20D vol.", "volatility_20d", default=0.0),
            eod_volume=_float(row, "ai2_eod_volume", "eod_volume", "EOD volume", "volume", default=0.0),
            price_check_clear=_price_check_clear(row, warnings),
            source_rank=source_rank,
            notes=notes,
            raw_source_fields=dict(row),
            warning_codes=warnings,
            signal_id=_first_text(row, "signal_id") or _stable_id("sig", row, *identity_parts),
            candidate_id=_first_text(row, "candidate_id") or _stable_id("cand", row, *identity_parts),
            event_id=_first_text(row, "event_id", "event_key") or _stable_id("evt", row, *identity_parts),
            source_file=source_file,
        )

    def normalize_records(self, records: list[dict[str, Any]]) -> CandidateNormalizationResult:
        candidates: list[Candidate] = []
        invalid: list[CandidateNormalizationIssue] = []
        for row in records:
            try:
                candidates.append(self.normalize_record(dict(row)))
            except Exception as exc:
                invalid.append(CandidateNormalizationIssue(source=dict(row), reason=str(exc)))
        return CandidateNormalizationResult(candidates=candidates, invalid_records=invalid)
