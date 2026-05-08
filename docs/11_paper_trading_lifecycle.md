# Paper Trading Lifecycle

The current lifecycle is intentionally paper-only:

1. Model signal is generated.
2. Trade quality gate approves, reduces, or rejects the candidate.
3. Order plan is written with sizing and exit levels.
4. Rejected rows are written to results with readable reasons.
5. Eligible approved or reduced rows can enter Alpaca paper submission when paper submission is explicitly enabled.
6. Tracking snapshots record order and position status.

Live trading is disabled by policy. If live trading is enabled in configuration, the runner raises a hard error instead of submitting orders.

Every eligible order must include:

- approved notional
- suggested quantity
- stop loss price
- take profit price
- max holding days
- trade quality status and reason

Lifecycle artifacts are written under `data/trading/`:

- `paper_trade_journal/`: merged order plan and order result lifecycle state
- `paper_pnl/`: position-level P&L snapshots
- `paper_positions/`, `paper_orders/`, `paper_fills/`: reserved for deeper paper execution history
- `agent_decisions/`, `execution_reports/`: reserved for monitor and execution-agent outputs

Use `scripts/run_paper_trading_cycle.py` after generating or tracking paper orders to materialize the journal and P&L snapshots.

## Position Decision Cycle

Use `scripts/run_position_decision_engine.py` after refreshing Alpaca order tracking to create an active position decision snapshot under `data/trading/agent_decisions/`.

The decision engine does not create orders. It compares open paper positions with the latest order plan, stop-loss/take-profit levels, signal age, and position P&L. It emits:

- `hold`: position is still inside rules.
- `watch`: position needs rescore or manual review, usually because the signal is stale.
- `close`: stop loss, take profit, max holding period, or signal reversal was detected.

The default intraday signal TTL is 10 minutes. This TTL controls the decision snapshot only; it does not rerun the model or create a new trade.
