from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

DEFAULT_ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "NYSEAMERICAN"}

NON_COMMON_NAME_PATTERNS = [
    r"\bACQUISITION CORP(?:ORATION)?\b",
    r"\bBANKRUPT(?:CY)?\b",
    r"\bBLANK CHECK\b",
    r"\bBUSINESS COMBINATION\b",
    r"\bLIQUIDAT(?:E|ED|ING|ION)\b",
    r"\bRECEIVERSHIP\b",
    r"\bREORGANIZATION\b",
    r"\bETF\b",
    r"\bETN\b",
    r"\bFUND\b",
    r"\bINDEX\b",
    r"\bWARRANTS?\b",
    r"\bRIGHTS?\b",
    r"\bUNITS?\b",
    r"\bPREFERRED\b",
    r"\bPREFERENCE\b",
    r"\bDEPOSITARY SHARES\b",
    r"\bNOTE[S]?\b",
    r"\bBOND[S]?\b",
    r"\bDEBENTURE[S]?\b",
    r"\bBABY BOND\b",
    r"\bSPAC\b",
]

NON_COMMON_SYMBOL_PATTERNS = [
    r"\+",
    r"\^",
    r"=",
    r"/",
]


def _upper_str(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.strip()


def normalize_universe_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    required = [
        "symbol",
        "yahoo_ticker",
        "company",
        "security_name",
        "listing_exchange",
        "test_issue",
        "etf_flag",
        "source",
    ]

    for col in required:
        if col not in out.columns:
            out[col] = ""

    out["symbol"] = _upper_str(out["symbol"])
    out["yahoo_ticker"] = (
        out["yahoo_ticker"]
        .fillna(out["symbol"])
        .astype(str)
        .str.upper()
        .str.strip()
        .str.replace(".", "-", regex=False)
    )
    out["company"] = out["company"].fillna("").astype(str).str.strip()
    out["security_name"] = out["security_name"].fillna(out["company"]).astype(str).str.strip()
    out["listing_exchange"] = _upper_str(out["listing_exchange"])
    out["test_issue"] = _upper_str(out["test_issue"])
    out["etf_flag"] = _upper_str(out["etf_flag"])
    out["source"] = out["source"].fillna("").astype(str).str.strip()

    return out


def classify_exclusion_reason(row: pd.Series, allowed_exchanges: Iterable[str] = DEFAULT_ALLOWED_EXCHANGES) -> str:
    symbol = str(row.get("symbol", "")).upper().strip()
    name = str(row.get("security_name", row.get("company", ""))).upper()
    exchange = str(row.get("listing_exchange", "")).upper().strip()
    etf = str(row.get("etf_flag", "")).upper().strip()
    test = str(row.get("test_issue", "")).upper().strip()
    financial_status = str(row.get("financial_status", "")).upper().strip()

    if not symbol:
        return "missing_symbol"

    if exchange not in set(allowed_exchanges):
        return "exchange_not_allowed"

    if test == "Y":
        return "test_issue"

    if financial_status and financial_status != "N":
        return "financial_status_not_normal"

    if etf == "Y":
        return "etf"

    for pattern in NON_COMMON_SYMBOL_PATTERNS:
        if re.search(pattern, symbol):
            return "non_common_symbol_pattern"

    for pattern in NON_COMMON_NAME_PATTERNS:
        if re.search(pattern, name):
            return "non_common_security_name"

    return ""


def clean_universe_frame(df: pd.DataFrame, allowed_exchanges: Iterable[str] = DEFAULT_ALLOWED_EXCHANGES) -> pd.DataFrame:
    out = normalize_universe_frame(df)
    out["exclude_reason"] = out.apply(lambda r: classify_exclusion_reason(r, allowed_exchanges), axis=1)
    out["is_tradable_common_stock_candidate"] = out["exclude_reason"].eq("")
    out = out.sort_values(
        ["is_tradable_common_stock_candidate", "listing_exchange", "symbol"],
        ascending=[False, True, True],
    )
    return out.reset_index(drop=True)


def tradable_only(df: pd.DataFrame, allowed_exchanges: Iterable[str] = DEFAULT_ALLOWED_EXCHANGES) -> pd.DataFrame:
    cleaned = clean_universe_frame(df, allowed_exchanges)
    return cleaned[cleaned["is_tradable_common_stock_candidate"]].copy().reset_index(drop=True)
