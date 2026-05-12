from __future__ import annotations

from stockml.intraday.refresh import candidate_refresh_tick, prune_old_snapshots


def main() -> None:
    result = candidate_refresh_tick()
    print("candidate_refresh_status:", result.get("status"))
    print("candidate_refresh_reason:", result.get("reason", ""))
    print("symbols:", len(result.get("symbols", [])))
    print("snapshots_written:", result.get("snapshots_written", 0))
    pruned = prune_old_snapshots()
    print("snapshots_pruned:", pruned)


if __name__ == "__main__":
    main()
