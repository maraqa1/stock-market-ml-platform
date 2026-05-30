from __future__ import annotations

from stockml.same_day.feature_worker import feature_tick, prune_old_features


def main() -> None:
    result = feature_tick()
    print("same_day_feature_status:", result.get("status"))
    print("same_day_feature_reason:", result.get("reason", ""))
    print("symbols:", len(result.get("symbols", [])))
    print("features_written:", result.get("features_written", 0))
    pruned = prune_old_features()
    print("features_pruned:", pruned)


if __name__ == "__main__":
    main()
