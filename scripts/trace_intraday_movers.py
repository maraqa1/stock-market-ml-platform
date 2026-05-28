from __future__ import annotations

import argparse
from pathlib import Path

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.mover_trace import parse_symbols, symbols_from_movers_file, trace_intraday_movers


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace why intraday movers did or did not become trades.")
    parser.add_argument("--symbols", help="Comma-separated symbols to trace, e.g. SNOW,AVEX,AMPX")
    parser.add_argument("--movers-file", help="CSV with symbol/ticker column to trace")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="Project root")
    parser.add_argument("--no-write", action="store_true", help="Print only; do not write trace CSV")
    args = parser.parse_args()

    symbols = []
    if args.symbols:
        symbols.extend(parse_symbols(args.symbols))
    if args.movers_file:
        symbols.extend(symbols_from_movers_file(Path(args.movers_file)))
    symbols = parse_symbols(symbols)
    if not symbols:
        raise SystemExit("Provide --symbols or --movers-file")

    frame, path = trace_intraday_movers(symbols, root=Path(args.root), write=not args.no_write)
    if path:
        print(f"mover_trace_path: {path}")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
