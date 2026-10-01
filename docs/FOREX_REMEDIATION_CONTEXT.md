# Forex Remediation Context

> Phase 0 architecture map plus the sequential Phase 1-9 remediation record.
> Phase 9 verified HEAD `4d2152914fc3ae85249b04126645339eaf755e79`
> (`Version2.0`) with the uncommitted remediation worktree on 2026-10-01.

## 1. Baseline

- Checkout: managed worktree at `origin/main`; detached `HEAD`, clean before this file.
- HEAD: `4d2152914fc3ae85249b04126645339eaf755e79`.
- `git log --oneline -10`: `4d21529 Version2.0`, `6f38596 Version1.9`,
  `18cdbea Version1.8`, `53bdf53 Version1.7`, `ae2a7d5 Version1.6`,
  `68dc83e Version1.5`, `db538c3 Version1.4`, `3a37664 Version1.3`,
  `dc05ec3 Phase 29: Complete MT5 interface`, `3150fd0 Version 1.2%`.
- The worktree has no `.venv`; gates were run against this worktree with
  `D:\TradingAgents\.venv\Scripts\python.exe`.
- `python -m ruff check .`: **pass**, `All checks passed!`.
- `python -m pytest -q -m "not e2e"`: **fail**, `1 failed, 2108 passed,
  5 skipped, 6 deselected, 37 warnings, 88 subtests passed` in 88.97 s.
  The failure is
  `tests/test_forex_graph.py::TestForexGraphConfiguration::test_env_var_precedence_in_forex_graph`:
  expected `gemini-2.5-flash`, received normalized `google/gemini-2.5-flash`.
  This is a current baseline defect, not one of the 18 risk gaps and was not changed.
- `python -m pytest -q -m e2e`: **pass**, `6 passed, 2114 deselected` in 46.72 s.
- `git diff --check`: **pass** before documentation creation.

### Phase 1 foundation (2026-09-30)

- Added immutable `tradingagents/risk/context.py` composition models:
  `ForexRiskContext`, `ForexMarketContext`, `ForexPortfolioContext`, and
  `ForexConversionRate`, with explicit `AVAILABLE`, `UNAVAILABLE`, and
  `NOT_APPLICABLE` conversion states.
- The context reuses `ForexAccountProfile`, `BrokerExecutionConstraints`, and
  `OpenPosition`; it deep-detaches mutable caller inputs, normalizes UTC timestamps,
  rejects future observations, and round-trips broker/portfolio data.
- `BrokerExecutionConstraints` now also carries optional broker symbol, digits,
  point, and pip size without changing existing defaults.
- `ForexTradingAgentsGraph` and its deterministic risk node accept an optional
  `risk_context`. Existing callers remain compatible. When supplied, market
  spread/ATR, account, broker constraints, positions, and explicit conversion data
  reach the existing risk/sizing engines.
- Explicitly unavailable required conversion makes sizing unexecutable; the context
  does not silently invent a rate. Live MT5 retrieval remains intentionally unwired
  until Phase 2.
- Focused gate: `57 passed, 1 deselected` across risk-context, sizing, and graph
  tests; the deselection is the pre-existing Google model normalization baseline failure.
- Phase 1 full non-E2E gate: `1 failed, 2116 passed, 5 skipped, 6 deselected,
  37 warnings, 88 subtests passed` in 71.74 s. The sole failure is the same
  pre-existing Google model normalization assertion recorded in the Phase 0 baseline;
  Phase 1 introduced no additional full-suite failure.

### Phase 2 deterministic conversion arithmetic (2026-09-30)

- Added `tradingagents/forex/conversion.py` as the shared low-level contract for
  directional, timestamped conversion observations. A rate always means one unit
  of `from_currency` in `to_currency`; direct and inverse observations are resolved
  explicitly, with optional point-in-time freshness enforcement.
- Pip value and lot sizing now fail closed when quote-to-account conversion is
  required but unavailable. A pair price is used as conversion only for the valid
  account-base case (for example USDJPY in a USD account).
- Margin converts base currency to account currency independently from pip-value
  conversion. Cross-pair prices are no longer treated as account conversion rates.
- MT5 positions with no stop now carry `stop_loss=None` and `risk_amount=None`.
  Portfolio sizing rejects a new proposal while any existing position has unbounded
  risk. Stopped positions use deterministic pair/account pip conversion instead of
  the former hard-coded `$10/pip/lot` estimate.
- The historical engine accepts immutable point-in-time conversion observations
  and supplies them to margin, P&L, floating P&L, spread, and slippage calculations;
  it does not retrieve live data.
- Phase 2 focused gate: `126 passed` across conversion/pips, sizing, MT5, backtest,
  and risk-context tests. Repository-wide Ruff and `git diff --check` pass.
- Phase 2 full non-E2E gate: `1 failed, 2128 passed, 5 skipped, 6 deselected,
  37 warnings, 88 subtests passed` in 69.81 s. The sole failure remains the
  pre-existing Google model normalization assertion recorded in the Phase 0 and
  Phase 1 baselines; Phase 2 introduced no additional full-suite failure.

### Phase 3 live MT5 risk-context assembly (2026-09-30)

- The live web worker now builds a complete `ForexRiskContext` when
  `account_source="mt5"` and passes it into `ForexTradingAgentsGraph`.
- Account balance/equity, used/free margin, currency, leverage, and broker margin
  thresholds come from the typed read-only MT5 account snapshot. Broker symbol,
  precision, pip/point sizes, contract size, and volume bounds come from the MT5
  symbol specification rather than generic defaults.
- Live bid/ask/spread and timestamp come from the validated MT5 tick. ATR is
  calculated deterministically from completed MT5 candles at the same observation
  cutoff and execution timeframe; no LLM text is parsed for ATR.
- The observer supplies directional MT5 conversion observations for required base
  and quote currencies. All current MT5 positions are converted with those Phase 2
  semantics before entering the immutable portfolio snapshot.
- Quote validation now separates an extreme-data sanity ceiling from the user's
  configured spread policy. The actual spread therefore reaches `ForexRiskEngine`,
  where both maximum-spread and spread/ATR rules execute.
- Explicit disconnected MT5 requests fail before graph construction and never use
  manual account defaults. MT5 remains observation-only; no execution method was added.
- Focused live integration gate: `204 passed, 17 warnings`. Full non-E2E gate:
  `1 failed, 2131 passed, 5 skipped, 6 deselected, 37 warnings, 88 subtests passed`
  in 70.19 s; the sole failure is the unchanged Google model normalization baseline.
  E2E gate: `6 passed, 2137 deselected`. Repository Ruff and `git diff --check` pass.

### Phase 4 deterministic portfolio safety (2026-09-30)

- `max_currency_exposure_percent` now means the maximum absolute net stop-loss
  risk attributed to one currency, divided by current account equity. Each bounded
  position assigns its account-currency stop risk positively to the long currency
  and negatively to the short currency; opposing exposures net. Raw lots are not
  compared with a percentage. Unknown risk remains fail-closed.
- Cumulative risk enforcement uses the sum of existing account-currency stop risks
  plus the correlation-adjusted proposed risk against `max_account_risk_percent`.
  Correlation scaling is applied once to the proposal before cumulative and currency
  concentration checks.
- `max_open_positions` is enforced by both the live risk decision and sizing engine.
  It counts only current open positions from MT5; pending orders are explicitly
  excluded from this limit and remain governed separately.
- Optional deterministic daily limits support percentage and absolute amounts. The
  percentage is based on reconstructed UTC day-start balance. Live realized P&L is
  the sum of MT5 BUY/SELL deal profit, commission, swap, and fees from 00:00 UTC to
  the risk snapshot cutoff. If an enabled rule lacks authoritative deal history,
  risk and sizing fail closed. Sizing also reserves enough headroom for the proposed
  stop loss rather than checking only loss already realized.
- `NO_TRADE` remains zero-risk and does not consume portfolio capacity. No broker
  write or automated execution behavior was introduced.
- Focused portfolio/live gate: `213 passed, 17 warnings`. Full non-E2E gate:
  `1 failed, 2140 passed, 5 skipped, 6 deselected, 37 warnings, 88 subtests passed`
  in 73.03 s; the only failure remains the documented Google model normalization
  baseline. E2E gate: `6 passed, 2146 deselected`. Ruff and `git diff --check` pass.

### Phase 5 explicit API account modes (2026-09-30)

- Analysis requests now validate `account_source` as `"mt5"` or `"manual"` and
  continue to default to MT5. An omitted source therefore means MT5; disconnected
  MT5 fails with `DATA_INSUFFICIENT` before graph construction and cannot become a
  manual `$100,000` account.
- Manual analysis requires caller-supplied balance, equity, free margin, leverage,
  and three-letter account currency. Known zero free margin is preserved; missing
  or negative free margin is never replaced with balance or equity.
- `POST /api/forex/proposals/size` is explicitly a `MANUAL_ESTIMATE`. Its response
  declares that it is not broker verified, lists omitted live portfolio/conversion
  evidence, and identifies broker constraints as either fully caller supplied or
  a labeled standard-FX estimate. Partial broker specifications are rejected.
- `POST /api/forex/proposals/evaluate-risk` is explicitly a manual, non-broker-
  verified evaluation and delegates to the same `ForexRiskEngine` used by normal
  analysis. Caller-supplied spread/ATR and policy thresholds retain their normal
  deterministic meanings; no parallel risk behavior was introduced.
- Focused API/risk/sizing gate: `162 passed`. Full non-E2E gate: `1 failed,
  2147 passed, 5 skipped, 6 deselected, 22 warnings, 88 subtests passed` in
  58.07 s; the sole failure is the unchanged Google model normalization baseline.
  E2E gate: `6 passed, 2153 deselected`. Repository Ruff and
  `git diff --check` pass.

### Phase 6 historical deterministic parity (2026-09-30)

- Every historical analysis cutoff now receives an immutable simulated account
  snapshot plus typed positions that are open at that cutoff. Balance, equity,
  used/free margin, position count, and each position's account-currency stop risk
  therefore reach the same `ForexRiskContext` and sizing node used by live analysis.
- Historical conversion observations are explicit directional, timestamped inputs
  on `AgentBacktestConfig`. Only observations at or before the decision cutoff are
  exposed to the graph. Actionable cross/account-currency proposals without the
  required quote-to-account observation raise `HistoricalDataUnavailable`.
- The simulator resolves conversions independently at every event timestamp for
  pip value, realized and floating P&L, margin, spread, and slippage. USD-denominated
  commission and swap settings are converted to non-USD account currency at the
  applicable fill or rollover timestamp. No live/current-rate lookup was added.
- Historical market/account/portfolio/conversion inputs are assembled into the
  shared deterministic risk context. An identical USDJPY snapshot produces the
  same sizing result and pip value through the live risk evaluator.
- Focused historical-integrity/backtest gate: `67 passed`. Full non-E2E gate:
  `1 failed, 2151 passed, 5 skipped, 6 deselected, 22 warnings, 88 subtests
  passed`; the sole failure is the unchanged Google model normalization baseline.
  E2E gate: `6 passed, 2157 deselected`. Repository Ruff and
  `git diff --check` pass.

### Phase 7 walk-forward semantics (2026-10-01)

- Return-based WFE is defined only when the mean development return is positive.
  Zero or negative development return yields `walk_forward_efficiency_ratio=None`
  with `UNAVAILABLE_NONPOSITIVE_DEVELOPMENT_RETURN`; no epsilon denominator is
  invented.
- `ROBUST`, `MARGINAL`, and `OVERFITTED` are emitted only when WFE is available.
  They remain descriptive heuristic labels, not statistical validation. Invalid
  WFE yields the explicit `UNAVAILABLE` verdict; OOS contamination remains `TAINTED`.
- Multi-split validation now uses an expanding-window methodology. One initial
  development/validation prefix is followed by disjoint chronological OOS blocks
  that partition the entire remaining tail. Later splits may use only timestamps
  completed before their own OOS block, including earlier completed OOS blocks.
  There is no implicit final holdout and no unexplained discarded tail.
- Focused walk-forward gate: `35 passed`. Full non-E2E gate: `1 failed,
  2154 passed, 5 skipped, 6 deselected, 22 warnings, 88 subtests passed`; the
  sole failure is the unchanged Google model normalization baseline. E2E gate:
  `6 passed, 2160 deselected`. Repository Ruff and `git diff --check` pass.

### Phase 8 cancellation/persistence linearization (2026-10-01)

- Forex proposal persistence now runs inside a short orchestration-owned guard
  using the existing run-state lock. The guard performs the final cancellation
  check and covers only the SQLite proposal write/supersession operation; no LLM
  or market-data call runs while the lock is held.
- Cancellation and persistence now have one observable ordering. If cancellation
  wins, the guarded write raises before `save_proposal()` and the run remains
  terminal `cancelled`. If persistence wins, the run becomes `finalizing` before
  the guard releases and cannot subsequently be relabeled cancelled; it proceeds
  to completed or failed through the existing worker path.
- Repeated cancellation remains idempotent. Existing completed and failed states
  remain terminal, cancelled SSE streams terminate on the cancellation event, and
  cancelled workers cannot publish a successful report or completion event.
- Focused concurrency gate: `15 passed`; broader graph/route/concurrency gate:
  `144 passed, 1 deselected` (the known Google assertion). Full non-E2E gate:
  `1 failed, 2159 passed, 5 skipped, 6 deselected, 22 warnings, 88 subtests
  passed`; the sole failure is the unchanged Google model normalization baseline.
  E2E gate: `6 passed, 2165 deselected`. Repository Ruff and
  `git diff --check` pass.

### Phase 9 final verification (2026-10-01)

- Traced and regression-tested all 22 requested runtime properties across the
  shared conversion/sizing engine, live MT5 context assembly, portfolio guards,
  historical pipeline, walk-forward validator, and cancellation boundary.
- Numeric acceptance is explicit: a USD 10,000 account risking 1% on USDJPY at
  150 with a 20-pip stop produces `6.6667 USD/pip/lot` and `0.75` theoretical
  lots. EURJPY and EURGBP directed-conversion regressions also pass.
- Corrected configuration precedence so persisted settings cannot overwrite
  explicit environment overrides: defaults, persisted settings, environment,
  then explicit per-call configuration.
- The browser now applies the authoritative terminal cancel response immediately,
  closing the small UI/SSE delivery race without changing server terminal-state
  or proposal-persistence guarantees.
- Final gates: focused remediation `376 passed`; Ruff passed; non-E2E `2161
  passed, 5 skipped, 6 deselected, 22 warnings, 88 subtests passed`; E2E `6
  passed, 2166 deselected`; compileall, JavaScript syntax, forbidden broker-write
  search, and `git diff --check` passed.
- No current-worktree GitHub Actions run exists because the remediation remains
  uncommitted. Windows + XM Demo acceptance was not executed. Classification:
  **READY FOR XM DEMO ACCEPTANCE**, not fully live-validated.

## 2. Architecture Summary

The system has two execution families which reuse the Forex graph but assemble
different context around it.

### Live browser path

1. `web/static/app.js::handleForexSubmit()` builds a request and calls
   `POST /api/forex/analyze`. `account_source` is validated as MT5 or manual and
   defaults to MT5. It does not send live quote, ATR, broker specification, or
   portfolio state.
2. `web/forex_routes.py::start_forex_analysis()` validates the body as
   `tradingagents.research.contracts.AnalysisRequest` (aliased as
   `ForexAnalysisRequest`), creates a run/event record and starts the worker.
3. `web/forex_routes.py::_run_forex_analysis()` resolves runtime configuration.
   In MT5 mode it assembles the typed account, live tick, deterministic candle ATR,
   broker constraints, all open positions, and required directional conversions
   into one immutable `ForexRiskContext`. Manual mode requires explicit balance,
   equity, free margin, leverage, and currency and preserves known zero values.
4. The worker creates `ForexTradingAgentsGraph` with `ForexRiskLimits` and the
   complete MT5 risk context. The graph passes actual spread/ATR into
   `ForexRiskEngine` and account/broker/portfolio/conversions into
   `ForexPositionSizingEngine`.
5. `ForexTradingAgentsGraph.create_run_state()` normalizes pair/timeframes,
   establishes the UTC cutoff, retrieves eligible lessons, and calls
   `prepare_live_forex_context()` for market context.
6. The compiled LangGraph executes selected Technical/Macro/News analysts in
   order, then Bull/Bear research, Research Manager, Forex Trader, deterministic
   Forex Risk Evaluator, and Portfolio Manager.
7. `create_forex_risk_evaluator()` reconstructs a typed proposal, calls
   `ForexRiskEngine.validate_proposal()`, then calls
   `ForexPositionSizingEngine.size_proposal()`. The node supplies the complete
   deterministic context, including open-position count, bounded position risks,
   currency exposure inputs, and UTC daily realized P&L availability/value.
8. `create_forex_portfolio_manager()` optionally produces an LLM executive brief,
   maps the deterministic decision to a proposal status, saves the immutable
   proposal/risk evidence, and supersedes older unexecuted proposals for the pair.
   It never creates a broker execution or calls MT5 order APIs.
9. The worker stores the report, updates the in-memory run, and emits SSE events.
   A user may then execute manually in MT5.

### CLI path

`cli/main.py::run_analysis()` prompts for Forex selection, constructs a manual
`ForexAccountProfile` from the entered balance and a `ForexRiskLimits`, creates
the same `ForexTradingAgentsGraph`, and runs the same analyst/debate/trader/risk/
portfolio-manager chain. It has no MT5 context assembly and therefore uses default
broker constraints, no open positions, no conversion rate, and no live spread/ATR.

### Manual execution, observation, journal, and learning path

1. Proposals are decision-support records only. Manual journal endpoints can
   record a user-reported execution; otherwise MT5 is the authoritative broker source.
2. `MT5Observer` exposes read-only account info, symbol specifications, tick,
   candles, positions, pending orders, deals, `to_sizing_account_profile()`,
   `to_broker_constraints()`, and `to_open_positions()`.
3. `MT5ObservationService` polls positions/orders/deals and reconciles them with
   `ForexJournalManager`. It has no order placement/modification/cancellation API.
4. Broker-confirmed opens/fills/partial closes/SL-TP changes/closes are written to
   the journal/timeline. Closed trades are sent to `ClosedTradeProcessor`.
5. Post-close processing obtains holding-window history, computes MFE/MAE and
   execution quality, persists reflection inputs/metadata, and enables lesson
   generation/retrieval. `ForexTradingAgentsGraph.create_run_state()` injects
   eligible prior lessons; historical runs gate lessons by their known-at cutoff.

### Historical/backtest path

1. Web backtest/walk-forward/ablation routes load validated candles and explicit
   provenance, then create `HistoricalForexPipelineConfig` and
   `create_historical_forex_pipeline()`.
2. `HistoricalForexAgentBacktester.run()` iterates completed bars chronologically.
   Before each analysis it settles the bar, creates a point-in-time candle window,
   and calls the pipeline at the completed-bar cutoff.
3. The historical pipeline establishes `historical_market_scope`, pins Trading
   Economics calendar access to the cutoff, creates the real Forex graph, runs it,
   requires typed proposal/risk evidence, and returns only the risk-authorized action,
   levels, and size.
4. The simulator executes at the observed close (or queues LIMIT/STOP orders for a
   later bar), applies spread/slippage/commission/swap, enforces simulator margin
   and `max_open_trades`, updates balance/equity, and force-settles remaining trades.
5. The pipeline receives an evolving `ForexAccountProfile`, typed simulated open
   positions, and conversion observations filtered to the completed-bar cutoff.
6. `ForexWalkForwardValidator` runs independent ledgers per period with warm-up
   context only. `ForexAblationRunner` runs variants over the same supplied sample
   and labels its comparison descriptive rather than statistically validated.

## 3. Risk and Sizing Call Graph

```text
Browser / CLI / historical runner
  -> ForexTradingAgentsGraph(... optional risk_context; legacy account/constraints ...)
    -> create_forex_risk_evaluator()
      -> ForexRiskEngine.validate_proposal(
           proposal, cutoff, account equity/currency,
           current_spread_pips=context.market.spread_pips,
           atr_pips=context.market.atr_pips)
         -> geometry, SL pips, R:R, calendar, market-open, optional spread/ATR,
            risk-percent clamp, legacy lot_size_from_risk()
      -> ForexPositionSizingEngine.size_proposal(
           proposal, account, constraints,
           atr_pips=context.market.atr_pips,
           open_positions=context.portfolio.open_positions,
           current_quote_price=explicit conversion)
         -> pip_value_in_account_currency()
         -> correlation scale
         -> broker volume normalization
         -> calculate_required_margin()
         -> cumulative portfolio-risk ceiling
      -> reconcile sizing result into ForexRiskDecision
    -> Portfolio Manager -> immutable proposal/risk persistence
```

The context arguments above apply only when a caller supplies `risk_context`;
legacy callers still receive their previous defaults. `ForexRiskEngine` and
`ForexPositionSizingEngine` overlap in lot sizing. The graph
ultimately overwrites the risk engine's approved lot with the sizing engine result.
The minimum clean remediation is to assemble one validated, point-in-time input at
the live/historical orchestration boundary and pass its fields to these existing
deterministic components. It need not begin as a large public `ForexRiskContext`
model, but the source/timestamp and missing-data policy must be explicit. Do not
duplicate calculations in routes, frontend code, or LLM prompts.

## 4. MT5 Data Flow

```text
MetaTrader5 Python API
  -> MT5Connection (connect/account transport)
  -> MT5Observer (read-only normalized account/symbol/tick/candles/positions/orders/deals)
     -> web account/status/symbol/tick/position endpoints
     -> MT5ObservationService
        -> ForexJournalManager reconciliation
        -> ForexTradeJournal + lifecycle timeline
        -> ClosedTradeProcessor -> MFE/MAE + execution quality + reflection/lessons
```

`MT5Observer` now assembles the live account, tick, candle ATR, broker constraints,
positions, directional conversion observations, and UTC-day deal P&L consumed by
the live risk context. `MT5Tick` retains observed bid/ask/spread and time provenance.

## 5. Original Issue Map and Resolution

Rows 1-10 preserve the Phase 0 defect descriptions for audit history; they were
resolved by Phases 1-4 and verified again in Phase 9. Rows 11-17 identify their
own resolving phase. Row 18 was resolved by the Phase 9 documentation refresh.

| # | Original Phase 0 runtime break / resolution | Responsible production files | Regression evidence identified at Phase 0 |
|---:|---|---|---|
| 1 | `pip_value_in_account_currency()` accepts `current_quote_price`, but live graph sizing never supplies it. Missing rates silently use the quote-as-account approximation, wrong for USDJPY, crosses, and non-USD accounts. | `tradingagents/forex/pips.py`; `tradingagents/risk/sizing.py`; `tradingagents/graph/forex_graph.py`; `web/forex_routes.py`; conversion acquisition likely belongs beside `tradingagents/mt5/observer.py`. | `tests/test_forex_pips.py::test_usdjpy_usd_account_with_rate`, `tests/test_forex_position_sizing.py::test_usd_base_pair_margin`, `tests/test_validation_engine_integration.py::test_walkforward_propagates_usdjpy_and_executes_every_split`; **missing live call-path assertions**. |
| 2 | MT5 tick/symbol spread is readable, and the engine checks supplied spread, but `create_forex_risk_evaluator()` omits `current_spread_pips`. | `tradingagents/mt5/observer.py`; `tradingagents/mt5/models.py`; `web/forex_routes.py`; `tradingagents/graph/forex_graph.py`; `tradingagents/risk/engine.py`. | `tests/test_mt5.py::test_get_current_tick`, `tests/test_forex_risk_engine.py::test_spread_exceeding_max_pips_rejected`, route MT5 symbol/tick tests; **missing analysis-to-risk-node wiring test**. |
| 3 | ATR can be deterministically calculated from market bars and the engine accepts `atr_pips`, but the risk node omits it, so spread/ATR enforcement is skipped. Analyst prose/indicator output is not an acceptable risk input. | `tradingagents/forex/indicators.py`; `tradingagents/dataflows/forex_context.py`; `tradingagents/graph/forex_graph.py`; `tradingagents/risk/engine.py`. | `tests/test_forex_risk_engine.py::test_spread_to_atr_ratio_exceeding_limit_rejected`; technical indicator tests cover calculation; **missing runtime provenance/wiring test**. |
| 4 | `MT5Observer.to_open_positions()` exists, but the worker passes no positions to graph sizing. Correlation and cumulative-risk checks therefore see an empty portfolio. | `tradingagents/mt5/observer.py`; `tradingagents/mt5/models.py`; `web/forex_routes.py`; `tradingagents/graph/forex_graph.py`; `tradingagents/risk/sizing.py`. | `tests/test_mt5.py::test_sizing_engine_accepts_mt5_profile_and_positions`, sizing correlation/portfolio-ceiling tests; **missing live worker integration test**. |
| 5 | Broker symbol data is read correctly, but live graph creation omits `sizing_constraints`, causing generic defaults. `BrokerExecutionConstraints` also lacks digits/point/pip/broker-symbol fields. | `tradingagents/mt5/models.py`; `tradingagents/mt5/observer.py`; `tradingagents/risk/sizing.py`; `web/forex_routes.py`; `tradingagents/graph/forex_graph.py`. | `tests/test_mt5.py::test_to_broker_constraints`, `test_get_symbol_info_and_spread`, position-sizing volume tests; **missing worker-to-sizing constraint test**. |
| 6 | Currency exposure is calculated by a helper and the profile has a ceiling, but `compute_size()` never calls the helper or compares exposure with the ceiling. | `tradingagents/risk/sizing.py`. | `tests/test_forex_position_sizing.py::test_currency_exposure_aggregation` tests only the helper; **no enforcement regression exists**. |
| 7 | Neither `ForexRiskLimits` nor `ForexAccountProfile` carries daily realized P&L/loss limit; live worker does not query daily deals/journal P&L. | `tradingagents/risk/engine.py`; `tradingagents/risk/sizing.py`; `web/forex_routes.py`; likely `tradingagents/mt5/observer.py` and journal query layer. | No direct daily-loss protection test exists. Journal/MT5 deal tests are supporting coverage only. |
| 8 | `max_open_trades` is enforced only by `ForexBacktestEngine`. Live risk/sizing has no maximum-count policy and receives no positions. | `tradingagents/risk/engine.py`; `tradingagents/risk/sizing.py`; `tradingagents/graph/forex_graph.py`; `web/forex_routes.py`. | `tests/test_forex_backtest.py::test_margin_and_max_trades_rejection`; **no live regression exists**. |
| 9 | `MT5Position.to_open_position()` hardcodes `$10/pip/lot`; it does not use account currency, pair conversion, broker contract, or current quote. | `tradingagents/mt5/models.py`; `tradingagents/mt5/observer.py`; `tradingagents/forex/pips.py`. | `tests/test_mt5.py::test_to_open_positions_risk_conversion` only asserts positive risk and therefore preserves the approximation; add USDJPY/cross/account-currency cases. |
| 10 | When MT5 SL is zero, conversion substitutes a 50-pip risk estimate but returns `stop_loss=0`, which violates `OpenPosition(stop_loss > 0)`. Even if relaxed, a fabricated 50-pip stop would understate unbounded exposure. | `tradingagents/mt5/models.py`; `tradingagents/risk/sizing.py`; callers in `tradingagents/mt5/observer.py`. | Existing MT5 position fixtures have SLs; **missing no-SL fail-closed/unbounded-risk test**. |
| 11 | **Resolved in Phase 5.** Omitted or explicit `account_source="mt5"` now fails closed when MT5 is disconnected; no `$100,000` fallback remains. Manual mode requires its complete account inputs. | `tradingagents/research/contracts.py`; `web/forex_routes.py`. | Route regressions cover omitted/explicit MT5, invalid source, incomplete manual data, and known-zero free margin. |
| 12 | **Resolved for the standalone contract in Phase 5.** `POST /api/forex/proposals/size` is a labeled `MANUAL_ESTIMATE`, requires complete account inputs, and exposes whether complete broker constraints were caller supplied or standard estimate assumptions were used. It does not claim live portfolio or broker verification. | `web/forex_routes.py`; `tradingagents/risk/sizing.py`. | Route regressions cover assumptions, missing data, and zero/invalid free margin. |
| 13 | **Resolved in Phase 6.** The historical loop passes the cutoff account snapshot and typed simulated open positions into the shared risk context before every analysis. Position risk and count are derived only from trades already open at that cutoff. | `tradingagents/backtest/agent_backtester.py`; `tradingagents/backtest/historical_pipeline.py`; `tradingagents/backtest/forex_engine.py`. | Historical-integrity regression verifies evolving balance/equity/margin, position count, and bounded open risk. |
| 14 | **Resolved in Phase 6.** Historical conversion observations are timestamped directional inputs, filtered at each cutoff, and used for simulator pip value, P&L, margin, spread/slippage, commission, and swap accounting. Missing required conversion rejects actionable historical proposals explicitly. | `tradingagents/backtest/forex_engine.py`; `tradingagents/backtest/historical_pipeline.py`; `tradingagents/backtest/agent_backtester.py`; `tradingagents/forex/conversion.py`. | Regressions cover unavailable and future conversions, non-USD accounting, and live/historical sizing parity. |
| 15 | **Resolved in Phase 7.** WFE is unavailable for nonpositive development return, and heuristic robustness labels are withheld rather than using an epsilon denominator. | `tradingagents/backtest/walk_forward.py`. | Positive, zero, and negative development-return regressions verify WFE status and verdict behavior. |
| 16 | **Resolved in Phase 7.** Multi-split runs use declared expanding windows with disjoint OOS blocks covering the complete tail; no implicit holdout or unused final chunk remains. | `tradingagents/backtest/walk_forward.py`. | Split regressions verify complete candle accounting, disjoint OOS blocks, chronology, final-tail coverage, and existing OOS taint guards. |
| 17 | **Resolved in Phase 8.** The web run-state lock now guards the final cancellation check and short proposal persistence operation. Successful persistence marks the run `finalizing` before releasing the guard; cancellation that wins first prevents the write. | `tradingagents/graph/forex_graph.py`; `web/forex_routes.py`. | Deterministic synchronization tests cover cancellation at the persistence boundary, the persistence-wins order, idempotent cancel, cancelled SSE termination, and preservation of failed/completed terminal states. |
| 18 | **Resolved in Phase 9.** README, final report, changelog, and this context now identify current HEAD `4d215291`, the uncommitted remediation boundary, final local gate counts, pending current-worktree CI, and outstanding XM Demo acceptance. | `README.md`; `FINAL_REPORT.md`; `CHANGELOG.md`; this file. | Documentation was updated only after all repository-local gates passed. |

## 6. Minimum Convergence Architecture

Do not introduce a parallel risk engine. Phase 1 added the orchestration-owned,
validated `ForexRiskContext` immediately before the deterministic risk node. Later
phases should construct it through adapters:

- live MT5 adapter: account + tick + symbol spec + positions + daily realized P&L
  + conversion observations, all with `observed_at` and source;
- explicit manual adapter: user-supplied account/broker/portfolio data, with no
  invented values for risk-critical fields;
- historical adapter: completed-bar market/ATR + evolving simulated account and
  positions + point-in-time conversion observations.

The risk node should consume the same semantic fields in every mode and pass them
to the existing `ForexRiskEngine` and `ForexPositionSizingEngine`. Missing
risk-critical data should produce a deterministic `NO_TRADE`/unexecutable result,
not a generic default. A single object similar to the proposed `ForexRiskContext`
is reasonable if it stays internal and small; it is not necessary to change all
public APIs at once.

Required semantics:

- `as_of_utc` bounds every market/account/conversion observation.
- Conversion names direction explicitly (`quote_to_account`) rather than using an
  ambiguous `current_quote_price`.
- Broker precision/contract data remains broker-observed and pair-specific.
- Positions without a valid stop are unbounded/unknown exposure and fail closed.
- Daily loss, maximum position count, cumulative risk, correlation, and currency
  exposure are evaluated before approval.
- Historical conversion data must be available at the decision/fill timestamp;
  future rates cannot be used.

## 7. Shared Invariants

1. One phase solves one tightly related problem group.
2. Future phases read this file first and do not rescan the repository.
3. Inspect target files plus immediate dependencies only.
4. If this document is stale, update only the relevant section.
5. Avoid broad refactors and unrelated cleanup.
6. Preserve public contracts unless change is required and migration is explicit.
7. Extend deterministic components instead of duplicating calculations.
8. Never delete tests, increase skips to hide failures, or weaken CI.
9. Never fabricate market/account/broker/conversion/portfolio data in production.
10. Risk-critical missing data fails closed; no silent fallback.
11. MT5 remains read-only: no `order_send`, broker mutation, or automatic execution.
12. LLMs never calculate risk, pip values, lots, margin, exposure, P&L, or ATR used
    by the risk decision.
13. Historical paths remain point-in-time safe, including conversion and memory.
14. Proposal and risk evidence remains immutable; execution requires separate
    manual or broker evidence.
15. Every serious bug receives a runtime-path regression, not only a helper test.
16. Run targeted tests after each change and full non-E2E/E2E gates before closing.
17. Keep diffs small. If a fix needs more than roughly 5-7 production files, stop
    and explain the cross-cutting reason before proceeding.
18. Preserve explicit source/provenance and observation timestamps.
19. Preserve authentication, cooperative cancellation, runtime retention, and
    sanitized error boundaries.
20. Keep historical validation claims conservative and descriptive where statistical
    validation is absent.

## 8. Known-Working Areas Not to Casually Refactor

- Analyst selection and execution/context timeframe propagation.
- MT5 balance/equity/free-margin extraction when MT5 mode is explicit and connected.
- Historical evolving account snapshot propagation.
- Historical memory source and known-at filtering.
- Cooperative run cancellation and terminal browser cancellation state (except the
  narrowly identified persistence race).
- Completed-bar decisions, pending-order lookahead protection, and warm-up isolation.
- Backtest spread/slippage/commission/swap mechanics for direct-account-currency
  cases.
- Descriptive-only historical ablation labeling and identical-sample comparison.
- Dashboard pair routing, expensive-run confirmation, browser E2E, API authentication.
- Read-only MT5 safety; proposal immutability; journal/timeline; post-close MFE/MAE,
  reflection and lessons; settings and runtime retention.

## 9. Recommended Dependency Order

1. **Risk snapshot contract and fail-closed source selection** (issues 11 and the
   shared context boundary). Establish explicit live/manual/historical modes first.
2. **Live market and broker observations** (issues 2, 3, 5): tick spread, deterministic
   ATR, symbol constraints/precision. These observations are also prerequisites for
   trustworthy sizing.
3. **Conversion service and deterministic arithmetic** (issues 1, 9, 10): explicit
   quote-to-account path and safe MT5 position-risk conversion.
4. **Live portfolio controls** (issues 4, 6, 8): current positions, max count,
   cumulative/correlation/currency ceilings using corrected risk amounts.
5. **Daily-loss control** (issue 7): daily realized P&L snapshot and hard policy.
6. **Sizing endpoint parity** (issue 12): reuse the same mode/context assembly; do
   not maintain a second optimistic calculator.
7. **Historical context parity** (issues 13, 14): evolving positions and point-in-time
   conversion rates after the shared semantics are stable.
8. **Walk-forward semantics** (issues 15, 16): define WFE for nonpositive development
   return and declare/verify rolling coverage or final holdout.
9. **Cancellation persistence race** (issue 17): narrow transactional/idempotent fix.
10. **Release documentation** (issue 18): update README/FINAL_REPORT only after all
    relevant targeted/full gates pass.

## 10. Files by Phase

| Phase | Primary files | Immediate tests |
|---|---|---|
| 1 context/source | `risk/context.py`, `risk/sizing.py`, `graph/forex_graph.py` | `test_forex_risk_context.py`, `test_forex_position_sizing.py`, `test_forex_graph.py` |
| 2 market/broker | `mt5/models.py`, `mt5/observer.py`, `forex/indicators.py`, `graph/forex_graph.py` | `test_mt5.py`, `test_forex_risk_engine.py`, technical indicator tests |
| 3 conversion/position risk | `forex/pips.py`, `risk/sizing.py`, `mt5/models.py`, `mt5/observer.py` | `test_forex_pips.py`, `test_forex_position_sizing.py`, `test_mt5.py` |
| 4 portfolio limits | `risk/sizing.py`, `graph/forex_graph.py`, `web/forex_routes.py` | `test_forex_position_sizing.py`, `test_forex_routes.py`, `test_forex_graph.py` |
| 5 daily loss | `risk/engine.py` or `risk/sizing.py`, `web/forex_routes.py`, MT5/journal query adapter | new focused daily-loss tests plus route integration |
| 6 sizing API | `web/forex_routes.py` and shared context assembler | `test_forex_routes.py` |
| 7 historical parity | `backtest/forex_engine.py`, `backtest/historical_pipeline.py`, `backtest/agent_backtester.py`, `backtest/walk_forward.py` | `test_historical_integrity.py`, `test_forex_historical_agent_backtest.py`, `test_validation_engine_integration.py` |
| 8 walk-forward | `backtest/walk_forward.py` | `test_walk_forward.py`, `test_validation_engine_integration.py`, `test_walkforward_endpoint.py` |
| 9 cancellation race | `graph/forex_graph.py`, journal transaction helper, possibly `web/forex_routes.py` | `test_forex_graph.py`, cancellation route/browser tests, new race test |
| 10 documentation | `README.md`, `FINAL_REPORT.md` | full gates and documentation checks |

## 11. Do Not Re-Explore

Future phases may trust these conclusions at the recorded HEAD unless a target file
has changed:

- The live web route is the MT5 risk-context assembler and passes the complete
  immutable `ForexRiskContext` into the graph.
- The graph risk node receives spread, ATR, conversion, positions, broker
  constraints, position count, and UTC daily P&L when supplied by `ForexRiskContext`.
- MT5 read-only account, tick, candle ATR, symbol constraints, positions, and
  directional conversion observations are assembled for live graph risk evaluation.
- Currency stop-risk concentration, cumulative account risk, live maximum positions,
  and optional UTC daily-loss limits are enforced deterministically.
- Historical pipelines propagate cutoff account snapshots, simulated open
  positions, and only point-in-time FX conversion observations.
- WFE is unavailable for nonpositive development return. Expanding-window splits
  use disjoint OOS blocks that account for the complete remaining tail.
- Proposal persistence is deliberately separate from execution, and MT5 observation
  must remain read-only.

For a later phase, read this file, check `git diff` and the current versions of only
the listed target files and tests, then implement the phase gate. Re-open broad
architecture only if a concrete changed dependency invalidates one of these statements.
