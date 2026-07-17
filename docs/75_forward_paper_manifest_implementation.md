# Forward Paper Manifest Implementation

The forward-paper manifest records the code commit, strategy/gate config hashes, latest model and trading artifacts, and paper/live safety flags for each daily run.

Implemented paths:
- `src/stockml/trading/config_fingerprint.py`
- `src/stockml/trading/forward_paper_manifest.py`
- `scripts/run_forward_paper_manifest.py`

Production path:
- `src/stockml/pipeline/profile_runner.py` writes a `forward_paper_manifest` stage on each profile run.
- The standalone script can write the same manifest after ad-hoc paper-trading diagnostics.

Output:
- `data/trading/forward_paper/forward_paper_manifest_YYYYMMDD.csv`

Safety:
- The manifest is read-only and does not submit orders.
- `live_trading_enabled=True` marks the paper program `not_fit_for_review`.
- A changed config hash marks the next manifest as `segmented_by_material_change`.
