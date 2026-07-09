from __future__ import annotations

from stockml.candidates.execution_ranker import latest_candidate_or_plan
from stockml.common.paths import timestamp
from stockml.diagnostics.source_direction_coverage import build_source_direction_coverage_detail
from stockml.trading.source_approval_expansion import write_source_approval_expansion_diagnostic


def main() -> int:
    source_path, candidates = latest_candidate_or_plan()
    if source_path is None or candidates.empty:
        csv_path, md_path, detail = write_source_approval_expansion_diagnostic(candidates, stamp=timestamp())
    else:
        coverage = build_source_direction_coverage_detail(candidates)
        enriched = candidates.reset_index(drop=True).copy()
        if not coverage.empty:
            enriched["source_no_decision_reason"] = coverage.reset_index(drop=True).get("source_no_decision_reason", "")
            enriched["primary_block_reason"] = coverage.reset_index(drop=True).get("primary_block_reason", enriched.get("primary_block_reason", ""))
        csv_path, md_path, detail = write_source_approval_expansion_diagnostic(enriched, stamp=timestamp())
    would_upgrade = int(detail["would_upgrade_to_source_long"].fillna(False).astype(bool).sum()) if not detail.empty and "would_upgrade_to_source_long" in detail.columns else 0
    watch_only = int(detail["source_expansion_decision"].fillna("").astype(str).eq("watch_candidate").sum()) if not detail.empty and "source_expansion_decision" in detail.columns else 0
    print("source_approval_expansion_status: ok")
    print(f"source_path: {source_path}")
    print(f"rows: {len(detail)}")
    print(f"would_upgrade_count: {would_upgrade}")
    print(f"watch_only_count: {watch_only}")
    print(f"csv_path: {csv_path}")
    print(f"markdown_path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
