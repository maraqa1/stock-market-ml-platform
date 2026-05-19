#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.marketdata.providers.eodhd import EODHD_BASE_URL, to_eodhd_symbol


def _load_env_api_key() -> str:
    existing = os.getenv("EODHD_API_KEY", "").strip()
    if existing:
        return existing

    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""

    for line in env_path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        if key.strip() == "EODHD_API_KEY":
            return value.strip().strip("'\"")
    return ""


def _json_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = payload[0] if payload else None
        return {
            "json_type": "list",
            "count": len(payload),
            "first_keys": sorted(first.keys()) if isinstance(first, dict) else [],
        }
    if isinstance(payload, dict):
        return {
            "json_type": "dict",
            "keys": sorted(payload.keys())[:30],
            "message": payload.get("message") or payload.get("error"),
        }
    return {"json_type": type(payload).__name__}


def _request_json(session: Any, url: str, params: dict[str, Any], timeout: int) -> tuple[int, dict[str, str], Any]:
    response = session.get(url, params=params, timeout=timeout)
    headers = {str(k).lower(): str(v) for k, v in getattr(response, "headers", {}).items()}
    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": getattr(response, "text", "")[:500]}
    return int(getattr(response, "status_code", 0)), headers, payload


def validate_eodhd_sentiment(
    symbols: list[str],
    from_date: str,
    to_date: str,
    *,
    api_key: str,
    timeout: int = 30,
) -> int:
    import requests

    provider_symbols = [to_eodhd_symbol(symbol) for symbol in symbols if str(symbol).strip()]
    if not provider_symbols:
        raise ValueError("At least one symbol is required.")
    if not api_key:
        raise RuntimeError("EODHD_API_KEY is not set in the environment or .env.")

    checks = [
        (
            "news",
            f"{EODHD_BASE_URL}/news",
            {
                "s": provider_symbols[0],
                "from": from_date,
                "to": to_date,
                "limit": 5,
                "api_token": api_key,
                "fmt": "json",
            },
        ),
        (
            "sentiments",
            f"{EODHD_BASE_URL}/sentiments",
            {
                "s": ",".join(provider_symbols),
                "from": from_date,
                "to": to_date,
                "api_token": api_key,
                "fmt": "json",
            },
        ),
    ]

    failures = 0
    for name, url, params in checks:
        safe_params = {key: ("<redacted>" if key == "api_token" else value) for key, value in params.items()}
        print(f"\n== {name} ==")
        print("url:", url)
        print("params:", json.dumps(safe_params, sort_keys=True))
        status, headers, payload = _request_json(requests, url, params, timeout)
        summary = _json_summary(payload)
        print("http_status:", status)
        print("content_type:", headers.get("content-type", ""))
        print("summary:", json.dumps(summary, sort_keys=True, default=str))
        if status >= 400 or (isinstance(payload, dict) and (payload.get("error") or payload.get("message"))):
            failures += 1

    return 1 if failures else 0


def main() -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description="Validate EODHD news and sentiment API access without writing pipeline artifacts.")
    parser.add_argument("--symbols", default="AAPL,NVDA", help="Comma-separated canonical tickers to validate.")
    parser.add_argument("--from-date", default=(today - timedelta(days=14)).isoformat())
    parser.add_argument("--to-date", default=today.isoformat())
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    return validate_eodhd_sentiment(
        symbols,
        args.from_date,
        args.to_date,
        api_key=_load_env_api_key(),
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
