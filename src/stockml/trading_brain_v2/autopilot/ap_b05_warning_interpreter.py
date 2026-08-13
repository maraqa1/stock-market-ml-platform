from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.models import Candidate, EntryAction
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


SUPPORTED_WARNING_CODES = {
    "high_volatility",
    "large_1d_move",
    "large_intraday_move",
    "extended_5d_momentum",
    "price_checks_clear",
    "price_check_failed",
    "expected_return_unavailable",
    "stale_eod",
    "unknown_warning",
}

WARNING_ALIASES = {
    "high_20_day_volatility": "high_volatility",
    "high_volatility": "high_volatility",
    "large_1d_move": "large_1d_move",
    "large_1_day_move": "large_1d_move",
    "large_intraday_move": "large_intraday_move",
    "extended_5d_momentum": "extended_5d_momentum",
    "extended_momentum": "extended_5d_momentum",
    "price_checks_clear": "price_checks_clear",
    "price_check_failed": "price_check_failed",
    "price_checks_failed": "price_check_failed",
    "expected_return_unavailable": "expected_return_unavailable",
    "stale_eod": "stale_eod",
}


@dataclass(frozen=True)
class WarningInterpretation:
    symbol: str
    warning_codes: tuple[str, ...]
    action: EntryAction
    reason: str


class WarningInterpreterBlock(PlaceholderBlock):
    block_id = "AP-B05"
    name = "Warning Interpreter"

    def evaluate(self, payload: dict | None = None) -> BrainBlockResult:
        candidates = (payload or {}).get("candidates") or []
        decisions = [self.interpret_candidate(candidate) for candidate in candidates]
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="NO_ACTION",
            reason="warning_interpretation_complete",
            details={
                "decisions": [
                    {**decision.__dict__, "action": decision.action.value}
                    for decision in decisions
                ]
            },
        )

    def parse_warning_codes(self, *values: Any) -> tuple[str, ...]:
        codes: list[str] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                raw_parts = [str(part) for part in value]
            else:
                raw_parts = str(value).replace(",", "|").replace(";", "|").split("|")
            for raw in raw_parts:
                text = raw.strip().lower()
                if not text or text in {"nan", "none", "null"}:
                    continue
                normalized_text = (
                    text.replace("warning:", "")
                    .replace("ok:", "")
                    .replace("(", " ")
                    .replace(")", " ")
                    .replace("-", "_")
                    .replace(" ", "_")
                    .strip("_")
                )
                code = self._code_from_text(normalized_text)
                if code not in codes:
                    codes.append(code)
        return tuple(codes)

    def _code_from_text(self, text: str) -> str:
        if text in WARNING_ALIASES:
            return WARNING_ALIASES[text]
        if "high" in text and "volatility" in text:
            return "high_volatility"
        if ("large" in text or "big" in text) and ("intraday" in text):
            return "large_intraday_move"
        if ("large" in text or "big" in text) and ("1d" in text or "1_day" in text or "one_day" in text):
            return "large_1d_move"
        if ("5d" in text or "5_day" in text or "five_day" in text) and ("extended" in text or "momentum" in text):
            return "extended_5d_momentum"
        if "price" in text and "clear" in text:
            return "price_checks_clear"
        if "price" in text and ("fail" in text or "failed" in text):
            return "price_check_failed"
        if "expected_return" in text and ("missing" in text or "unavailable" in text):
            return "expected_return_unavailable"
        if "stale" in text and "eod" in text:
            return "stale_eod"
        return "unknown_warning"

    def interpret_codes(self, codes: tuple[str, ...], *, symbol: str = "") -> WarningInterpretation:
        clean_codes = tuple(code if code in SUPPORTED_WARNING_CODES else "unknown_warning" for code in codes)
        if "price_check_failed" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.BLOCK, reason="price_check_failed")
        if "stale_eod" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.REFRESH_AND_RECHECK, reason="stale_eod")
        if "large_intraday_move" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.REFRESH_AND_RECHECK, reason="large_intraday_move")
        if "large_1d_move" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.REFRESH_AND_RECHECK, reason="large_1d_move")
        if "expected_return_unavailable" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.BLOCK, reason="expected_return_unavailable")
        if "extended_5d_momentum" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.ENTER_REDUCED, reason="extended_5d_momentum_reduces_size")
        if "high_volatility" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.ENTER_REDUCED, reason="high_volatility_reduces_size")
        if clean_codes and set(clean_codes).issubset({"price_checks_clear"}):
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.ENTER, reason="price_checks_clear_continue")
        if "unknown_warning" in clean_codes:
            return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.BLOCK, reason="unknown_warning")
        return WarningInterpretation(symbol=symbol, warning_codes=clean_codes, action=EntryAction.ENTER, reason="no_blocking_warnings")

    def interpret_candidate(self, candidate: Candidate) -> WarningInterpretation:
        codes = self.parse_warning_codes(candidate.warning_codes)
        return self.interpret_codes(codes, symbol=candidate.symbol)
