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
