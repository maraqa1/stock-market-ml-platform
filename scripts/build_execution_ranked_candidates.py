from __future__ import annotations

import argparse

from stockml.candidates.execution_ranker import (
    build_execution_ranked_candidates,
    latest_candidate_or_plan,
    write_execution_ranked_candidates,
)
from stockml.trading.ticker_direction_memory import apply_ticker_direction_memory, load_latest_ticker_direction_memory


def main() -> int:
    parser = argparse.ArgumentParser(description="Build execution-ranked paper-trading candidates.")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    source_path, candidates = latest_candidate_or_plan()
    if source_path is None or candidates.empty:
        print("execution_ranked_candidates_status: missing_data")
        print("missing_inputs: latest_candidate_or_plan")
        return 0

    memory_path, memory = load_latest_ticker_direction_memory()
    candidates = apply_ticker_direction_memory(candidates, memory)
    ranked = build_execution_ranked_candidates(candidates)
    output_path = write_execution_ranked_candidates(candidates, output_dir=args.output_dir)
    executable = ranked[ranked["executable"].eq(True)].copy()
    research_only_shorts = ranked[
        ranked["research_only"].eq(True) & ranked["side"].astype(str).str.lower().eq("sell")
    ]

    print("execution_ranked_candidates_status: ok")
    print(f"source_path: {source_path}")
    print(f"ticker_direction_memory_path: {memory_path or ''}")
    print(f"output_path: {output_path}")
    print(f"rows: {len(ranked)}")
    print(f"executable_candidates: {len(executable)}")
    print(f"research_only_shorts: {len(research_only_shorts)}")
    print("top_execution_ranked_candidates:")
    top = executable.sort_values("execution_rank", kind="mergesort").head(10)
    if top.empty:
        print(" - none")
    else:
        for row in top.to_dict("records"):
            print(
                " - "
                f"{row.get('execution_rank')}: {row.get('symbol')} {row.get('side')} "
                f"raw_rank={row.get('raw_rank')} "
                f"expected_bps={row.get('validated_expected_return_bps')} "
                f"hit_rate={row.get('validated_hit_rate')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
