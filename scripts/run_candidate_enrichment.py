from __future__ import annotations

import argparse
from pathlib import Path

from stockml.common.paths import PROJECT_ROOT
from stockml.trading_brain_v2.enrichment.ai2_enrichment_orchestrator import AI2EnrichmentOrchestrator


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Trading Brain V2 candidate enrichment for a raw candidate pool.")
    parser.add_argument("raw_candidate_file", help="Path to raw candidate pool CSV.")
    parser.add_argument("--run-id", default="", help="Optional stable run id for the enrichment artifact.")
    parser.add_argument("--audit-path", default="", help="Optional JSONL audit output path.")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="Project root.")
    args = parser.parse_args()

    root = Path(args.root)
    raw = Path(args.raw_candidate_file)
    if not raw.is_absolute():
        raw = root / raw
    audit_path = Path(args.audit_path) if args.audit_path else root / "data" / "trading" / "audit" / "trading_brain_v2_enrichment.jsonl"
    orchestrator = AI2EnrichmentOrchestrator(audit_path=audit_path, root=root)
    result = orchestrator.enrich_and_intake(raw, run_id=args.run_id or None)

    print(f"enrichment_status: {result.status}")
    if result.reason:
        print(f"enrichment_reason: {result.reason}")
    print(f"enrichment_provider: {getattr(orchestrator.adapter, 'provider', 'ai2')}")
    print(f"raw_candidate_file: {result.raw_candidate_file or ''}")
    print(f"enriched_candidate_file: {result.enriched_candidate_file or ''}")
    print(f"canonical_enriched_file: {result.canonical_enriched_file or ''}")
    print(f"row_count: {result.row_count}")
    print(f"intake_status: {result.intake_status}")
    print(f"adapter_version: {result.adapter_version}")
    print(f"audit_path: {audit_path}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
