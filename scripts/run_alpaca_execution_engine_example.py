#!/opt/jupyter-env/bin/python3
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.execution_engine import AlpacaExecutionEngine


def main() -> int:
    recommendations = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "signal": "LONG",
                "confidence": 0.72,
                "rank_score": 0.91,
                "sector": "Technology",
                "last_price": 139.0,
                "avg_dollar_volume": 100_000_000,
                "recommended_notional": 1000,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.06,
            }
        ]
    )
    report = AlpacaExecutionEngine(mode="dry_run").execute(recommendations)
    print(report.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
