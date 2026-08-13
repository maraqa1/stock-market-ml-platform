from __future__ import annotations

from dataclasses import dataclass
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
        parsed = pd.to_numeric(value, errors="coerce")
        if not pd.isna(parsed):
            return float(parsed)
    return default


def _int(row: dict[str, Any], *names: str, default: int = 0) -> int:
    return int(_float(row, *names, default=float(default)))


def normalize_ai2_status(value: Any) -> str:
    text = _text(value).lower().replace("_", " ")
    return AI2_STATUS_MAP.get(text, "unknown")


def normalize_side(row: dict[str, Any]) -> str:
    raw = _first_text(row, "final_execution_side", "side", "Side/action", "trade_action", "source_trade_action", "decision_side")
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
        row.get("all_block_reasons"),
    ]
    parts: list[str] = []
    for raw in raw_values:
        text = _text(raw)
        if not text:
            continue
        for piece in text.replace(",", "|").replace(";", "|").split("|"):
            clean = piece.strip().lower().replace("warning:", "").replace("ok:", "").strip()
            clean = clean.replace(" ", "_")
            if clean and clean not in parts:
                parts.append(clean)
    return tuple(parts)


def _price_check_clear(row: dict[str, Any], warnings: tuple[str, ...]) -> bool:
    explicit = _first_text(row, "price_check_clear", "price_checks_clear", "order_ready")
    if explicit.lower() in {"true", "1", "yes", "y"}:
        return True
    if explicit.lower() in {"false", "0", "no", "n"}:
        return False
    if "price_checks_clear" in warnings:
        return True
    status = _first_text(row, "session_reject_reason", "primary_block_reason", "order_ready_reason")
    return status == "" or status.lower() in {"order_ready", "none"}


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
        ai2_status = normalize_ai2_status(_first_text(row, "ai2_status", "Decision", "execution_decision", "decision", "Candidate status", "candidate_status"))
        candidate_status = _first_text(row, "candidate_status", "Candidate status", "status", "execution_domain")
        latest_eod = _first_text(row, "latest_eod_date", "Latest EOD date/close", "eod_date", "date")
        if " / " in latest_eod:
            latest_eod = latest_eod.split(" / ", 1)[0].strip()
        return Candidate(
            symbol=_first_text(row, "symbol", "Symbol", "ticker"),
            side=normalize_side(row),
            rank=_int(row, "shortlist_rank", "execution_rank", "rank", "Rank", "Source rank", "candidate_rank", default=0),
            candidate_status=candidate_status,
            ai2_status=ai2_status,
            decision_label=_first_text(row, "decision_label", "Decision", "execution_decision", "ai2_status"),
            approved_notional=_float(row, "approved_notional", "Approved notional", "notional", default=0.0),
            qty=_float(row, "qty", "suggested_quantity", "quantity", default=0.0),
            risk_class=_first_text(row, "risk_class", "risk_tier", "Risk tier"),
            latest_eod_date=latest_eod,
            close_price=_float(row, "close_price", "close", "Latest EOD close", "latest_eod_close", default=0.0),
            expected_return_bps=_float(row, "expected_return_bps", "validated_expected_return_bps", "net_expected_return_bps", default=0.0),
            one_day_return=_float(row, "one_day_return", "1D return", "return_1d", default=0.0),
            five_day_return=_float(row, "five_day_return", "5D return", "return_5d", default=0.0),
            twenty_day_volatility=_float(row, "twenty_day_volatility", "20D vol.", "volatility_20d", default=0.0),
            eod_volume=_float(row, "eod_volume", "EOD volume", "volume", default=0.0),
            price_check_clear=_price_check_clear(row, warnings),
            warning_codes=warnings,
            signal_id=_first_text(row, "signal_id"),
            candidate_id=_first_text(row, "candidate_id"),
            event_id=_first_text(row, "event_id", "event_key"),
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
