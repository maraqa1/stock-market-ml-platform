from __future__ import annotations

import argparse

from stockml.diagnostics.ai_diagnosis_pack import build_ai_diagnosis_pack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    args = parser.parse_args()
    result = build_ai_diagnosis_pack(start=args.start, end=args.end)
    print(f"ai_diagnosis_pack_path: {result['pack_dir']}")
    print(f"ai_diagnosis_pack_files: {len(result['manifest']['files_included'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
