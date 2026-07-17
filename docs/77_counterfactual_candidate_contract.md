# Counterfactual Candidate Contract

The forward-paper program now logs every candidate row, including blocked and shadow rows, with the decision-time price.

Outputs:
- `data/trading/forward_paper/counterfactual_candidates_YYYYMMDD_HHMMSS.csv`
- `data/trading/forward_paper/counterfactual_forward_returns_YYYYMMDD_HHMMSS.csv`

Production path:
- `src/stockml/trading/paper_trader.py` writes the counterfactual candidate log whenever the paper trader builds a candidate pool.
- This happens even when no broker orders are submitted.

Forward-return attachment:
- `scripts/run_counterfactual_forward_returns.py` joins the counterfactual log to the latest Gold outcomes.
- Missing forward outcomes are marked `insufficient_data`.

Safety:
- Read-only. No order submission and no gate changes.
- Live trading remains disabled by existing platform safeguards.
