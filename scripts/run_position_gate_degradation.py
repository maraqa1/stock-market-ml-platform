from __future__ import annotations

from stockml.diagnostics.position_gate_degradation import build_position_gate_degradation


def main() -> int:
    result = build_position_gate_degradation()
    print(f"position_gate_degradation_status: {result['status']}")
    print(f"position_gate_degradation_path: {result['csv_path']}")
    print(f"position_gate_degradation_summary: {result['markdown_path']}")
    print(f"position_gate_degradation_rows: {result['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
