# TradingAgents Forex remediation final verification report

- Verification date: 2026-10-07
- Final implementation base commit: `57e0eb4b282ee7f715a5fb18e7c46a95d80f7b4c`
- Working-tree state: target-state remediation is implemented and verified locally but intentionally uncommitted
- Remediation status: Local engineering gates complete; external XM lifecycle acceptance pending
- Release status: **READY FOR XM DEMO ACCEPTANCE; NOT XM DEMO ACCEPTED**

This status means the automated engineering gates pass. It is not a trading
performance recommendation, and it does not claim live XM acceptance.

## Current architecture

```text
Browser / CLI
      |
      v
Analysis Request
      |
      v
Verified Forex Snapshot
  +---+---+
  v   v   v
 Tech Macro News
  +---+---+
      v
Bull / Bear Debate
      v
Research Manager
      v
Forex Trader Proposal
      v
Deterministic Risk Policy
      v
Effective Allowed Risk
      v
Single Position-Sizing Engine
      v
Portfolio / Margin / Broker Validation
      v
LONG / SHORT / NO_TRADE
      |
      v
USER MANUAL DECISION
      |
      v
XM / MT5 manual execution
      |
      v
Read-Only MT5 Observer
      v
Reconciliation + SQLite Journal
      v
MFE/MAE + Execution Metrics
      v
Reflection + Structured Lessons
      v
Relevant Memory Retrieval ------> future analyses
```

There is no automated Forex broker-order submission path.

## Automated evidence

| Gate | Result |
| --- | --- |
| `ruff check .` | PASS — 0 errors across all repository files |
| `pytest -q -m "not e2e"` | PASS — 2,378 passed, 5 skipped, 9 deselected, 20 warnings, 88 subtests passed |
| `pytest -q tests/test_browser_e2e.py` | PASS — 9 passed, 2,383 deselected |
| Total Automated Tests | PASS — 2,387 passed |
| `python -m compileall tradingagents web cli tests` | PASS |
| JavaScript syntax checks (`node -c web/static/app.js`) | PASS |
| `git diff --check` | PASS — 0 whitespace or formatting errors |
| `pip check` | PASS — No broken dependencies |

The five local skips are
three POSIX-mode checks on Windows, optional Bedrock without `langchain_aws`, and
an optional live DeepSeek call without credentials. No mandatory browser test
was skipped.

## Feature matrix

`COMPLETE` means the target-state engineering requirements, runtime path, required
UI/API path, truthful data labeling, error handling, tests, and safety properties
were all found. It does not mean that a trading strategy is profitable or
statistically validated; the SRS requires that to remain a separate release state.
`PARTIAL` is reserved for missing target-state engineering behavior.

| Subsystem | Status | Evidence / boundary |
| --- | --- | --- |
| Forex domain | COMPLETE | Typed pairs, directions, proposals, risk decisions, and normalization are exercised by tests. |
| Market data | COMPLETE | MT5-first data, explicit Yahoo selection, provenance, quality, and cutoff checks exist. |
| Timeframes | COMPLETE | Execution/context timeframe contracts, aggregation, and report propagation are tested. |
| Technicals | COMPLETE | Technical analysis is on the graph, API, report, and browser workflow. |
| Macro | COMPLETE | Point-in-time macro path fails explicitly when historical evidence is unavailable. |
| News/calendar | COMPLETE | Trading Economics provenance, known-at/revision cutoffs, deduplication, and archive rules are tested. |
| Agents | COMPLETE | Technical, Macro, and News agents execute through the graph and report path. |
| Debate | COMPLETE | Bull/Bear evidence and rounds reach the Research Manager and decision report. |
| Trader | COMPLETE | The structured Forex proposal is produced without recording an execution. |
| Risk | COMPLETE | Typed deterministic approval/rejection/modification and `LONG`/`SHORT`/`NO_TRADE` are tested. |
| Position sizing | COMPLETE | Deterministic account/risk/broker-constraint sizing is API and regression tested. |
| Proposals | COMPLETE | Immutable proposal and risk evidence are persisted independently from broker execution. |
| Proposal lifecycle | COMPLETE | Explicit approve/skip/expire transitions reach API and UI without placing orders. |
| MT5 | COMPLETE | One read-only worker, process-wide serialized native reads, correct positional history queries, bounded lifecycle, reconnect protection, and live XM demo read-only observations are verified. |
| Matching | COMPLETE | Confidence-based proposal/execution matching and unplanned/manual classification persist evidence. |
| Journal | COMPLETE | SQLite is authoritative for trades, deals, costs, proposals, and lifecycle truth. |
| Timeline | COMPLETE | Ordered lifecycle and broker-observation events are persisted and displayed. |
| MFE/MAE | COMPLETE | Holding-window history and excursion calculations are tested with explicit unavailable states. |
| Post-close automation | COMPLETE | Confirmed close triggers retryable metrics, reflection, and learning processing. |
| Reflection | COMPLETE | Structured reflection persists, fails safely, and can be retried. |
| Learning | COMPLETE | Structured lessons persist and navigate to source trades. |
| Memory retrieval | COMPLETE | Relevant lesson filtering reaches future-analysis context; future historical lessons are excluded. |
| Confidence calibration | COMPLETE | Raw confidence is never presented as probability without sufficient empirical samples. |
| Performance | COMPLETE | Deterministic performance/execution metrics and sample warnings reach API and UI. |
| Skipped proposals | COMPLETE | Counterfactual skipped/expired proposal outcomes are evaluated and displayed truthfully. |
| Historical backtest | COMPLETE | BT-001–014 are implemented: causal PIT replay, next-eligible fills, evolving account/risk context, historical conversion, costs, provenance, ambiguity handling, and truthful `validated_strategy_performance=false`. |
| Walk-forward | COMPLETE | WF-001–006 and WF-008 are implemented: chronological expanding OOS windows, cutoff-safe warmup, complete tail coverage, unavailable nonpositive-return WFE, taint guards, and explicitly heuristic labels. |
| Ablation | COMPLETE | AN-008 and WF-007 are implemented: variants share identical verified inputs and boundaries, expose comparable deltas and sample warnings, and remain descriptive unless a separate statistically valid study is performed. |
| Dashboard | COMPLETE | Consolidated real-data overview and explicit unavailable states reach the browser. |
| Analysis UI | COMPLETE | Authenticated form, timeframe controls, SSE progression, failure handling, and validation are E2E tested. |
| Decision report | COMPLETE | Research, proposal, risk, provenance, and unavailable confidence states are E2E tested. |
| Proposals UI | COMPLETE | View and manual decision workflow is E2E tested without broker submission. |
| MT5 UI | COMPLETE | Offline and connected observer states, masked account data, positions, orders, and deals are implemented. |
| Journal UI | COMPLETE | Trade detail, timeline, costs, reflection, and navigation are implemented and tested. |
| Performance UI | COMPLETE | Performance, breakdown, execution, sample-size, and calibration states are tested. |
| Backtest UI | COMPLETE | Demo/historical labels, walk-forward details, provenance, and caveats are rendered. |
| Learning UI | COMPLETE | Lesson filters, evidence, rule text, and source-trade navigation are E2E tested. |
| Settings | COMPLETE | Safe allowlisted persistence/reset, secret status, and reload behavior are E2E tested. |
| Authentication | COMPLETE | Protected Forex router, in-memory key, HttpOnly cookie, strict origin/cookie policy, and sanitized errors are tested. |
| E2E | COMPLETE | Six mandatory real-Chromium workflows pass locally and the GitHub browser job passes. |
| Runtime retention | COMPLETE | Age/count bounds, event caps, locks, tombstones, SSE exit, and journal preservation are stress tested. |
| Documentation | COMPLETE | README, this report, phase evidence, and XM checklist reflect the verified Phase 6 working tree. |

## Security result

- No committed production credential was detected by the repository audit.
- `/api/config` and Forex settings use allowlists and do not return keys, session
  tokens, passwords, or broker credentials.
- Browser API keys remain in memory; legacy web-storage values are removed. Keys
  do not enter URLs.
- MT5 passwords are request-only, consumed once, cleared, excluded from response
  models, and masked in object representations.
- Session cookies are host-only, HttpOnly, `SameSite=Strict`, eight-hour signed
  cookies, and `Secure` when HTTPS is used.
- Authentication and provider/broker errors expose sanitized codes/messages rather
  than credentials, raw tracebacks, or rejected secret values.

## MT5 execution safety

Static search covered `order_send`, `order_check`, `order_calc_margin`,
`order_calc_profit`, `TRADE_ACTION_DEAL`, `TRADE_ACTION_PENDING`, position-close,
and SL/TP-modification patterns. Matches were tests, documentation, model/journal
constants, or local journal updates. No runtime call submits, modifies, or closes
a broker order. Broker SL/TP changes and deals are observed after the user acts.

## Historical integrity

Real historical mode disables fallback, rejects uploaded/unverified candles,
requires explicit date coverage and completed candles, applies a shared
point-in-time cutoff, excludes future lessons, constrains news/calendar evidence
to what was historically known, and includes spread, slippage, commission, swap,
margin, and execution assumptions. Completed-bar decisions execute no earlier
than their decision time, pending orders cannot use the decision candle, and
walk-forward partitions receive real pre-period warm-up bars without pre-period
trades. The evolving simulator account is supplied to historical sizing.
Walk-forward partitions preserve OOS boundaries and taint optimization that
accesses OOS data. Ablation variants share the input dataset and baseline
comparison; historical rankings remain descriptive only. All performance outputs keep
`validated_strategy_performance=false`.

## Live XM status

**CONNECTIVITY VERIFIED; MANUAL LIFECYCLE PENDING.** The XM Global demo terminal
initialized successfully with the broker-specific executable, reported a connected
demo account, exposed 1,639 symbols, returned a live EURUSD tick, and returned
positions, pending orders, 11 recent deals, and 8 historical orders. A fresh
dashboard instance completed 20 concurrent read-only account/position/order/deal
requests without a failure.

This does not establish XM demo acceptance. Analysis, proposal approval, manual
open/modify/partial-close/final-close actions, post-close metrics and learning,
and non-empty restart persistence remain unchecked in
[docs/XM_MT5_DEMO_ACCEPTANCE.md](docs/XM_MT5_DEMO_ACCEPTANCE.md).

## Known limitations

1. XM connectivity and read-only observation are verified, but broker-specific manual trade modification and partial-close behavior remain unverified.
2. Native MT5 IPC cannot be force-cancelled; a permanently stalled call may retain deferred resources until process exit.
3. Positions opened and closed entirely between observer polls may require manual reconciliation.
4. Historical results are descriptive, not statistically validated or predictive.
5. LLM pretrained knowledge cannot be proven point-in-time; unavailable historical evidence is withheld rather than fabricated.
6. Provider-backed analyses still require external credentials, availability, quota, and compatible model access.

## Release truth

All repository-local automated engineering gates and all seven GitHub CI jobs
pass for the Phase 6 implementation. The remaining environment gate is the
explicitly manual XM demo trade lifecycle.
The objective local classification is therefore:

**READY FOR XM DEMO ACCEPTANCE**

It must not be promoted to **XM DEMO ACCEPTED** until every required manual
checklist item has recorded evidence.
