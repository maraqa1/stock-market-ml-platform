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

from stockml.trading.snapshot_export import export_trading_snapshot


def main() -> int:
    result = export_trading_snapshot(ROOT)
    print("trading_snapshot_status:", result.get("status"))
    print("trading_snapshot_rows:", result.get("rows", 0))
    print("trading_snapshot_path:", result.get("path", ""))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
