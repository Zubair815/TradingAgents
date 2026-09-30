# TradingAgents final system acceptance report

- Audit date: 2026-09-30
- Commit at audit start: `18cdbea0806eb631911dd332a5f0211c45a65d92` (`Version1.8`)
- Working tree at audit start: clean
- Final release-evidence patch: uncommitted; it has no commit SHA until the maintainer commits it
- Release status: **READY FOR LOCAL DEMO VALIDATION**

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
Deterministic Risk Engine
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
| `ruff check .` | PASS |
| `pytest -q -m "not e2e"` | PASS — 2,109 passed, 5 skipped, 6 deselected, 20 warnings, 88 subtests |
| `pytest -q -m e2e` | PASS — 6 passed |
| `python -m compileall tradingagents web cli` | PASS |
| JavaScript syntax checks | PASS |
| `git diff --check` | PASS |
| Fresh local Python 3.12 install and package/CLI import | PASS |
| GitHub Python 3.10 | PASS |
| GitHub Python 3.11 | PASS |
| GitHub Python 3.12 | PASS |
| GitHub Python 3.13 | PASS |
| GitHub clean-install smoke | PASS |
| GitHub Chromium E2E | PASS |
| GitHub Ruff | PASS |

Latest GitHub evidence for the base commit: [CI run 36667732822](https://github.com/Zubair815/TradingAgents/actions/runs/36667732822),
completed successfully for `18cdbea0806eb631911dd332a5f0211c45a65d92` on 2026-09-30.
It does not cover the uncommitted final working-tree patch; final GitHub CI is
therefore pending commit and push. The five local skips are
three POSIX-mode checks on Windows, optional Bedrock without `langchain_aws`, and
an optional live DeepSeek call without credentials. No mandatory browser test
was skipped.

## Feature matrix

`COMPLETE` means the runtime path, required UI/API path, truthful data labeling,
error handling, tests, and safety properties were all found. `PARTIAL` identifies
an implemented path whose real-world or statistical acceptance remains limited.

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
| MT5 | COMPLETE | One read-only worker, bounded lifecycle, offline startup, reconnect protection, and status UI are tested. |
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
| Historical backtest | PARTIAL | PIT/source/cost integrity is enforced, but LLM historical knowledge and broker-fill fidelity prevent validation claims. |
| Walk-forward | PARTIAL | Sequential OOS boundaries and taint guards exist; output remains descriptive and not statistical validation. |
| Ablation | PARTIAL | Comparable variant machinery and reporting exist; results are explicitly non-validated and no live performance study was run. |
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
| E2E | COMPLETE | Six mandatory real-Chromium workflows pass locally; the five-test base gate passed in GitHub Actions. |
| Runtime retention | COMPLETE | Age/count bounds, event caps, locks, tombstones, SSE exit, and journal preservation are stress tested. |
| Documentation | COMPLETE | README, this evidence report, phase integrity docs, and XM checklist reflect the audited commit. |

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

**NOT EXECUTED.** No live XM terminal, demo credentials, broker session, or M1
holding-window evidence was supplied. Execute
[docs/XM_MT5_DEMO_ACCEPTANCE.md](docs/XM_MT5_DEMO_ACCEPTANCE.md) before promoting
the release from local-demo readiness to full live-environment acceptance.

## Known limitations

1. Live XM/MT5 behavior and broker-specific netting/partial-close behavior are unverified.
2. Native MT5 IPC cannot be force-cancelled; a permanently stalled call may retain deferred resources until process exit.
3. Positions opened and closed entirely between observer polls may require manual reconciliation.
4. Historical results are descriptive, not statistically validated or predictive.
5. LLM pretrained knowledge cannot be proven point-in-time; unavailable historical evidence is withheld rather than fabricated.
6. Provider-backed analyses still require external credentials, availability, quota, and compatible model access.

## Release truth

All repository-local automated engineering gates pass for the current working
tree. GitHub CI is green for the base commit and must be rerun after this final
patch is committed and pushed. The remaining environment gate is the explicitly
manual Windows + XM Demo acceptance run. Therefore the objective local release
classification is:

**READY FOR LOCAL DEMO VALIDATION**
