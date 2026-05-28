#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.pipeline.profile_runner import run_profile
from stockml.pipeline.doctor import audit_latest_pipeline
from stockml.reports.pipeline_quality_checks import build_pipeline_quality_report
from stockml.trading.holding_period import generate_holding_period_report
from stockml.trading.paper_trader import run_paper_trading


def _print_quality_failures(result: dict[str, object]) -> None:
    for row in result.get("failures", []):
        if not isinstance(row, dict):
            continue
        print(
            "quality_failure:",
            row.get("check", ""),
            "observed=" + str(row.get("observed", "")),
            "threshold=" + str(row.get("threshold", "")),
            "message=" + str(row.get("message", "")),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the full nightly pipeline plus trading-day readiness gates.")
    parser.add_argument("--profile", default="us_full")
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--skip-profile", action="store_true", help="Only run quality, plan-only, and holding review against current artifacts.")
    parser.add_argument("--skip-price-download", action="store_true", help="When running the profile, rebuild price validation from the existing price store without downloading.")
    parser.add_argument("--reuse-existing-artifacts", action="store_true", help="When running the profile, avoid external providers and reuse latest upstream artifacts.")
    parser.add_argument("--write-database", action="store_true")
    parser.add_argument("--provider", default=None)
    args = parser.parse_args(argv)

    if not args.skip_profile:
        run_profile(
            args.profile,
            override_limit=args.limit_tickers,
            write_database=args.write_database,
            provider_name=args.provider,
            reuse_existing_artifacts=args.reuse_existing_artifacts,
            skip_price_download=args.skip_price_download,
        )

    doctor = audit_latest_pipeline(ROOT, profile_name="us_full")
    print("pipeline_doctor_status:", doctor.get("status"))
    print("pipeline_doctor_reason:", doctor.get("reason", ""))
    print("pipeline_doctor_manifest:", doctor.get("manifest_path"))
    if doctor.get("status") != "ok":
        print("trading_day_readiness_status: failed_pipeline_doctor")
        return 1

    quality = build_pipeline_quality_report(ROOT)
    print("pipeline_quality_status:", quality.get("status"))
    print("pipeline_quality_path:", quality.get("path"))
    print("pipeline_quality_failed_checks:", quality.get("failed_checks"))
    if quality.get("status") != "ok":
        _print_quality_failures(quality)
        print("trading_day_readiness_status: failed_quality_gate")
        return 1

    plan = run_paper_trading(plan_only=True)
    print("orders_planned:", plan.get("orders_planned"))
    print("candidate_pool_rows:", plan.get("candidate_pool_rows"))
    print("orders_approved:", plan.get("orders_approved"))
    print("orders_rejected:", plan.get("orders_rejected"))
    print("plan_path:", plan.get("plan_path"))
    print("result_path:", plan.get("result_path"))

    if int(plan.get("candidate_pool_rows") or 0) <= 0 or int(plan.get("orders_planned") or 0) <= 0:
        print("trading_day_readiness_status: failed_empty_plan")
        return 1

    holding = generate_holding_period_report(ROOT, plan_file=Path(plan["plan_path"]))
    print("holding_period_status:", holding.get("status"))
    print("holding_review_rows:", holding.get("review_rows"))
    print("holding_review_passed:", holding.get("review_passed"))
    print("holding_review_blocked:", holding.get("review_blocked"))
    print("holding_review_path:", holding.get("review_path"))

    if int(holding.get("review_rows") or 0) <= 0:
        print("trading_day_readiness_status: failed_empty_holding_review")
        return 1

    print("trading_day_readiness_status: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
