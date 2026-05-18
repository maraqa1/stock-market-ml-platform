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

For continuous paper-position review, install the systemd timer:

```bash
cd /home/massa/stock-market-ml-platform
sudo bash deployment/vm/install_alpaca_auto_trader.sh
```

This installs `stockml-position-monitor.timer`, which runs every 30 seconds during the market window. Each run refreshes Alpaca paper order/position tracking and writes:

- latest tracking snapshot
- latest paper positions snapshot
- paper trade journal
- paper P&L snapshot
- position decisions under `data/trading/agent_decisions/`

The monitor does not submit new entry orders and does not close positions by itself. It creates the review layer needed before adding an automatic close/rebalance executor.

## Paper Autopilot Exit Rules

Paper Autopilot may submit paper-only close orders from the synchronized intraday trading clock. The current exit rules are deliberately simple and defensive:

- Hard stop: close when unrealized return is at or below `-4.0%`.
- Defensive stale-signal stop: close when the signal is stale and unrealized return is at or below `-2.5%`.
- Defensive unknown-signal stop: close when the signal is unknown and unrealized return is at or below `-2.0%`.
- Trailing profit protection: once a position's peak unrealized return reaches at least `+3.0%`, close if it gives back at least `1.5%` from that peak while the signal is stale or unknown.

Paper Autopilot stores high-water marks in `position_peak_plpc` inside `data/portal_outputs/paper_autopilot_state.json`. These peaks are used for trailing profit protection; they are not model scores and they do not change candidate ranking.

These rules are implemented in `src/stockml/trading/paper_autopilot.py` as `HARD_STOP_LOSS_THRESHOLD`, `DEFENSIVE_STALE_LOSS_THRESHOLD`, `DEFENSIVE_UNKNOWN_LOSS_THRESHOLD`, `TRAILING_PROFIT_MIN`, and `TRAILING_GIVEBACK_THRESHOLD`. Changing them is a risk-control change and should be tested separately from model, threshold, or provider work.

## Scheduler Synchronization

The paper lifecycle has two coordinated clocks:

- Daily research clock: builds the universe, prices, metadata, features, sentiment, Gold dataset, model outputs, and then a plan-only paper candidate pool for the portal.
- Intraday trading clock: refreshes candidate snapshots, scores promotions, writes rotation recommendations, and ticks Paper Autopilot.

Use explicit UTC for every production systemd timer. The market-session jobs already use UTC; nightly timers should also use UTC so their behavior is independent of the server timezone.

The intraday trading clock should run as a single sequential service every 5 minutes during market hours:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_intraday_candidate_refresh.py
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_intraday_promotion_scoring.py
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_rotation_recommendations.py
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_paper_autopilot.py tick
```

Do not schedule those four commands as unrelated timers. The later stages depend on fresh rows from the earlier stages, and independent timers can leave the portal showing stale candidates, stale promotions, or delayed auto-open/EOD state.
