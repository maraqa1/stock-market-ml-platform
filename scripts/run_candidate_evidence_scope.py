from __future__ import annotations

from stockml.diagnostics.candidate_evidence_scope import run_candidate_evidence_scope


def main() -> int:
    result = run_candidate_evidence_scope()
    print(f"candidate_evidence_scope_status: {result['status']}")
    print(f"source_path: {result['source_path']}")
    print(f"csv_path: {result['csv_path']}")
    print(f"markdown_path: {result['markdown_path']}")
    print(f"rows: {result['rows']}")
    print(f"executable_count: {result['executable_count']}")
    print(f"research_only_count: {result['research_only_count']}")
    print(f"blocked_count: {result['blocked_count']}")
    print(f"expected_return_scope_distribution: {result['expected_return_scope_distribution']}")
    print(f"ticker_memory_distribution: {result['ticker_memory_distribution']}")
    print(f"inverse_warnings_actionable: {result['inverse_warnings_actionable']}")
    for label, path in result["split_paths"].items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
