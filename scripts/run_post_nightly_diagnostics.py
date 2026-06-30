#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.pipeline.doctor import audit_latest_pipeline


DEFAULT_STEPS = (
    ("trade_ledger", ROOT / "scripts" / "build_trade_ledger.py"),
    ("profitability_attribution", ROOT / "scripts" / "build_profitability_attribution.py"),
    ("strategy_diagnostics", ROOT / "scripts" / "run_strategy_diagnostics.py"),
    ("intraday_promotion_replay", ROOT / "scripts" / "run_intraday_promotion_replay.py"),
)


def _print_doctor(result: dict) -> None:
    print("pipeline_doctor_status:", result.get("status"))
    print("pipeline_doctor_reason:", result.get("reason", ""))
    print("pipeline_profile:", result.get("profile", ""))
    print("pipeline_manifest:", result.get("manifest_path", ""))
    print("pipeline_manifest_status:", result.get("manifest_status", ""))
    print("pipeline_run_id:", result.get("run_id", ""))
    print("pipeline_age_minutes:", result.get("age_minutes"))
    print("pipeline_missing_stages:", ",".join(result.get("missing_stages", [])))
    missing_outputs = result.get("missing_outputs", [])
    if missing_outputs:
        print("pipeline_missing_outputs:")
        for item in missing_outputs[:20]:
            print(" -", item)


def wait_for_pipeline(
    *,
    profile: str,
    stale_after_minutes: int,
    max_wait_minutes: int,
    poll_seconds: int,
) -> dict:
    deadline = time.monotonic() + max_wait_minutes * 60
    while True:
        result = audit_latest_pipeline(ROOT, profile_name=profile, stale_after_minutes=stale_after_minutes)
        _print_doctor(result)
        status = result.get("status")
        if status == "ok":
            return result
        if status != "running":
            raise RuntimeError(f"pipeline_doctor_failed:{result.get('reason', '')}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"pipeline_wait_timeout:{max_wait_minutes}m")
        print(f"post_nightly_diagnostics_status: waiting poll_seconds={poll_seconds}")
        time.sleep(poll_seconds)


def run_step(name: str, script: Path, extra_args: list[str] | None = None) -> None:
    command = [sys.executable, str(script), *(extra_args or [])]
    print(f"post_nightly_step_start: {name}")
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"post_nightly_step_done: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for the nightly pipeline, then run read-only diagnostics.")
    parser.add_argument("--profile", default="us_full")
    parser.add_argument("--stale-after-minutes", type=int, default=360)
    parser.add_argument("--max-wait-minutes", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--skip-wait", action="store_true", help="Run one doctor check and fail if it is not ok.")
    parser.add_argument("--skip-trade-ledger", action="store_true")
    parser.add_argument("--skip-profitability-attribution", action="store_true")
    parser.add_argument("--skip-strategy-diagnostics", action="store_true")
    parser.add_argument("--skip-promotion-replay", action="store_true")
    args = parser.parse_args(argv)

    if args.skip_wait:
        result = audit_latest_pipeline(ROOT, profile_name=args.profile, stale_after_minutes=args.stale_after_minutes)
        _print_doctor(result)
        if result.get("status") != "ok":
            print("post_nightly_diagnostics_status: blocked")
            return 1
    else:
        wait_for_pipeline(
            profile=args.profile,
            stale_after_minutes=args.stale_after_minutes,
            max_wait_minutes=args.max_wait_minutes,
            poll_seconds=args.poll_seconds,
        )

    steps = []
    if not args.skip_trade_ledger:
        steps.append(DEFAULT_STEPS[0])
    if not args.skip_profitability_attribution:
        steps.append(DEFAULT_STEPS[1])
    if not args.skip_strategy_diagnostics:
        steps.append(DEFAULT_STEPS[2])
    if not args.skip_promotion_replay:
        steps.append(DEFAULT_STEPS[3])

    for name, script in steps:
        run_step(name, script)

    print("post_nightly_diagnostics_status: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
