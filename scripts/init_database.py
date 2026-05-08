#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.db.loaders import init_database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    init_database(args.database_url)
    print("Database schema initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

