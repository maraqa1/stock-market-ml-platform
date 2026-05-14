#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.per_symbol_forecast.audit import audit_per_symbol_forecast


def main() -> int:
    result = audit_per_symbol_forecast(ROOT)
    print("per_symbol_forecast_audit_status:", result.get("status"))
    print("rows:", result.get("rows", 0))
    print("path:", result.get("path", ""))
    print("source:", result.get("source", ""))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
