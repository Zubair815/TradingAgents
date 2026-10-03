# Phase 30: Historical Forex backtest integrity

## Root cause

The API built demo candles before checking the requested mode. The historical
backtester accepted unsourced candles and unconditionally marked both empty and
completed results as validated. The API repeated that claim. Analysis also saw
full candle OHLC at the candle's opening timestamp, and graph risk decisions were
not applied to the trader proposal.

## Implemented behavior

- Historical mode branches before any demo candle construction. It requires
  explicit start/end dates and retrieves the configured MT5 or Yahoo source
  through the existing Forex adapter with automatic fallback disabled.
- Date-only boundaries mean midnight UTC. The end is the latest allowed candle
  close, not an inclusive calendar day. `count` remains a demo setting; historical
  requests fetch the requested range without silently truncating it.
- The API rejects caller-supplied historical candles because their origin cannot
  be verified. Unavailable or invalid historical data returns HTTP 422 with
  `HISTORICAL_DATA_UNAVAILABLE`; pipeline failures produce no saved success result.
- The Python historical runner requires matching provider provenance, completed
  candles, valid OHLC/volume/spread, chronological unique timestamps, timeframe
  alignment, coverage without unexpected market gaps, and at least two candles.
  Future and out-of-period data fail. Lower-resolution execution data also needs
  matching, validated provenance.
- Analysis runs at candle close. Graph market tools read only that run's completed
  candle window, including complete higher-timeframe aggregates. Market fills use
  the observed close with configured friction; they cannot fill at the earlier
  opening price. Existing engine order, margin, stop, target, swap, and excursion
  calculations remain in use.
- Graph proposals must have a typed risk decision. Rejections are not executed;
  approved direction, lot size, and modified levels reach the execution engine.
- Archived calendar coverage is mandatory for graph evaluation. News is optional
  and archive-only. Calendar and news requests are capped to the run's cutoff even
  if a caller requests a future timestamp or omits the timestamp. Unverified
  intraday macro series report `MACRO_UNAVAILABLE`. Learned journal context is
  excluded because it has no historical availability contract.
- Provider, canonical quick/deep model settings, token limits, analyst selection,
  and research depth reach graph creation. Standard/deep select one/three debate
  rounds through the existing graph configuration.
- Reports include source, vendor symbol, canonical pair, timeframe, requested and
  actual coverage, retrieval time, quality checks, execution-market flag, cutoff
  policy, models, analysts, strategy version, and execution assumptions. No
  credentials are copied from graph configuration into reports.
- Historical completion yields `PARTIALLY_VALIDATED`, with explicit reasons and
  `validated_strategy_performance=false` at both report and engine-result levels.
  Demo results are labeled `DEMO`, illustrative, and false. Walk-forward and
  ablation comparisons also explicitly disavow statistical validation; sample
  ranking functionality is preserved.

## Changed files

- `web/forex_routes.py`
- `tradingagents/backtest/historical_data.py` (new)
- `tradingagents/backtest/agent_backtester.py`
- `tradingagents/backtest/forex_engine.py`
- `tradingagents/backtest/walk_forward.py`
- `tradingagents/backtest/ablation.py`
- `tradingagents/dataflows/forex_context.py`
- `tradingagents/dataflows/forex_data.py`
- `tradingagents/dataflows/forex_news.py`
- `tradingagents/dataflows/trading_economics.py`
- `tradingagents/agents/utils/forex_macro_tools.py`
- `tradingagents/graph/forex_graph.py`
- `tests/test_historical_integrity.py` (new)
- `tests/fixtures/eurusd_yahoo_historical.json` (new)
- `tests/test_forex_historical_agent_backtest.py`
- `tests/test_ablation_engine.py`
- This document.

## Verification

Commands used the repository's `.venv\Scripts\python.exe -m` prefix.

Baseline:

- `ruff check .`: `All checks passed!`
- `pytest -q`: `1923 passed, 5 skipped, 20 warnings, 88 subtests passed in 61.36s (0:01:01)`

After implementation:

- `ruff check .`: `All checks passed!`
- `pytest -q`: `1954 passed, 5 skipped, 20 warnings, 88 subtests passed in 48.04s`
- `pytest -q tests/test_forex_historical_agent_backtest.py`: `10 passed in 3.25s`
- `pytest -q tests/test_walk_forward.py`: `21 passed in 2.20s`
- `pytest -q tests/test_ablation_engine.py`: `9 passed in 2.38s`
- `pytest -q tests/test_forex_routes.py`: `85 passed in 7.68s`

The new integrity suite adds 31 parametrized cases covering source rejection,
missing coverage, invalid candles, pipeline failures, demo labeling, real Yahoo
fixture execution, cutoff-bound tool access, later news/revisions, fill timing,
risk rejection, lower-timeframe provenance, and historical memory exclusion.
Existing ForexBacktestEngine tests pass in the full suite. Existing generated
execution fixtures now explicitly use demo mode, retaining their execution
assertions; obsolete assertions claiming validated synthetic performance were
replaced with assertions of the corrected contract.

## Remaining limitations

- Historical runs need actual provider range coverage and contemporaneously
  observed calendar archives. Missing mandatory evidence fails explicitly.
- Yahoo OHLC is labeled non-execution-market history. It cannot establish exact
  broker fills. Provider data and trusted Python adapter metadata are not a
  cryptographic proof of origin.
- Two bars suffice for a mechanical simulation, not statistical inference or
  indicator warmup. No statistical strategy-validation procedure was introduced.
- LLM pretrained knowledge cannot be guaranteed historical. Macro intraday
  availability remains unverified and is withheld; optional missing news is not
  fabricated. These limitations preclude a fully validated performance claim.
- Close-price fills assume zero decision latency plus the configured costs.
  Intrabar OHLC ambiguity still uses the existing conservative engine behavior
  or verified lower-timeframe data.
- The real Yahoo fixture was fetched through the existing adapter. Automated
  graph tests use controlled pipeline doubles; no paid live LLM performance
  validation was performed. Five baseline environment-dependent skips remain.

## Target-state completion classification

Historical backtesting, walk-forward evaluation, and ablation are engineering
complete against the target-state SRS. Their non-validated performance labels are
mandatory safety behavior, not missing implementation.

| Requirement group | Engineering status | Evidence boundary |
| --- | --- | --- |
| BT-001–005 | COMPLETE | Chronological cutoff-safe replay, post-decision eligibility, unfilled terminal proposals, and fail-closed sizing are enforced. |
| BT-006–012 | COMPLETE | Explicit friction, evolving account/margin, shared risk rules, historical conversion/ATR, labelled broker assumptions, and conservative or lower-timeframe ambiguity handling are enforced. |
| BT-013–014 | COMPLETE | Results retain `validated_strategy_performance=false` and report provenance, assumptions, ambiguity, and unavailable evidence. |
| WF-001–006 | COMPLETE | Chronological development/OOS isolation, expanding windows, warmup exclusion, complete-tail accounting, unavailable nonpositive-return WFE, and heuristic-only labels are enforced. |
| WF-007 | COMPLETE | Every ablation variant receives identical input candles and period boundaries. |
| WF-008 | COMPLETE | OOS optimization is rejected or explicitly tainted; parameters cannot be presented as untouched OOS after fitting. |
| AN-008 | COMPLETE | Ablation comparisons are descriptive and cannot become a validated strategy claim from trade count alone. |

`STRATEGY_RESEARCH_VALIDATED` remains a separate release state requiring an
explicit statistical research protocol and evidence. It is not required for
engineering completeness and must not be inferred from these tools.

Suggested commit: `Phase 30: Harden historical backtest integrity`
