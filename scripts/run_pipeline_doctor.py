#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.pipeline.doctor import audit_latest_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the latest managed pipeline run before readiness or trading.")
    parser.add_argument("--profile", default="us_full")
    parser.add_argument("--stale-after-minutes", type=int, default=90)
    args = parser.parse_args(argv)

    result = audit_latest_pipeline(ROOT, profile_name=args.profile, stale_after_minutes=args.stale_after_minutes)
    print("pipeline_doctor_status:", result.get("status"))
    print("pipeline_doctor_reason:", result.get("reason", ""))
    print("pipeline_profile:", result.get("profile"))
    print("pipeline_manifest:", result.get("manifest_path"))
    print("pipeline_manifest_status:", result.get("manifest_status"))
    print("pipeline_run_id:", result.get("run_id", ""))
    print("pipeline_age_minutes:", result.get("age_minutes"))
    print("pipeline_missing_stages:", ",".join(result.get("missing_stages", [])))
    missing_outputs = result.get("missing_outputs", [])
    if missing_outputs:
        print("pipeline_missing_outputs:")
        for item in missing_outputs[:20]:
            print(" -", item)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
