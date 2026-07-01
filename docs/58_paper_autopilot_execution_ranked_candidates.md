# Paper Autopilot Execution-Ranked Candidates

Paper Autopilot now reads the newest `data/portal_outputs/execution_ranked_candidates_*.csv` artifact before falling back to older candidate sources.

Only candidates with all of the following are passed into the guarded open path:

- `status = executable`
- non-empty `execution_rank`
- `side` of `buy` or `sell`
- not `research_only`
- `executable = true` when present
- empty block reasons
- actionable Long/Short direction

Candidates are scanned by ascending `execution_rank`. Raw rank remains diagnostic only.

The actual paper order still goes through the existing Paper Autopilot runtime guards:

- Paper-only guard and live-trading disabled check
- submit-orders configuration
- kill switch
- basket risk
- validation cycle/day/position caps
- existing held-symbol and open-order checks
- model evidence and entry alignment
- session order policy
- overnight tradability
- quote/spread checks
- position-intent guard

Standalone basket submission remains blocked while `execution_owner = paper_autopilot`.

For a read-only tick diagnostic:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_paper_autopilot_tick.py --dry-run
```
