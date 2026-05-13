# Branching And Deployment

StockML uses two long-lived branches:

- `main`: stable production branch for the VM portal and trading service.
- `dev`: active development branch for code changes, experiments, and patches before promotion.

## Rules

- The VM website should normally pull from `main`.
- New patches should be made on `dev`.
- Promote `dev` to `main` only after tests pass and the portal can render locally or on a staging check.
- Do not resolve production config conflicts directly during a risky pull. Preserve local VM config first.
- Runtime data under `data/` is not a deployment artifact.

## VM Update Flow

For production-safe updates:

```bash
cd /home/massa/stock-market-ml-platform
git fetch origin
git checkout main
git status --short
git pull --ff-only origin main
PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest tests/test_autopilot_auto_open.py tests/test_near_miss_analysis.py tests/test_portal_routes.py -q
sudo systemctl restart stockml-portal
```

If local config files are modified, inspect before pulling:

```bash
git diff -- config/autopilot.yaml config/eod.yaml config/meta_labeling.yaml
```

Use a stash or local commit for VM-only config before pulling `main`.

## Development Flow

For active work:

```bash
git checkout dev
git pull --ff-only origin dev
```

After implementation:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest tests -q
git push origin dev
```

Then merge or pull-request `dev` into `main` only when the change is safe for the VM.

## Emergency Rule

If the website is broken, stop deploying from `dev`. Fix forward on `dev`, test, then promote to `main`. Only hotfix `main` directly for small production recovery patches.
