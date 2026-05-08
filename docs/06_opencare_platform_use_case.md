# Deferred OpenCare Use Case: Reboot-Safe Data Intelligence Pipeline

## Implementation Status

Status: deferred.

This document is not an instruction to implement the OpenCare platform changes now. It is an organized future use case based on the StockML work completed in this repository. The purpose is to preserve the architecture, lessons, and acceptance criteria so the OpenCare implementation can be planned later with less ambiguity.

## Use Case Summary

OpenCare may need a repeatable platform pattern for running data-intensive analytical pipelines that generate operational intelligence, persist large outputs safely, expose the results through a web portal or API, and survive VM reboots without losing configuration or data.

The StockML platform implementation provides the reference pattern:

- local repository is the source of truth
- GitHub Actions validates every code change
- VM pulls only from `main`
- generated outputs are not committed
- PostgreSQL stores durable analytical outputs
- systemd keeps services and nightly jobs alive after reboot
- `.env` stores recoverable local secrets outside git
- `/etc/opencare/*.env` or `/etc/stockml/*.env` stores systemd runtime config

## Future Business Goal

Build an OpenCare production workflow where analytical outputs can be generated nightly, stored in a database, and surfaced through a portal/API without manual recovery after server restart.

This pattern should support OpenCare use cases such as:

- patient cohort intelligence
- provider performance dashboards
- operational risk scoring
- appointment and care-gap prediction
- nightly quality reports
- portal-ready decision support outputs

## Future Primary Actors

- Platform operator: installs and maintains VM services.
- Data pipeline owner: owns ingestion, feature generation, scoring, and quality checks.
- Portal/API user: consumes dashboards, summaries, and decision outputs.
- DevOps/GitHub Actions: validates code before it reaches the VM.

## Candidate Future Workflow

1. Developer changes code locally.
2. Code is pushed to GitHub.
3. GitHub Actions runs compile and test checks.
4. VM pulls latest `main`.
5. VM installers create persistent runtime config from `.env`.
6. PostgreSQL is installed/enabled and schema is initialized.
7. Nightly systemd timers run ingestion, feature building, model/scoring, and DB load.
8. Portal starts automatically and reads latest outputs.
9. After reboot, PostgreSQL, portal, timers, and runtime config recover automatically.

## Candidate Functional Requirements

### Repository and Deployment

- Local repo remains the only source of truth.
- VM must not contain untracked code changes needed for production behavior.
- GitHub Actions must pass before VM pull.
- Large generated data files must not be committed.
- Deployment scripts must be idempotent.

### Runtime Configuration

- Secrets must live in a repo-local `.env` file that is ignored by git.
- A template file, `.env.template`, must document required variables.
- Install scripts must copy runtime config into a systemd-readable file under `/etc/opencare/`.
- Reboot must not require re-exporting environment variables manually.

### Database Persistence

- PostgreSQL must be installed and enabled as a system service.
- Database/user/schema creation must be repeatable.
- Existing database data must not be deleted by reinstall scripts.
- Large CSV or parquet outputs must load in chunks.
- Loader must normalize missing values before typed database inserts.
- Each major dataset should commit independently where possible.

### Scheduled Jobs

- systemd timers must run nightly jobs.
- Timers must survive reboot.
- Job order must be explicit.
- Logs must be available through `journalctl`.
- Failures must not silently corrupt prior successful outputs.

### Portal/API

- Portal must start after reboot.
- `/health` route must report status, project root, latest output files, and timestamp.
- Missing data should show empty states, not crashes.
- Portal should be designed to migrate from CSV reads to database reads.

## Candidate Non-Functional Requirements

- Reboot-safe
- Idempotent deployment
- No committed secrets
- No committed large outputs
- Scalable batch loading
- Clear operational commands
- Clear health checks
- CI-validated shell scripts and Python code

## Candidate Data Flow Pattern

```text
source systems
  -> ingestion layer
  -> validated raw/interim outputs
  -> feature or analytical panel
  -> model/scoring layer
  -> portal/API outputs
  -> PostgreSQL persistence
  -> portal/API/dashboard consumption
```

## OpenCare Mapping To Define During Planning

| StockML Pattern | OpenCare Equivalent |
| --- | --- |
| equity universe | patient/provider/facility universe |
| price history | appointment, claims, encounter, or operational history |
| metadata enrichment | patient/provider/facility context |
| feature panel | care, operational, or risk feature panel |
| Gold ML dataset | model-ready OpenCare intelligence dataset |
| Long/Short/No Decision | Action / Escalate / No Action or Review Needed |
| portal outputs | OpenCare dashboard/API outputs |
| PostgreSQL persistence | OpenCare analytics database |
| systemd timers | nightly OpenCare jobs |

## Acceptance Criteria

- Fresh VM can be provisioned from repo scripts.
- `.env` stores recoverable local runtime secrets and is ignored by git.
- `/etc/opencare/opencare.env` or equivalent persists runtime config for systemd.
- PostgreSQL remains available after reboot.
- Portal/API remains available after reboot.
- Nightly timers remain active after reboot.
- Latest analytical outputs can be loaded into PostgreSQL without OOM failures.
- Health endpoint returns HTTP 200 after reboot.
- CI validates Python compilation, tests, and deployment shell syntax.

## Recommended Implementation Phases

### Phase 1: Platform Skeleton

- Add `.env.template`.
- Add install scripts for database, portal/API, and scheduler.
- Add systemd service/timer assets.
- Add `/health`.
- Add CI checks.

### Phase 2: Durable Data Layer

- Add PostgreSQL schema.
- Add chunked database loaders.
- Add ingestion run audit table.
- Add typed value cleanup for missing values.

### Phase 3: Portal/API Integration

- Switch portal/API services from flat-file reads to database queries.
- Add pagination and summary endpoints.
- Add operational empty states.

### Phase 4: Production Hardening

- Move secrets to a managed secret store when available.
- Add database backups.
- Add log rotation.
- Add monitoring alerts.
- Add rollback procedure.

## Operational Verification Commands

```bash
sudo systemctl status postgresql --no-pager
sudo systemctl status opencare-portal --no-pager
sudo systemctl list-timers 'opencare-*'
curl http://127.0.0.1:8091/health
```

Database verification:

```bash
sudo -u postgres psql -d opencare -c "\dt"
sudo -u postgres psql -d opencare -c "select * from ingestion_runs order by created_at desc limit 10;"
```

## Key Lesson From StockML

The most important production lesson is that reboot persistence is not only about keeping services enabled. The platform must also persist runtime configuration, database credentials, scheduler settings, and generated analytical state. Otherwise, a VM can appear healthy after reboot while manual CLI jobs or nightly jobs fail because environment variables were lost.

## Parking Lot For Future Planning

Before implementation starts, OpenCare should decide:

- Which OpenCare dataset is the first target: patients, appointments, claims, providers, facilities, or care gaps.
- Which database schema should be canonical.
- Whether the portal/API should read from PostgreSQL from day one or start with file-backed outputs.
- Whether secrets remain in VM `.env` files or move immediately to a managed secret store.
- What nightly schedule and data freshness targets are required.
- What counts as an actionable output versus a diagnostic-only output.
- What compliance, access control, and audit requirements apply.

## Backlog Candidate Tickets

1. Define OpenCare analytical dataset grain and schema.
2. Create OpenCare `.env.template` and reboot-safe systemd env file pattern.
3. Add PostgreSQL schema and idempotent install script.
4. Add chunked output loader with ingestion audit table.
5. Add health endpoint and systemd service for portal/API.
6. Add nightly timer assets and install script.
7. Add CI checks for Python, tests, and deployment shell scripts.
8. Add database-backed portal/API queries.
9. Add backup and restore procedure.
