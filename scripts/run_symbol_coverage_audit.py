#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.reports.symbol_coverage_audit import build_symbol_coverage_audit


def _parse_symbols(values: list[str]) -> list[str]:
    symbols: list[str] = []
    for value in values:
        symbols.extend(part.strip().upper() for part in value.split(",") if part.strip())
    return symbols


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=[], help="Optional symbols to audit, comma-separated or space-separated.")
    parser.add_argument("--provider", default=None, help="Optional price source filter, for example eodhd.")
    args = parser.parse_args()

    result = build_symbol_coverage_audit(ROOT, symbols=_parse_symbols(args.symbols), provider_name=args.provider)
    print("symbol_coverage_audit_status:", result.get("status"))
    print("rows:", result.get("rows", 0))
    print("provider:", result.get("provider", ""))
    print("path:", result.get("path", ""))
    for name, path in result.get("artifacts", {}).items():
        print(f"{name}_path:", path)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
