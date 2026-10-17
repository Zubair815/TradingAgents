# Strategy validation protocol

Status: **protocol defined; no profitability claim**.

This protocol fixes the evaluation rules before final results are inspected. It
does not authorize live trading and it does not turn a passing software test
suite into evidence of a profitable strategy.

## Data and partitions

- Use timestamped, point-in-time inputs only. Each decision may see data whose
  publication or bar-close time is no later than that decision's cutoff.
- Keep an untouched chronological out-of-sample segment of at least 20% of the
  available history. Do not use it for prompt, rule, threshold, or model tuning.
- Run rolling walk-forward evaluation with development windows preceding their
  validation windows. Report every window, including windows with no trades.
- Freeze the revision, configuration, symbol universe, dates, and data-source
  identifiers before evaluating the final out-of-sample segment.

## Execution assumptions

Apply recorded or conservatively estimated bid/ask spread, slippage, commission,
swap, and financing costs. A result is invalid when a required cost is silently
treated as zero. Reject fills outside available bar ranges and prevent decisions
from executing before their evidence cutoff.

## Predeclared minimum evidence thresholds

These are screening thresholds, not guarantees:

- At least 100 closed out-of-sample trades in aggregate and at least 20 in each
  reported market regime; otherwise classify the evidence as insufficient.
- Positive net return after all modeled costs and profit factor above 1.10.
- Maximum peak-to-trough drawdown no greater than 15% and no configured risk
  limit breach.
- A positive lower 95% bootstrap confidence bound for mean return per trade.
- Performance must exceed both a no-trade cash benchmark after financing and a
  simple disclosed directional benchmark on the same dates and costs.
- No single symbol, month, or trade may contribute more than 25% of total net
  profit; otherwise report concentration and fail the robustness screen.
- Walk-forward results must be positive in at least 60% of validation windows.

Threshold failure means **not validated**. Threshold passage means only that the
strategy may proceed to paper-forward observation; it is not production or
profitability acceptance.

## Paper-forward gate

After the historical configuration is frozen, observe at least 30 calendar days
and 30 closed demo trades. Reconcile proposed, observed, and closed trades,
including rejected signals, partial fills, commission, swap, slippage, restarts,
and delayed broker updates. Compare paper-forward distributions with the frozen
out-of-sample ranges. Any code or parameter change restarts this gate for the new
revision.

## Required evidence

Preserve the exact Git revision, dirty-tree status, execution manifest, data
cutoffs and source identifiers, cost model, partition definitions, per-window
metrics, benchmark results, uncertainty calculation, and all exclusions. Mark
missing broker or provider evidence as unavailable rather than estimating it.
