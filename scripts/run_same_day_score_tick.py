from __future__ import annotations

import argparse
from datetime import datetime, timezone

from stockml.same_day.feature_worker import current_5min_boundary
from stockml.same_day.score_worker import score_tick


def _parse_time(value: str | None) -> datetime:
    if not value:
        return current_5min_boundary()
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score same-day momentum features for one decision tick.")
    parser.add_argument("--decision-time", help="UTC ISO timestamp for the decision tick. Defaults to current 5-minute boundary.")
    args = parser.parse_args(argv)
    decision_time = _parse_time(args.decision_time)
    result = score_tick(decision_time=decision_time)
    print("same_day_score_status:", result.get("status"))
    print("decision_time:", decision_time.isoformat())
    print("features_seen:", result.get("features_seen", 0))
    print("signals_logged:", result.get("signals_logged", 0))
    print("candidates_emitted:", result.get("candidates_emitted", 0))
    print("model_id:", result.get("model_id", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
