# TradingAgents Forex Master Remediation Context

> **Authoritative Baseline & Durable Context for Remediation Phases 2–11**
> **Cycle Start Date:** 2026-10-02
> **Source Specification:** `TradingAgents_Target_State_System_Requirements_Specification.pdf`
> **Starting Audit Hypothesis:** `audit_report.md` (2026-10-02)
> **Repository Root:** `d:\TradingAgents`

---

## 1. Baseline

- **Current Branch:** `main`
- **Current HEAD Commit:** `4c226137477c4d6cb3907c2397361d8ad45f7a26`
- **Requirements Specification Version:** Target-State System Requirements Specification (Release Candidate / 30 Sections / 200+ normative requirements)
- **Local Runtime Environment:** Windows 11 / Python 3.12 (`D:\TradingAgents\.venv\Scripts\python.exe`)
- **Pytest Baseline Status:** Local non-browser test suite passes 2,193+ tests; Chromium E2E passes 6 tests; targeted CLI Forex tests pass 14/14; risk & sizing tests pass 57/57.
- **Git Status Summary:** Clean Git tracking for `.env` (file is in `.gitignore:156` and has never been committed); working tree has previous verified documentation/operational additions.

---

## 2. Non-Negotiable Architecture & Critical Invariants

1. **Manual Execution Only:** The system is strictly a single-user decision-support platform. It MUST NOT submit, modify, cancel, or close broker orders under any circumstance. Production code must NEVER call `order_send` or equivalent broker-write primitives.
2. **Read-Only MT5 Integration:** MetaTrader 5 provides account, symbol, tick, candle, order, and deal observation only. All MT5 calls are serialized via process-wide reentrant locking (`_serialized_mt5`).
3. **Deterministic Risk Precedence:** The deterministic risk engine (`ForexRiskEngine`) and position sizing engine (`ForexPositionSizingEngine`) possess absolute veto authority. Natural-language LLM prose, agent ratings, or sentiment MUST NOT override deterministic rejections, sizing calculations, or safety clamps.
4. **Fail Closed:** When required market quotes, historical bars, conversion rates, MT5 connectivity, or risk metadata are missing, uncomputable, or stale, the system MUST fail closed (emitting `NO_TRADE` or raising `DataInsufficientError` / `FXConversionUnavailable`), never inventing defaults or optimistic fallbacks.
5. **Point-in-Time (PIT) Safety:** Historical backtests, walk-forward validation, and memory retrieval must never leak data past the decision bar's `as_of_utc` cutoff.
6. **Persistence Integrity:** SQLite is the authoritative persistent store for proposals, trades, deals, and lessons. Proposals and risk evidence are immutable once saved.
7. **Zero Secret Leakage:** Passwords, API keys, and session secrets must never appear in logs, API responses, CLI outputs, browser HTML/JS, or Git commits.

---

## 3. Important File Map

| Module / File | Responsibility | Key Classes & Functions | Key Dependencies | Requirements Affected |
|---|---|---|---|---|
| `cli/main.py` | Non-interactive CLI runner & commands | `run_command()`, `app` | `application.py`, `forex_graph.py`, `MT5Observer` | `SCOPE-005`, `ARCH-002`, `CLI-001`–`CLI-005` |
| `tradingagents/forex/application.py` | Framework-independent context builder | `build_mt5_application_context()`, `build_manual_application_context()` | `MT5Observer`, `ForexRiskContext`, `ForexAccountProfile` | `ARCH-002`, `API-005`, `API-007`, `CLI-002` |
| `tradingagents/graph/forex_graph.py` | Multi-agent research & risk graph | `ForexTradingAgentsGraph`, `create_forex_risk_evaluator()`, `create_forex_portfolio_manager()` | `ForexRiskEngine`, `ForexPositionSizingEngine`, `ForexRiskContext` | `ARCH-003`, `AGENT-001`–`AGENT-008`, `DOM-009` |
| `tradingagents/risk/context.py` | Immutable risk context models | `ForexRiskContext`, `ForexMarketContext`, `ForexPortfolioContext`, `ForexConversionRate` | Pydantic frozen models | `ARCH-001`, `DOM-008`, `PORT-011` |
| `tradingagents/risk/engine.py` | Deterministic trade validation | `ForexRiskEngine`, `ForexRiskLimits`, `ForexRiskDecision` | `pips.py`, `calendar.py`, `sessions.py` | `RISK-001`–`RISK-010`, `SIZE-008` |
| `tradingagents/risk/sizing.py` | Broker-aware position sizing | `ForexPositionSizingEngine`, `BrokerExecutionConstraints`, `OpenPosition` | `pips.py`, `conversion.py` | `SIZE-001`–`SIZE-007`, `PORT-004`–`PORT-006` |
| `tradingagents/forex/pips.py` | Pip arithmetic & conversion math | `pip_size_for()`, `pips_between()`, `pip_value_in_account_currency()` | `conversion.py`, `domain.py` | `FX-001`–`FX-006`, `SIZE-008` |
| `tradingagents/forex/conversion.py` | Directional FX conversion | `ForexConversionRate`, `resolve_conversion_rate()` | Pure math & timestamp bounds | `FX-001`–`FX-006`, `TIME-007`, `TIME-008` |
| `tradingagents/research/contracts.py` | Target-state domain contracts | `AnalysisRun`, `MarketSnapshot`, `AgentReport`, `BrokerEvent`, `AnalysisRequest` | Pydantic immutable records | `DOM-009`, `DOM-010`, `TIME-006`, `AGENT-007` |
| `tradingagents/database/models.py` | SQLite persistent records | `ProposalRecord`, `TradeJournalRecord`, `OrderExecutionRecord`, `StrategyVersionRecord` | Pydantic / SQLite schemas | `DOM-010`, `DOM-011`, `JOURNAL-001` |
| `tradingagents/database/journal.py` | SQLite journal repository | `ForexTradeJournal` | `sqlite3`, migrations, mutex lock | `JOURNAL-001`–`JOURNAL-007` |
| `tradingagents/journal/lifecycle.py` | Proposal & trade state machines | `TradeLifecycleManager`, `check_and_expire_proposals()` | `ForexTradeJournal`, timeline | `PROP-004`–`PROP-006`, `EXEC-007` |
| `tradingagents/learning/retriever.py` | Contextual lesson retrieval | `LessonRetriever`, `retrieve_lessons()` | `ForexLessonStore`, `ForexLesson` | `LEARN-006`–`LEARN-010` |
| `tradingagents/learning/models.py` | Learning & reflection schemas | `ForexLesson`, `TradeReflection`, `RetrievedLesson` | Pydantic models | `LEARN-004`–`LEARN-006` |
| `tradingagents/mt5/observer.py` | Read-only MT5 interface | `MT5Observer`, `MT5ObservationService` | `MetaTrader5` SDK (read-only) | `MT5-001`–`MT5-013`, `PORT-001`, `DATA-001` |
| `web/server.py` | FastAPI application entry point | `app`, lifespan, auth middleware, CORS | FastAPI, uvicorn, static files | `SEC-004`, `SEC-005`, `DEPLOY-002`, `API-001` |
| `web/forex_routes.py` | Forex REST and SSE router | `start_forex_analysis()`, `get_analysis_stream()`, proposal/journal endpoints | `application.py`, `forex_graph.py`, `journal.py` | `API-001`–`API-007`, `CFG-006`, `UI-001`–`UI-008` |
| `web/static/app.js` | Browser dashboard frontend | `handleForexSubmit()`, render functions | Vanilla JS | `UI-001`–`UI-008` |

---

## 4. Phase 1 Verification of All 30 Audit Findings

| Audit ID | Requirement | Initial Audit Status | Phase 1 Verified Classification | Evidence & Ground Truth |
|---|---|---|---|---|
| **SEC-002** | `.env` file exclusion | IMPLEMENTED BUT BROKEN | **FALSE_POSITIVE** (Git leak) / **VERIFICATION_ONLY** (Security) | `.env` exists locally for development; `.gitignore:156` ignores it; `git ls-files --error-unmatch .env` confirms untracked; `git log --all --full-history -- .env` shows it was NEVER committed. |
| **SEC-005** | CORS defaults | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `web/server.py:576-584` defines `DEFAULT_CORS_ORIGINS = ["http://localhost:8050", "http://127.0.0.1:8050"]`. Explicit localhost allowlist by default. |
| **SEC-009** | Private overlay / reverse proxy | PARTIALLY IMPLEMENTED | **DOCUMENTATION_ONLY** | Spec requirement is advisory ("SHOULD use private overlay such as Tailscale..."). Deployment guidance in docs. |
| **DEPLOY-002**| Dashboard host binding | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `web/server.py:1029` sets `host = os.environ.get("TRADINGAGENTS_DASHBOARD_HOST", "127.0.0.1")`. Defaults to `127.0.0.1`. |
| **DEPLOY-003**| Remote access private tunnel | PARTIALLY IMPLEMENTED | **DOCUMENTATION_ONLY** | Advisory recommendation (SHOULD, P1) for remote access deployment documentation. |
| **ARCH-002** | Shared application context builder | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `tradingagents/forex/application.py` defines `build_mt5_application_context`. Both `web/forex_routes.py` (L1695) and `cli/main.py` (L1158) invoke it. |
| **CLI-002**  | CLI MT5 mode shared builder | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `cli/main.py:1155-1165` connects MT5, calls `build_mt5_application_context`, fails closed on error, and passes `risk_context` into the graph. Tested in `test_cli_forex.py` (14/14 pass). |
| **DOM-009**  | Canonical run contracts | FULLY IMPLEMENTED | **COMPLETE (Phase 5)** | `AnalysisRun`, `MarketSnapshot`, `AgentReport`, `BrokerEvent` are systematically produced and linked during graph runs and retrievable via `get_last_run()`, `get_last_snapshot()`, and `get_last_agent_reports()`. |
| **DOM-010**  | `schema_version` & UTC timestamps | FULLY IMPLEMENTED | **COMPLETE (Phase 5)** | Added `schema_version: int = 1` to `TradeJournalRecord`, `OrderExecutionRecord`, and `StrategyVersionRecord`. Migration 5 adds `schema_version INTEGER NOT NULL DEFAULT 1` to `trades` table idempotently. |
| **DOM-011**  | Prompt/strategy versioning | FULLY IMPLEMENTED | **COMPLETE (Phase 5)** | System prompts deterministically hashed via SHA-256 (`prompt_hash`), attached to `ForexTraderProposal`, saved `ProposalRecord`, and `StrategyVersionRecord` in journal. |
| **TIME-006** | External object provenance tagging | FULLY IMPLEMENTED | **COMPLETE (Phase 5)** | Provenance fields (`source`, `retrieved_at_utc`, `observed_at_utc`/`timestamp`) enforced across `ForexBar`, `ForexNewsArticle`, `EconomicEvent`, and `BrokerEvent`. |
| **AGENT-007**| Agent report structure with metadata| FULLY IMPLEMENTED | **COMPLETE (Phase 5)** | Active analyst reports are enveloped into structured `AgentReport` records, persisted to `journal.research`, and stored in `state["forex_agent_reports"]` while preserving raw string state keys. |
| **DATA-008** | Cache point-in-time safety | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `tradingagents/dataflows/forex_data.py:501-510` strictly filters cached data with `filter_candles_by_cutoff(data, as_of, tf)`. Backtest mode bypasses disk cache completely. |
| **NEWS-004** | Calendar event vs publication time | PARTIALLY IMPLEMENTED | **ALREADY_FIXED** | `EconomicEvent` in `calendar.py:76-140` has `date`, `time_utc`, `published_at_utc`, `known_at_utc`, `revision_at_utc`, and `is_released_as_of()` strictly tests publication cutoff. |
| **NEWS-006** | Archive import for calendar/news | FULLY SATISFIED / DEFERRED | **DEFERRED_OPTIONAL** | Spec uses normative keyword "MAY" (P2). Programmatic import (`TradingEconomicsCalendar.import_snapshot()`) is fully implemented; dedicated standalone CLI command is deferred as unnecessary for personal-use scope. |
| **PROP-006** | Automatic deterministic proposal expiry | FULLY IMPLEMENTED | **COMPLETE (Phase 6)** | `check_and_expire_proposals()` is automatically invoked on `MT5ObservationService.poll_once()` cycles and on web `list_proposals` and `get_proposal` query boundaries, transitioning stale active proposals to `EXPIRED` deterministically. |
| **SIZE-008** | Broker digits price normalization | FULLY IMPLEMENTED | **COMPLETE (Phase 4)** | `tradingagents/risk/engine.py` dynamically resolves digits via `_resolve_digits` (from `BrokerExecutionConstraints.digits`, pair metadata, or JPY convention), eliminating hardcoded 5 decimals. |
| **PORT-005** | Unbounded risk position fail-closed | FULLY IMPLEMENTED | **COMPLETE (Phase 4)** | `tradingagents/risk/sizing.py` explicitly rejects proposals if any open position has `stop_loss is None or risk_amount is None`. Direct exposure calculation raises `ValueError`. |
| **PORT-006** | Currency concentration by stop-risk| FULLY IMPLEMENTED | **COMPLETE (Phase 4)** | Sizing engine strictly enforces currency exposure by net monetary stop-risk via `calculate_currency_risk_exposure()`, rejecting when exposure exceeds `max_currency_exposure_percent` of equity. |
| **PORT-011** | Pending orders risk evaluation | FULLY IMPLEMENTED | **COMPLETE (Phase 4)** | `PendingExposure` models pending orders. If any pending order lacks an objective stop or reserved risk, new proposals fail closed under conservative policy. Planned stop-risk is reserved against portfolio ceiling without ticket double-counting (PORT-010, PORT-012). |
| **JOURNAL-007**| Protected broker fields | FULLY IMPLEMENTED | **COMPLETE (Phase 6)** | `trades` broker execution fields and `proposals` / `proposal_evidence` rows are protected as strictly immutable via SQLite triggers and journal access patterns; user reflections and notes only update `trades.reflection`, `trades.tags_json`, and `trades.notes`. |
| **JOURNAL-008**| Journal export (CSV/JSON/MD) | FULLY IMPLEMENTED | **COMPLETE (Phase 6)** | Added `export_trades(fmt='json'|'csv')` on `ForexTradeJournal` and `ForexJournalManager` providing structured, deterministic export across all recorded trades with schema versions. |
| **LEARN-006**| Configurable minimum lesson support | FULLY IMPLEMENTED | **COMPLETE (Phase 7)** | `LessonRetriever.retrieve_lessons()` and `ForexLearningManager.retrieve_guidance_for_proposal()` expose configurable `min_support` threshold (defaulting to 1 for backward compatibility), strictly filtering out heuristics with insufficient empirical observations. |
| **LEARN-010**| Configurable lesson age decay | FULLY IMPLEMENTED | **COMPLETE (Phase 7)** | `calculate_age_decay()` evaluates continuous mathematical exponential decay ($2^{-\Delta t / T_{\text{half}}}$) parameterized by `half_life_days` (default 30.0), scaling recency bonus smoothly without discrete step boundaries and supporting point-in-time `as_of` cutoffs. |
| **AN-007**   | MFE/MAE parameter suggestions | FULLY IMPLEMENTED | **COMPLETE (Phase 7)** | `RiskStopCalibrator.calibrate()` produces `StopTargetCalibration` research suggestions with strict advisory separation (`is_advisory_only=True`, `applied_automatically=False`, explicit advisory disclaimer), preventing automated mutation of runtime risk parameters. |
| **WF-008**   | Hyperparameter optimization bounds | CANNOT VERIFY | **DEFERRED_OPTIONAL** | Spec uses normative keyword "MAY be added later" (P2). Deterministic walk-forward cross-validation evaluates fixed rules; automated Bayesian hyperparameter search is deferred as unnecessary for personal-use scope. |
| **UI-005**   | Truthful unavailable data rendering | FULLY IMPLEMENTED | **COMPLETE (Phase 8)** | `web/static/app.js` and API routes explicitly distinguish unavailable data from valid zeroes; empty metrics render "Unavailable", disconnected MT5 returns 503 rather than fake $0 balance. |
| **RUN-008**   | Checkpoint/resume for long runs | FULLY IMPLEMENTED | **COMPLETE (Phase 8)** | `ForexTradingAgentsGraph` implements `_resolve_graph_input()` querying checkpointer `get_state()`, passing `None` on interrupted runs to resume execution from the interrupted node without re-executing completed analysts. |
| **CFG-006**  | MT5 restart-required notification | FULLY IMPLEMENTED | **COMPLETE (Phase 8)** | `web/forex_routes.py` accurately evaluates active vs pending MT5 polling intervals (`_is_restart_required`), emitting truthful restart notifications while preserving zero-restart behavior for dynamic settings. |
| **REL-003**  | Truthful release documentation | PARTIALLY IMPLEMENTED | **DOCUMENTATION_ONLY** | `README.md` and `FINAL_REPORT.md` truthfully state "READY FOR XM DEMO ACCEPTANCE; NOT XM DEMO ACCEPTED", but need commit/test count alignment upon final completion. |

---

## 5. False Positives / Already Resolved Findings

These items were investigated and confirmed **resolved or false positives**. Later phases MUST NOT reopen or re-explore them:

1. **SEC-002 (`.env` git leak):** `.env` is properly ignored (`.gitignore:156`) and has NEVER been committed to git history. No repository leak exists.
2. **SEC-005 (CORS origins):** `DEFAULT_CORS_ORIGINS` in `web/server.py` already defaults strictly to localhost/127.0.0.1:8050.
3. **DEPLOY-002 (Bind address):** `web/server.py:1029` already defaults host to `127.0.0.1`.
4. **ARCH-002 & CLI-002 (Shared MT5 context in CLI):** `cli/main.py:1158` already calls `build_mt5_application_context`, fails closed, and forwards `risk_context`. Verified with 14 passing tests.
5. **DATA-008 (Cache PIT safety):** Point-in-time filtering is strictly applied after reading cached files in `tradingagents/dataflows/forex_data.py`.
6. **NEWS-004 (Calendar event vs published time):** `EconomicEvent` already models `published_at_utc` and `revision_at_utc` separately from scheduled time.
7. **PORT-005 (Unbounded risk rejection):** Sizing engine already verifies `position.risk_amount is not None` and rejects proposals if unbounded.
8. **PORT-006 (Currency stop-risk concentration):** Sizing engine already aggregates net stop-risk exposure per currency (not raw lot counts) via `calculate_currency_risk_exposure()`.
9. **JOURNAL-007 (Protected broker fields):** SQLite journal already restricts updates exclusively to reflection, notes, and tags.
10. **AN-007 (MFE/MAE calibration):** `RiskStopCalibrator` already exists and produces empirical stop/target calibrations.
11. **CFG-006 (Restart required notifications):** Both backend and UI already track and notify when MT5 polling interval changes require a restart.
12. **`aggressive_debater.py` vs `aggressive_debator.py`:** `aggressive_debater.py` is an intentional 5-line backward-compatibility alias re-exporting the `-or` module. Do not delete.
13. **Legacy `AnalysisRequest` in `web/server.py`:** Actively used by the equity stock analysis endpoint (`POST /api/analyze`). Do not remove.

---

## 6. Phase Dependency Map

```text
Phase 1: Verification & Master Remediation Plan (COMPLETE)
                     │
    ┌────────────────┴────────────────┐
    ▼                                 ▼
Phase 2: Security & Deployment    Phase 4: Sizing & Portfolio Risk
Verification (Evidence only)      (SIZE-008, PORT-011)
    │                                 │
    ▼                                 ▼
Phase 3: CLI & Shared Context     Phase 5: Domain Contracts & Provenance
(Regression tests only)           (DOM-009, DOM-010, DOM-011, TIME-006, AGENT-007)
                                      │
                                      ▼
                                  Phase 6: Proposal Lifecycle & Expiry
                                  (PROP-006)
                                      │
                                      ▼
                                  Phase 7: Learning & Retrieval Config
                                  (LEARN-006, LEARN-010)
                                      │
                                      ▼
                                  Phase 8: Runtime & UI Verification
                                  (UI-005)
                                      │
    ┌─────────────────────────────────┴───────────────────┐
    ▼                                                     ▼
Phase 9: Optional Capabilities (NEWS-006, JOURNAL-008)  Phase 10: Code Hygiene
    │                                                     │
    └─────────────────────────────────┬───────────────────┘
                                      ▼
Phase 11: Full Verification & Release Evidence (REL-003, Final Quality Gate)
```

---

## 7. Master Remediation Backlog

| Task ID | Requirements | Verified Classification | Severity | Exact Problem Description | Primary Files | Target Tests | Phase |
|---|---|---|---|---|---|---|---|
| **TASK-SEC-01** | `SEC-002`, `SEC-005`, `DEPLOY-002` | `VERIFICATION_ONLY` | LOW | Add explicit automated regression test proving `.env` remains untracked, CORS defaults to localhost, and server host defaults to 127.0.0.1. | `tests/test_forex_security.py`, `tests/test_web_server.py` | `test_env_untracked`, `test_cors_default_origins`, `test_dashboard_bind_address` | Phase 2 |
| **TASK-SEC-02** | `SEC-009`, `DEPLOY-003` | `DOCUMENTATION_ONLY` | LOW | Document deployment recommendation regarding private overlay (Tailscale) and reverse proxy with TLS in README. | `README.md`, `docs/FOREX_REMEDIATION_CONTEXT.md` | Doc verification | Phase 2 |
| **TASK-CLI-01** | `ARCH-002`, `CLI-002` | `VERIFICATION_ONLY` | LOW | Confirm existing 14 tests in `test_cli_forex.py` remain green and verify CLI MT5 integration coverage. | `tests/test_cli_forex.py` | `test_run_forex_mt5_uses_shared_authoritative_context` | Phase 3 |
| **TASK-RISK-01**| `SIZE-008` | `CONFIRMED_IMPLEMENTATION_GAP` | MEDIUM | `tradingagents/risk/engine.py` hardcodes `round(..., 5)` for suggested SL/TP price levels. Must use broker digits (e.g. 3 for JPY, broker symbol digits). | `tradingagents/risk/engine.py`, `tradingagents/forex/pips.py` | `tests/test_forex_risk_engine.py` | Phase 4 |
| **TASK-RISK-02**| `PORT-011` | `CONFIRMED_IMPLEMENTATION_GAP` | MEDIUM | Pending broker orders without stop-loss risk estimates are not checked to block additional exposure under conservative default policy. | `tradingagents/risk/sizing.py`, `tradingagents/risk/context.py` | `tests/test_forex_position_sizing.py` | Phase 4 |
| **TASK-DOM-01** | `DOM-009`, `AGENT-007` | `CONFIRMED_IMPLEMENTATION_GAP` | MEDIUM | Graph analyst outputs are raw strings in state. Envelope agent reports into `AgentReport` records with role, model, prompt hash, and cutoff; generate linked `MarketSnapshot`. | `tradingagents/graph/forex_graph.py`, `tradingagents/research/contracts.py` | `tests/test_forex_graph.py`, `tests/test_forex_routes.py` | Phase 5 |
| **TASK-DOM-02** | `DOM-010` | `CONFIRMED_IMPLEMENTATION_GAP` | LOW | Add `schema_version: int = 1` to `TradeJournalRecord` and ensure all persistent journal records carry schema versions. | `tradingagents/database/models.py`, `tradingagents/database/journal.py` | `tests/test_forex_journal.py` | Phase 5 |
| **TASK-DOM-03** | `DOM-011`, `TIME-006` | `CONFIRMED_IMPLEMENTATION_GAP` | LOW | Attach prompt version/hash systematically to proposal evidence and external data provenance. | `tradingagents/graph/forex_graph.py`, `tradingagents/database/journal.py` | `tests/test_forex_journal.py` | Phase 5 |
| **TASK-PROP-01**| `PROP-006` | `FULLY IMPLEMENTED` | MEDIUM | Automatic proposal expiry is implemented and triggered on MT5 observation polling loop and web proposal queries (`list_proposals`, `get_proposal`). | `tradingagents/mt5/service.py`, `web/forex_routes.py`, `tradingagents/journal/lifecycle.py` | `tests/test_forex_journal.py`, `tests/test_mt5_service.py` | Phase 6 |
| **TASK-LRN-01** | `LEARN-006` | `FULLY IMPLEMENTED` | LOW | Configurable `min_support` parameter added to `LessonRetriever` and `ForexLearningManager`, filtering out heuristics below empirical observation threshold. | `tradingagents/learning/retriever.py`, `tradingagents/learning/manager.py` | `tests/test_forex_learning.py` | Phase 7 |
| **TASK-LRN-02** | `LEARN-010` | `FULLY IMPLEMENTED` | LOW | Continuous exponential age decay function parameterized by `half_life_days` and supporting point-in-time `as_of` cutoffs added to `LessonRetriever`. | `tradingagents/learning/retriever.py`, `tradingagents/learning/manager.py` | `tests/test_forex_learning.py` | Phase 7 |
| **TASK-UI-01**  | `UI-005` | `VERIFICATION_ONLY` | LOW | Verify that edge-case unavailable metrics render with explicit reason badges in browser UI. | `web/static/app.js`, `tests/test_browser_e2e.py` | `test_browser_e2e.py` | Phase 8 |
| **TASK-OPT-01** | `NEWS-006`, `JOURNAL-008` | `COMPLETE / DEFERRED` | LOW | JOURNAL-008 export fully implemented in Phase 6; NEWS-006 programmatic import operational, standalone CLI classified as DEFERRED_OPTIONAL. | `tradingagents/database/journal.py`, `tradingagents/dataflows/trading_economics.py` | `tests/test_forex_journal.py` | Phase 9 |
| **TASK-CLN-01** | Code Hygiene | `FULLY VERIFIED` | LOW | Maintainability review of `web/forex_routes.py` (all endpoints and behaviors retained intact; debater shims and asset-class schemas preserved). | `web/forex_routes.py`, `tradingagents/agents/risk_mgmt/aggressive_debater.py` | `tests/test_repaired_regressions.py`, `tests/test_forex_routes.py` | Phase 10 |
| **TASK-REL-01** | `REL-003`, Quality Gate | `FULLY IMPLEMENTED` | MEDIUM | Final repository test gate execution; updated `README.md`, `FINAL_REPORT.md`, `CHANGELOG.md` with final commit `4c226137477c4d6cb3907c2397361d8ad45f7a26` and exact test counts (2,301 passed). | `README.md`, `FINAL_REPORT.md`, `CHANGELOG.md` | Full pytest suite + Ruff | Phase 11 |

---

## 8. Subsystem Test Map

| Subsystem | Relevant Test Files | Command to Run |
|---|---|---|
| **CLI & Application Context** | `tests/test_cli_forex.py`, `tests/test_forex_application.py` | `pytest tests/test_cli_forex.py tests/test_forex_application.py` |
| **Risk Engine & Sizing** | `tests/test_forex_risk_engine.py`, `tests/test_forex_position_sizing.py`, `tests/test_forex_pips.py`, `tests/test_forex_risk_context.py` | `pytest tests/test_forex_risk_engine.py tests/test_forex_position_sizing.py tests/test_forex_pips.py tests/test_forex_risk_context.py` |
| **MT5 Read-Only Integration**| `tests/test_mt5.py`, `tests/test_mt5_service.py`, `tests/test_mt5_runtime.py` | `pytest tests/test_mt5.py tests/test_mt5_service.py tests/test_mt5_runtime.py` |
| **Journal, Lifecycle & DB** | `tests/test_forex_journal.py`, `tests/test_database_backup_restore.py` | `pytest tests/test_forex_journal.py tests/test_database_backup_restore.py` |
| **Post-Trade Learning** | `tests/test_forex_learning.py` | `pytest tests/test_forex_learning.py` |
| **Historical & Backtesting** | `tests/test_forex_backtest.py`, `tests/test_historical_integrity.py`, `tests/test_walk_forward.py`, `tests/test_ablation_engine.py` | `pytest tests/test_forex_backtest.py tests/test_historical_integrity.py tests/test_walk_forward.py` |
| **Web Server & Security** | `tests/test_forex_security.py`, `tests/test_web_server.py`, `tests/test_forex_routes.py` | `pytest tests/test_forex_security.py tests/test_web_server.py` |
| **Browser E2E** | `tests/test_browser_e2e.py` | `pytest tests/test_browser_e2e.py` |

---

## 9. Architectural Decisions Log

1. **ADR-01: `.env` Security Classification:** Local `.env` is uncommitted, ignored by `.gitignore:156`, and has never existed in git history. No history rewrite or git filter-branch is necessary or permitted. Credential exposure audit confirmed only placeholder strings in `.env.example`.
2. **ADR-02: Price Normalization via Broker Digits (SIZE-008):** `ForexRiskEngine` will derive price rounding precision from `BrokerExecutionConstraints.digits` when provided, falling back to `3` for JPY pairs and `5` for standard pairs, replacing the hardcoded `round(price, 5)`.
3. **ADR-03: Automatic Proposal Expiry Trigger (PROP-006):** Proposal expiry will be checked automatically upon each `MT5ObservationService.poll()` cycle and on `list_proposals` query boundaries, transitioning stale proposals to `EXPIRED` without requiring manual trigger calls.
4. **ADR-04: Non-Breaking Run Artifact Envelope (DOM-009 / AGENT-007):** `ForexTradingAgentsGraph` will instantiate `AgentReport` for each analyst and synthesize run metadata into `MarketSnapshot` while preserving existing graph state dictionary keys (`state["technical_analyst_report"]`, etc.) for complete backward compatibility.
5. **ADR-05: Configurable Minimum Support & Decay in Learning (LEARN-006 / LEARN-010):** `LessonRetriever` will expose `min_support: int = 1` and `half_life_days: float = 30.0` parameters, defaulting to non-breaking values that preserve current behavior unless callers specify tighter thresholds.
6. **ADR-06: Preserving Shims & Stock Schemas:** The debater alias files (`aggressive_debater.py`) and stock `AnalysisRequest` in `server.py` are actively used for backward compatibility and equity routes; they will remain in place.
7. **ADR-07: Phase 2 Security & Deployment Baseline Verification:** `web/server.py` exports `get_cors_origins()` and `get_dashboard_bind_address()` for deterministic verification. Defaults strictly enforce `127.0.0.1:8050` and localhost CORS (`http://localhost:8050`, `http://127.0.0.1:8050`). `README.md` documents private overlay (Tailscale/WireGuard) and reverse proxy recommendations for remote access (`SEC-009`, `DEPLOY-003`).
8. **ADR-08: Phase 3 CLI MT5 / Shared Application Context Verification (ARCH-002 / CLI-002):** The non-interactive CLI Forex command (`tradingagents run <PAIR> --account-source mt5`) strictly reuses the framework-independent authoritative MT5 application context builder (`build_mt5_application_context`) in `tradingagents/forex/application.py`, identical to the web SSE analysis pipeline. In MT5 mode, authoritative MT5 account details, broker constraints, live tick market context, pending orders, open positions, and conversion context are retrieved and packaged into `ForexRiskContext` passed to `ForexTradingAgentsGraph`. On connection failure or missing market/portfolio data, the system fails closed with non-zero exit code (1), ensures clean MT5 observer disconnection, never executes the graph, and outputs explicit failure diagnostics. Manual mode continues using `build_manual_application_context` and strictly preserves zero free margin. MT5 integration remains read-only without broker order submission.
9. **ADR-09: Deterministic Portfolio, Risk & Sizing Authority Architecture (PORT-005, PORT-006, PORT-010, PORT-011, PORT-012, SIZE-008):** `ForexPositionSizingEngine` remains the single deterministic authority for lot volume and portfolio capacity checks. Unbounded risk in existing positions (`stop_loss is None or risk_amount is None`) or pending broker orders (`stop_loss is None or reserved_risk is None`) immediately fails closed (`is_executable=False`). Pending orders reserve their planned stop risk in cumulative portfolio risk ceilings (`PORT-010`) and net currency exposure calculations (`PORT-006`), de-duplicated against open positions by broker ticket ID (`PORT-012`). Currency concentration is calculated strictly on net monetary stop-risk exposure (assigning `+risk` to base and `-risk` to quote for longs, inverted for shorts) and clamped to `acc.max_currency_exposure_percent` of equity; raw lot-based exposure is never substituted for risk concentration checks. Broker price decimal formatting and rounding dynamically resolve broker digits (`BrokerExecutionConstraints.digits`), falling back to catalogue pair definitions or standard retail FX conventions (`SIZE-008`). All broker execution remains strictly read-only and manual-approval only.
10. **ADR-10: Domain Contracts, Provenance, and Point-in-Time Integrity Architecture (DOM-009, DOM-010, DOM-011, TIME-006, DATA-008, NEWS-004, AGENT-007):**
    - **Canonical Run Contracts & Artifact Envelope (`DOM-009`, `AGENT-007`):** Graph execution in `ForexTradingAgentsGraph.create_run_state()` instantiates canonical `AnalysisRun`, `AnalysisRequest`, `MarketSnapshot`, and `StrategyVersionRecord` entities linked to research storage (`journal.research`). In `forex_portfolio_manager_node()`, individual text reports from active analysts (technical, macro/calendar, sentiment/news) are wrapped into structured `AgentReport` contracts recording `role`, `analyst_id`, `model_name`, `provider`, `prompt_hash`, `as_of_utc`, `confidence`, and `sentiment`, persisted to `journal.research.save_report()`, and stored in `state["forex_agent_reports"]`. Backward compatibility is preserved by continuing to set raw string keys (`state["technical_analyst_report"]`, etc.) for downstream nodes and UI stream consumers. The graph exposes `get_last_run()`, `get_last_snapshot()`, and `get_last_agent_reports()` accessors.
    - **Uniform Schema Versioning & Migration 5 (`DOM-010`):** `TradeJournalRecord`, `OrderExecutionRecord`, and `StrategyVersionRecord` models define `schema_version: int = 1`. SQLite migration 5 (`version: 5, description: "Add schema_version to trades table (DOM-010)"`) safely inspects `PRAGMA table_info(trades)` and adds `schema_version INTEGER NOT NULL DEFAULT 1` without destructive table recreation, fully preserving historical trade records and database state.
    - **Prompt & Strategy Provenance Hashing (`DOM-011`):** `forex_trader_node` computes a deterministic SHA-256 digest of the actual system prompt template (`prompt_hash`) and attaches `prompt_hash`, `prompt_version="1.0.0"`, and `strategy_version="1.0.0"` to `ForexTraderProposal`. When saving proposal records, the journal links `run_id`, `snapshot_id`, and `version_id` alongside the prompt hash. Zero credentials or environment variables are included in prompts or hashes.
    - **External Data Provenance & Point-in-Time Guarantees (`TIME-006`, `DATA-008`, `NEWS-004`):** External data artifacts (`ForexBar`, `ForexNewsArticle`, `EconomicEvent`, `BrokerEvent`) require explicit `source`, `retrieved_at_utc`, and `observed_at_utc`/`timestamp` fields. Disk-cached bar data is strictly filtered by `filter_candles_by_cutoff` before returning to prevent lookahead bias. Economic calendar events enforce `published_at_utc <= cutoff` and `known_at_utc <= cutoff` via `is_released_as_of()`, and `clamp_to_as_of()` strictly masks future or unannounced actual/revised values.
11. **ADR-11: Proposal Lifecycle State Machine, Deterministic Expiry & Journal Immutability (PROP-006, JOURNAL-007, JOURNAL-008):**
    - **Deterministic Expiry Integration (`PROP-006`):** Proposal expiry checks (`check_and_expire_proposals()`) are evaluated automatically during `MT5ObservationService.poll_once()` cycles and on web query boundaries (`list_proposals`, `get_proposal`). Active proposals (`PROPOSED`, `APPROVED`, `MODIFIED`, `WAITING_USER`) whose UTC `expires_at` timestamp is at or before the evaluation cutoff deterministically transition to `LifecycleState.EXPIRED` and log an immutable `EventType.PROPOSAL_EXPIRED` timeline event.
    - **Safe Idempotency & Terminal State Preservation (`PROP-006`):** Calling `expire_proposal()` on an already expired proposal is safely idempotent (immediate no-op, preventing duplicate timeline events or race conditions). Attempting to expire a terminal or executing proposal (`EXECUTED`, `REJECTED`, `SUPERSEDED`) raises `LifecycleTransitionError`, preserving strict state machine transitions.
    - **Trigger-Protected Journal Immutability & Safe Reflection (`JOURNAL-007`):** Database triggers `proposal_fields_immutable` and `proposal_evidence_immutable_update` strictly forbid mutating original research and proposal content in `proposals` and `proposal_evidence`. Mutable user annotations on recorded trades are strictly isolated to `trades.reflection`, `trades.tags_json`, and `trades.notes` via `update_trade_reflection()`, preserving immutable broker execution facts (`open_price`, `close_price`, `lots`, `pips_gained`, `r_multiple`, `net_profit`, `exit_reason`).
    - **Structured Trade Journal Export (`JOURNAL-008`):** `ForexTradeJournal.export_trades(fmt="json"|"csv", pair=None, status=None, limit=1000)` and `ForexJournalManager.export_trades(...)` produce deterministic, structured CSV or JSON exports including schema version, pair, action, status, prices, lot volumes, performance metrics, and exit reasons.
12. **ADR-12: Configurable Learning Evidence Thresholds, Continuous Age Decay & Analytics Advisory Boundary (LEARN-006, LEARN-010, AN-007):**
    - **Configurable Minimum Support (`LEARN-006`):** `LessonRetriever.retrieve_lessons()` and `ForexLearningManager.retrieve_guidance_for_proposal()` expose `min_support: int | None = None` (defaulting to `default_min_support = 1`), strictly excluding heuristics whose empirical validation count is below the configured threshold (`evidence_count < min_support`).
    - **Continuous Mathematical Age Decay (`LEARN-010`):** Replaced hardcoded discrete intervals (`<= 7d`, `<= 30d`) with continuous exponential decay: $\text{decay} = 2^{-\Delta t / T_{\text{half}}}$ via `calculate_age_decay()`. Parameterized by `half_life_days` (default 30.0 days), it smoothly decays recency weighting over time and supports point-in-time reference cutoffs (`as_of`) for backtest integrity.
    - **Point-in-Time Retrieval Cutoff:** When `as_of` cutoff is provided, lessons created after the cutoff are strictly excluded from retrieval, and elapsed age for eligible lessons is evaluated relative to `as_of` rather than live execution time.
    - **Fundamental Learning & Analytics Advisory Boundary (`AN-007`):** Lessons are advisory evidence for LLM prompt augmentation and cannot override deterministic risk limits, sizing rules, or broker constraints. `format_lessons_for_prompt()` prepends an explicit advisory disclaimer. `StopTargetCalibration` models and `RiskStopCalibrator` outputs enforce `is_advisory_only=True`, `applied_automatically=False`, and include explicit advisory notices to guarantee parameter suggestions remain descriptive empirical research rather than runtime configuration.
13. **ADR-13: Runtime Checkpoint/Resume Integrity, Truthful Configuration Restart Signals, and Truthful UI Representation (RUN-008, CFG-006, UI-005):**
    - **Checkpoint/Resume Integrity (`RUN-008`):** `ForexTradingAgentsGraph` implements `_resolve_graph_input()` in both synchronous execution (`propagate()`) and streaming (`stream()`). When execution is configured with a checkpointer and `thread_id`, the graph inspects `self.graph.get_state(cfg)`. If an interrupted execution exists (`state and state.next`), it passes `None` as the graph input, enabling LangGraph to resume cleanly from the interrupted node without duplicating initial state or re-executing completed analyst nodes. Completed threads (`not state.next`) or fresh runs cleanly initiate fresh executions.
    - **Deterministic Configuration Restart Signals (`CFG-006`):** `_is_restart_required()` evaluates whether the running MT5 background observation service requires a service restart by comparing active polling interval (`_forex_runtime.service.poll_interval`) with persisted configuration (`mt5_poll_interval_seconds`). Dynamic settings (`forex_default_pair`, `forex_default_risk_percent`, `forex_min_rr`, `forex_market_source`, `forex_max_spread_pips`, `forex_news_blackout_minutes`, `llm_provider`, `quick_think_llm`, `deep_think_llm`) dynamically apply without falsely reporting `restart_required = True`. `GET /api/forex/settings`, `PATCH /api/forex/settings`, and `POST /api/forex/settings/reset` strictly and truthfully communicate restart necessity.
    - **Truthful UI State Representation (`UI-005`):** Unavailable and error states (e.g. MT5 disconnects, missing account metrics, empty calibration data, unprovided stop loss levels) are represented truthfully via explicit badges, 503 error boundaries, and "Unavailable" labels, never coerced into fake `$0.00`, `0.000`, or empty strings that could deceive an operator.
14. **ADR-14: Review and Classification of Optional / Low-Priority Capabilities (NEWS-006, WF-008, JOURNAL-008, SEC-009, DEPLOY-003):**
    - **Classification of Remaining Normative MAY/SHOULD Requirements:**
      1. `JOURNAL-008` (Trade journal export): **Valuable optional enhancement / Fully Implemented (Phase 6)**. `ForexTradeJournal.export_trades(fmt="json"|"csv")` and `ForexJournalManager.export_trades()` fulfill target-state export requirements with full schema versioning.
      2. `SEC-009` (Private overlay network) & `DEPLOY-003` (Remote access private tunnel): **Valuable optional enhancements / Fully Satisfied (Phase 2)**. Documented in `README.md`; system enforces strict local-only `127.0.0.1:8050` binding and localhost CORS by default.
      3. `NEWS-006` (Historical calendar/news archive import CLI): **Unnecessary for current personal-use scope / DEFERRED_OPTIONAL**. Core programmatic snapshot import (`TradingEconomicsCalendar.import_snapshot()`) is fully implemented, verified, and PIT-safe. Dedicated standalone CLI command is intentionally deferred because snapshot JSON files placed in the configured archive directory are automatically loaded and verified by `read_snapshots()`. Core functionality remains 100% compliant, PIT-safe, and fails closed (`DataInsufficientError`) on missing coverage without destabilizing historical backtests.
      4. `WF-008` (Automated Bayesian hyperparameter search): **Unnecessary for current personal-use scope / DEFERRED_OPTIONAL**. `WalkForwardOptimizer` already evaluates out-of-sample parameter stability and robustness across rolling and anchored splits; automated parameter search is deferred to avoid data snooping and combinatorial overfitting.
15. **ADR-15: Controlled Code Hygiene, Backward Compatibility Shim Preservation, and Single-Module Router Invariant:**
    - **Debater Aliases Preservation:** Both `tradingagents/agents/risk_mgmt/aggressive_debater.py` and `aggressive_debator.py` are preserved intact. `aggressive_debater.py` is the canonical module that re-exports `create_aggressive_debater` and deprecated alias `create_aggressive_debator`. `aggressive_debator.py` provides the implementation and issues a `DeprecationWarning` on alias usage. This contract is explicitly tested and verified in `tests/test_repaired_regressions.py:158`. Removing or moving either file would cause breaking import regressions.
    - **Asset-Class Request Model Separation:** `AnalysisRequest` in `web/server.py` defines the Pydantic schema for equities stock analysis (`POST /api/analyze`), verified in `tests/test_web_server.py`. `AnalysisRequest` in `tradingagents/research/contracts.py` defines the canonical Pydantic research contract for Forex runs (`POST /api/forex/analyze`), re-exported in `web/forex_routes.py` as `ForexAnalysisRequest` and verified in `tests/test_forex_phase1.py` and `tests/test_run_store_hardening.py`. Neither model is a legacy remnant; both are active, strictly typed, and required.
    - **Single-Module Router Invariant:** `web/forex_routes.py` is maintained as a cohesive single module. Splitting it into fragmented sub-routers carries severe regression risk (circular imports with runtime managers, lock contamination, and worker threads) with zero architectural benefit. The file is verified 100% clean by Ruff and passes all 111 route tests without regression.

---

## 10. Phase 2 Execution Record (2026-10-02)

- **Completed Requirements:** `SEC-002`, `SEC-005`, `SEC-009`, `DEPLOY-002`, `DEPLOY-003`
- **Files Modified:**
  - `web/server.py`: Added `get_cors_origins()` and `get_dashboard_bind_address()` testable functions.
  - `tests/test_forex_security.py`: Added regression tests for `.env` exclusion, restrictive CORS defaults, and localhost binding.
  - `README.md`: Documented private overlay (Tailscale) and reverse proxy deployment security recommendations.
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded Phase 2 completion and decisions.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_security.py`: 74 passed (including 3 new security tests).
  - `pytest tests/test_web_server.py`: 20 passed.
  - `ruff check web/server.py tests/test_forex_security.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 9 confirmed implementation gaps (`SIZE-008`, `PORT-011`, `DOM-009`, `DOM-010`, `DOM-011`, `TIME-006`, `AGENT-007`, `PROP-006`, `LEARN-006` / `LEARN-010`) scheduled for Phases 4–7.

---

## 11. Phase 3 Execution Record (2026-10-02)

- **Completed Requirements:** `ARCH-002`, `CLI-002`
- **Files Modified:**
  - `tests/test_cli_forex.py`: Added comprehensive regression tests verifying end-to-end MT5 context wiring, authoritative account and portfolio context delivery to `ForexTradingAgentsGraph`, connection failure handling, missing tick fail-closed behavior, and disconnected MT5 status fail-closed behavior.
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded Phase 3 completion, ADR-08, and updated master status.
- **Verification Gates Passed:**
  - `pytest tests/test_cli_forex.py`: 18 passed (including 4 new end-to-end regression tests).
  - `pytest tests/test_forex_application.py tests/test_mt5.py tests/test_mt5_service.py tests/test_forex_risk_context.py`: 58 passed.
  - `pytest tests/test_web_server.py`: 20 passed.
  - `ruff check tests/test_cli_forex.py cli/main.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 9 confirmed implementation gaps (`SIZE-008`, `PORT-011`, `DOM-009`, `DOM-010`, `DOM-011`, `TIME-006`, `AGENT-007`, `PROP-006`, `LEARN-006` / `LEARN-010`) scheduled for Phases 4–7.

---

## 12. Phase 4 Execution Record (2026-10-03)

- **Completed Requirements:** `PORT-005`, `PORT-006`, `PORT-010`, `PORT-011`, `PORT-012`, `SIZE-008`
- **Files Modified:**
  - `tradingagents/risk/sizing.py`: Added `PendingExposure` model, integrated pending orders and open positions into `calculate_currency_risk_exposure()` with ticket de-duplication, enforced fail-closed rejection for unbounded open positions (`PORT-005`) and unbounded pending orders (`PORT-011`), reserved pending stop-risk in cumulative portfolio risk ceilings (`PORT-010`), and verified volume stepping flooring and minimum volume checks.
  - `tradingagents/forex/__init__.py`: Exported `PendingExposure` and `calculate_currency_risk_exposure`.
  - `tradingagents/risk/context.py`: Added `pending_exposures: tuple[PendingExposure, ...] = ()` to `ForexPortfolioContext`.
  - `tradingagents/mt5/models.py`: Added `MT5Order.to_pending_exposure()` conversion method.
  - `tradingagents/mt5/observer.py`: Added `MT5Observer.to_pending_exposures()` to transform MT5 pending orders to `PendingExposure` models.
  - `tradingagents/forex/application.py`: Injected pending order exposures into `ForexPortfolioContext` in `build_mt5_application_context()`.
  - `tradingagents/risk/engine.py`: Added `_resolve_digits()` dynamically deriving digits from `BrokerExecutionConstraints.digits`, `ForexPair.digits`, or pair convention (3 for JPY, 5 for standard), removing hardcoded `round(..., 5)` in trade geometry checks, R:R adjustments, and limits clamping (`SIZE-008`).
  - `tradingagents/graph/forex_graph.py`: Forwarded `broker_digits` to `engine.validate_proposal()` and `pending_exposures` to `s_engine.size_proposal()`.
  - `tests/test_forex_risk_engine.py`: Added `TestForexRiskEngineBrokerDigits` verifying dynamic 3-digit JPY formatting, explicit `broker_digits` overrides, and limits clamping precision.
  - `tests/test_forex_position_sizing.py`: Added boundary tests covering unbounded stop-loss fail-closed behavior (`PORT-005`), unbounded pending order fail-closed behavior (`PORT-011`), net stop-risk currency concentration attribution across multiple positions (`PORT-006`), pending order risk reservation (`PORT-010`), ticket de-duplication (`PORT-012`), and volume-step flooring.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_risk_engine.py tests/test_forex_position_sizing.py tests/test_forex_risk_context.py tests/test_portfolio_context.py tests/test_cli_forex.py tests/test_forex_application.py tests/test_mt5.py`: 160 passed.
  - `pytest tests/test_forex_graph.py`: 26 passed.
  - `ruff check tradingagents/risk/sizing.py tradingagents/risk/engine.py tradingagents/graph/forex_graph.py tests/test_forex_risk_engine.py tests/test_forex_position_sizing.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 7 confirmed implementation gaps (`DOM-009`, `DOM-010`, `DOM-011`, `TIME-006`, `AGENT-007`, `PROP-006`, `LEARN-006` / `LEARN-010`) scheduled for Phases 5–7.

---

## 13. Phase 5 Execution Record (2026-10-03)

- **Completed Requirements:** `DOM-009`, `DOM-010`, `DOM-011`, `TIME-006`, `DATA-008`, `NEWS-004`, `AGENT-007`
- **Files Modified:**
  - `tradingagents/database/models.py`: Added `schema_version: int = 1` to `TradeJournalRecord`, `OrderExecutionRecord`, and `StrategyVersionRecord`.
  - `tradingagents/database/migrations.py`: Added Migration 5 with idempotent PRAGMA inspection adding `schema_version INTEGER NOT NULL DEFAULT 1` to `trades` table.
  - `tradingagents/database/journal.py`: Updated `record_trade_open()` and `_row_to_trade_record()` to persist and retrieve `schema_version`.
  - `tradingagents/agents/trader/forex_trader.py`: Computed SHA-256 `prompt_hash` over system prompt and attached `prompt_hash`, `prompt_version`, and `strategy_version` to `ForexTraderProposal`.
  - `tradingagents/agents/utils/agent_states.py`: Added `forex_run_id`, `forex_snapshot_id`, `forex_version_id`, `forex_prompt_hash`, and `forex_agent_reports` TypedDict keys to `AgentState`.
  - `tradingagents/graph/forex_graph.py`: Implemented canonical run contracts lifecycle (`AnalysisRun`, `MarketSnapshot`, `AgentReport`, `StrategyVersionRecord`), persisted via `journal.research`, enveloped analyst reports in `forex_portfolio_manager_node`, linked run identities to proposals, and added graph inspection accessors (`get_last_run()`, `get_last_snapshot()`, `get_last_agent_reports()`).
  - `tests/test_forex_journal.py`: Added `TestForexJournalSchemaAndProvenance` verifying `schema_version` column existence, migration 5 historical preservation, and strategy version round trips.
  - `tests/test_forex_graph.py`: Added `TestForexGraphDomainProvenance` verifying canonical run identities, snapshot persistence, E2E run completion, `AgentReport` linkage, prompt hash attachment, and proposal evidence links.
  - `tests/test_historical_integrity.py`: Added `TestPointInTimeIntegrityAndProvenance` verifying `ForexBar`, `ForexNewsArticle`, and `BrokerEvent` provenance, cache PIT filtering via `filter_candles_by_cutoff`, and `EconomicEvent` release/revision PIT clamping.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_journal.py tests/test_forex_graph.py tests/test_historical_integrity.py tests/test_database_backup_restore.py tests/test_forex_phase1.py tests/test_forex_database.py tests/test_forex_data.py tests/test_forex_routes.py tests/test_cli_forex.py tests/test_forex_application.py tests/test_forex_risk_engine.py tests/test_forex_position_sizing.py`: 387 passed.
  - `ruff check tradingagents/database/models.py tradingagents/database/migrations.py tradingagents/database/journal.py tradingagents/agents/trader/forex_trader.py tradingagents/graph/forex_graph.py tests/test_forex_journal.py tests/test_forex_graph.py tests/test_historical_integrity.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 3 confirmed implementation gaps (`PROP-006`, `LEARN-006`, `LEARN-010`) scheduled for Phases 6–7.

---

## 14. Phase 6 Execution Record (2026-10-03)

- **Completed Requirements:** `PROP-006`, `JOURNAL-007`, `JOURNAL-008`
- **Files Modified:**
  - `tradingagents/journal/lifecycle.py`: Made `expire_proposal(proposal_id, reason, actor)` safely idempotent when called on an already expired proposal while raising `LifecycleTransitionError` for non-expirable states (`EXECUTED`, `REJECTED`, `SUPERSEDED`); normalized ISO timestamps with trailing `Z` in `check_and_expire_proposals()` and restricted active query to valid `ProposalStatus` enum members.
  - `tradingagents/database/journal.py`: Added `export_trades(fmt="json"|"csv", pair=None, status=None, limit=1000)` to `ForexTradeJournal` with structured CSV/JSON formatting including `schema_version`.
  - `tradingagents/journal/manager.py`: Exported `export_trades` on `ForexJournalManager` delegating to `ForexTradeJournal`.
  - `tradingagents/mt5/service.py`: Integrated `self.journal_mgr.check_and_expire_proposals()` into `poll_once()`, emitting `PROPOSAL_EXPIRED` observation events into the polling stream.
  - `web/forex_routes.py`: Added automatic `check_and_expire_proposals()` evaluation to `list_proposals` and `get_proposal` query endpoints.
  - `tests/test_forex_journal.py`: Added `TestProposalLifecycleAndJournalCompletion` suite with 8 focused tests:
    - Expiry boundary condition (strict `<= as_of` cutoff)
    - Already executed proposal cannot expire (`LifecycleTransitionError`)
    - Rejected proposal cannot expire (`LifecycleTransitionError`)
    - Superseded proposal cannot expire (`LifecycleTransitionError`)
    - Repeated expiry processing idempotency (no duplicate events or state flips)
    - Persistence and restart integrity across database reconnections
    - User-note protection preserving immutable execution evidence and triggering SQLite constraint errors on raw update attempts
    - Export correctness for JSON and CSV formats
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded ADR-11, Phase 6 execution record, and updated current progress.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_journal.py`: 39 passed (including 8 new lifecycle & journal completion tests).
  - `pytest tests/test_mt5_service.py`: 7 passed.
  - `pytest tests/test_forex_routes.py`: 111 passed.
  - Full subsystem regression suite (367 tests across journal, MT5, graph, historical integrity, DB backup, phase 1, database, routes, CLI, application context, risk engine, position sizing): 367 passed.
  - `ruff check tradingagents/journal/lifecycle.py tradingagents/database/journal.py tradingagents/journal/manager.py tradingagents/mt5/service.py web/forex_routes.py tests/test_forex_journal.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps. Remaining items: `UI-005` (verification only, Phase 8), `NEWS-006` (optional requirement, Phase 9), code hygiene (Phase 10), and release evidence (Phase 11).

---

## 15. Phase 7 Execution Record (2026-10-03)

- **Completed Requirements:** `LEARN-006`, `LEARN-010`, `AN-007`
- **Files Modified:**
  - `tradingagents/learning/retriever.py`: Added `calculate_age_decay()` implementing continuous mathematical exponential decay ($2^{-\Delta t / T_{\text{half}}}$) parameterized by `half_life_days` (default 30.0), point-in-time `as_of` cutoff filtering, and configurable `min_support` threshold in `retrieve_lessons()` and `LessonRetriever.__init__()`. Added institutional advisory disclaimer to `format_lessons_for_prompt()`.
  - `tradingagents/learning/manager.py`: Forwarded `min_support`, `half_life_days`, and `as_of` from `ForexLearningManager.retrieve_guidance_for_proposal()` to `LessonRetriever.retrieve_lessons()`.
  - `tradingagents/analytics/models.py`: Added `is_advisory_only: bool = True`, `applied_automatically: bool = False`, and descriptive `evidence_note` to `StopTargetCalibration`.
  - `tradingagents/analytics/calibration.py`: Added explicit advisory notice recommendation to `RiskStopCalibrator.calibrate()` and `_build_empty_calibration()`, ensuring calibration recommendations remain descriptive empirical research and cannot mutate runtime risk parameters or broker constraints.
  - `web/forex_routes.py`: Added `min_support`, `half_life_days`, and `as_of` query parameters to `/api/forex/learning/retrieve` endpoint.
  - `tradingagents/graph/forex_graph.py`: Forwarded `as_of=cutoff` to `retrieve_lessons()` in `forex_trader_node` while preserving defense-in-depth cutoff post-filtering.
  - `tests/test_forex_learning.py`: Added `TestLearningAndAnalyticsCompletion` suite with 7 comprehensive tests:
    - `test_min_support_threshold_boundary`: Threshold boundary filtering across evidence counts (1, 2, 4).
    - `test_insufficient_evidence_excluded`: Strict exclusion of anecdotal/low-support lessons despite perfect setup alignment.
    - `test_configurable_age_decay_half_life`: Mathematical decay verification across 0d (1.0), 30d (0.5), 60d (0.25), and sensitivity to fast/slow half-lives (7d vs 365d).
    - `test_point_in_time_retrieval_cutoff`: Strict exclusion of future lessons and elapsed age evaluation relative to cutoff.
    - `test_learning_advisory_boundary_prompt_framing`: Explicit advisory disclaimer framing in formatted prompt text.
    - `test_analytics_stop_target_calibration_advisory_separation`: Advisory flags and descriptive research separation in `RiskStopCalibrator`.
    - `test_learning_retrieval_route_with_support_and_decay`: API endpoint query parameter integration.
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded ADR-12, Phase 7 execution record, and updated current progress.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_learning.py`: 34 passed (including 7 new Phase 7 tests).
  - `pytest tests/test_forex_analytics.py`: 13 passed.
  - `pytest tests/test_forex_routes.py`: 111 passed.
  - `pytest tests/test_historical_integrity.py`: 41 passed.
  - `pytest tests/test_forex_closed_loop.py`: 4 passed.
  - `ruff check`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps. Remaining backlog items: `NEWS-006` (optional, Phase 9), code hygiene (Phase 10), release evidence (Phase 11).

---

## 16. Phase 8 Execution Record (2026-10-03)

- **Completed Requirements:** `RUN-008`, `CFG-006`, `UI-005`
- **Files Modified:**
  - `tradingagents/graph/forex_graph.py`: Implemented `_resolve_graph_input()` in `ForexTradingAgentsGraph` for both `propagate()` and `stream()`, resolving `None` input when resuming an interrupted LangGraph checkpoint thread (`state and state.next`) so nodes resume without repeating prior work or duplicating initial state.
  - `web/forex_routes.py`: Added `_is_restart_required()` evaluating active MT5 service poll interval against persisted configuration; updated `GET /api/forex/settings`, `PATCH /api/forex/settings`, and `POST /api/forex/settings/reset` to truthfully notify when restart is required while ensuring dynamically applied settings never trigger false restart alerts.
  - `tests/test_forex_runtime_phase8.py`: Created dedicated test suite with 8 focused tests:
    - `test_checkpoint_resume_after_mid_graph_crash`: Verifies that an interrupted graph run halts safely, saves step state to checkpointer, and on resume executes remaining nodes without re-executing completed analyst nodes.
    - `test_stream_checkpoint_resume`: Verifies that graph streaming correctly resumes from interrupted checkpoints.
    - `test_dynamic_setting_never_reports_restart_required`: Verifies dynamic settings (`forex_default_pair`, `forex_default_risk_percent`, etc.) apply without false restart notifications on both PATCH and subsequent GET.
    - `test_mt5_poll_interval_change_reports_restart_required`: Verifies changes to `mt5_poll_interval_seconds` truthfully report `restart_required = True`.
    - `test_mt5_poll_interval_unchanged_does_not_falsely_require_restart`: Verifies resubmitting identical poll interval does not trigger restart notices.
    - `test_reset_reports_restart_if_poll_interval_was_overridden`: Verifies restoring defaults reports restart required when poll interval is reset from custom value.
    - `test_mt5_account_when_disconnected_reports_unavailable_metrics`: Verifies disconnected MT5 returns 503 instead of fabricating fake $0.00 balances.
    - `test_proposals_missing_metrics_do_not_fabricate_zeroes`: Verifies missing risk metrics are truthfully preserved rather than coerced to zeroes.
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded ADR-13, Phase 8 execution record, and updated current progress.
- **Verification Gates Passed:**
  - `pytest tests/test_forex_runtime_phase8.py`: 8 passed.
  - `pytest tests/test_forex_graph.py tests/test_checkpoint_resume.py tests/test_checkpoint_lifecycle.py`: 40 passed.
  - `pytest tests/test_forex_routes.py`: 111 passed.
  - `ruff check tradingagents/graph/forex_graph.py web/forex_routes.py tests/test_forex_runtime_phase8.py`: All checks passed.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps. Remaining backlog items: code hygiene (Phase 10), release evidence (Phase 11).

---

## 17. Phase 9 Execution Record (2026-10-03)

- **Completed Scope:** Systematic review, classification, and architectural disposition of all remaining normative `MAY` and `SHOULD` requirements.
- **Reviewed Requirements & Dispositions:**
  - `NEWS-006` (Archive import CLI for economic calendar & news): Classified as **unnecessary for current personal-use scope** and marked **`DEFERRED_OPTIONAL`**. Core programmatic snapshot import (`TradingEconomicsCalendar.import_snapshot(path)`) is fully implemented, verified, and PIT-safe. Dedicated standalone CLI command is intentionally deferred because snapshot JSON files placed in the configured archive directory are automatically discovered and loaded by `read_snapshots()`. Core functionality remains 100% compliant, PIT-safe, and fails closed (`DataInsufficientError`) on missing coverage without destabilizing historical backtests.
  - `WF-008` (Automated Bayesian hyperparameter search): Classified as **unnecessary for current personal-use scope** and marked **`DEFERRED_OPTIONAL`**. Deterministic walk-forward cross-validation (`WalkForwardOptimizer`) evaluates out-of-sample parameter stability and robustness across rolling and anchored splits; automated parameter search is deferred to avoid data snooping and combinatorial overfitting.
  - `JOURNAL-008` (Trade journal structured export): Classified as **valuable optional enhancement**; fully implemented and verified in Phase 6 on `ForexTradeJournal.export_trades(fmt="json"|"csv")` and `ForexJournalManager.export_trades()`.
  - `SEC-009` (Private overlay network) & `DEPLOY-003` (Remote access private tunnel): Classified as **valuable optional enhancements**; documented in `README.md` and enforced via `127.0.0.1:8050` binding baseline in Phase 2.
- **Verification Gates Passed:**
  - Full subsystem regression suite (367+ tests) remains green.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps. Remaining backlog items: code hygiene (Phase 10), release evidence (Phase 11).

---

## 18. Phase 10 Execution Record (2026-10-03)

- **Completed Scope:** Controlled code hygiene, backward-compatibility shim verification, asset-class model isolation, and router maintainability review.
- **Audited Items & Decisions:**
  - `aggressive_debater.py` / `aggressive_debator.py`: Confirmed active requirement for both files. `aggressive_debater.py` is the canonical module re-exporting `create_aggressive_debater` and deprecated alias `create_aggressive_debator`; `aggressive_debator.py` implements the nodes and emits a `DeprecationWarning` on alias usage. Verified via `tests/test_repaired_regressions.py:158`. Both files retained intact per ADR-06 and ADR-15.
  - `AnalysisRequest` Schemas: Confirmed `AnalysisRequest` in `web/server.py` is the active Equities model (`POST /api/analyze`), and `AnalysisRequest` in `tradingagents/research/contracts.py` is the canonical Forex research contract (re-exported as `ForexAnalysisRequest` in `web/forex_routes.py`). Neither is dead code; both are strictly typed and actively tested with 100% test coverage across their respective domains.
  - `web/forex_routes.py` Maintainability: Retained as a single cohesive router per Phase 1 decision and explicit architectural guidelines. Verified 100% clean by Ruff with zero lint or typing errors. Monolithic structure prevents circular dependencies with runtime managers (`_forex_runtime`), locks, and worker thread pools without introducing regression risk.
- **Verification Gates Passed:**
  - `pytest tests/test_repaired_regressions.py`: 5 passed.
  - `pytest tests/test_forex_routes.py`: 111 passed.
  - `pytest tests/test_web_server.py tests/test_forex_runtime_phase8.py tests/test_cli_forex.py`: 46 passed.
  - `pytest tests/test_forex_phase1.py tests/test_run_store_hardening.py`: 37 passed.
  - Repository-wide linting: `ruff check .` passed with 0 errors across all files.
  - `git diff --check`: Clean (no whitespace or line-ending errors).
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps. Only Phase 11 (Full Verification & Release Evidence) remains.

---

## 19. Phase 11 Execution Record (2026-10-03)

- **Completed Scope:** Final automated verification across all project gates, static broker-write safety verification, database migration upgrade/backup/restore verification, truthful release documentation alignment (`README.md`, `FINAL_REPORT.md`, `CHANGELOG.md`), and final release readiness assessment.
- **Verification Gates Passed:**
  - `ruff check .`: PASS — 0 errors across all codebase files.
  - `python -m compileall tradingagents web cli tests`: PASS — 0 syntax or compile errors.
  - Full non-E2E pytest suite (`pytest -q -m "not e2e"`): PASS — 2,295 passed, 5 skipped (Windows POSIX file modes, optional Bedrock without langchain_aws, optional DeepSeek live call without key), 6 deselected, 22 warnings, 88 subtests passed in 83.07s.
  - Full E2E Chromium suite (`pytest -q tests/test_browser_e2e.py`): PASS — 6 passed in 51.63s.
  - Total automated tests passed: **2,301 passed** (0 failed).
  - JavaScript syntax checks (`node -c web/static/app.js`): PASS — 0 errors.
  - Clean install / dependency check (`pip check`): PASS — "No broken requirements found."
  - Format / whitespace check (`git diff --check`): PASS — Clean.
- **Safety Invariants Verified:**
  - **No Broker-Write Capability:** Static inspection for `order_send`, `order_check`, `order_calc_margin`, `order_calc_profit` across `tradingagents/`, `web/`, and `cli/` confirmed zero broker-order placement paths in runtime code. All matches are docstrings, safety tests, or local SQLite journal updates.
  - **Read-Only MT5 Observer:** Single worker, serialized native calls, positional history queries, and fail-closed disconnection handling verified across 62 MT5 tests (`test_mt5.py`, `test_mt5_service.py`, `test_mt5_runtime.py`).
  - **Deterministic Risk Authority:** Sizing engine and risk engine strictly enforce portfolio ceilings, stop-loss presence, net currency stop-risk concentration, and broker digits rounding; LLM recommendations cannot override deterministic rejections (verified by 117 tests in `test_forex_risk_engine.py`, `test_forex_position_sizing.py`, `test_forex_pips.py`, `test_forex_risk_context.py`).
  - **Zero vs Unavailable Semantics:** Disconnected MT5 yields 503; missing account metrics render explicit "Unavailable" badges rather than deceptive $0.00 balances.
  - **Point-in-Time & Historical Integrity:** Completed-bar execution at close, strict pre-period warm-up bar isolation, post-cache PIT filtering, and news/macro publication cutoffs verified by 94 tests in `test_historical_integrity.py`, `test_forex_backtest.py`, `test_walk_forward.py`, `test_ablation_engine.py`.
  - **Trigger-Protected Journal & Database:** Schema versioning, SQLite triggers preventing mutation of original proposals and broker execution facts, and full backup/integrity/restore operations verified by 48 tests in `test_database_backup_restore.py` and `test_forex_journal.py`.
  - **Authentication & Security:** In-memory browser API keys, HttpOnly/SameSite=Strict/host-only session cookies, 127.0.0.1 default binding, and localhost CORS verified by 213 tests across security, web server, and routes.
- **Documentation Updated:**
  - `README.md`: Verified current test counts (2,301 passed) and commit reference (`4c226137477c4d6cb3907c2397361d8ad45f7a26`).
  - `FINAL_REPORT.md`: Updated verification date (2026-10-03), commit hash, exact automated test counts, and release status ("READY FOR XM DEMO ACCEPTANCE; NOT XM DEMO ACCEPTED").
  - `CHANGELOG.md`: Added Master Forex Remediation Plan Completion entry under `[Unreleased]`.
  - `docs/FOREX_REMEDIATION_CONTEXT.md`: Recorded Phase 11 execution record and final progress.
- **Unresolved Remaining Findings:** 0 confirmed implementation gaps.
- **Release Status:** **READY FOR XM DEMO ACCEPTANCE; NOT XM DEMO ACCEPTED** (Pending manual execution of [docs/XM_MT5_DEMO_ACCEPTANCE.md](docs/XM_MT5_DEMO_ACCEPTANCE.md) on live Windows MT5 terminal).

---

## 20. Current Progress

- **Phase 1 (One-Time Full Repository Analysis & Master Remediation Plan):** **COMPLETE**
- **Phase 2 (Security and Deployment Baseline):** **COMPLETE**
- **Phase 3 (CLI MT5 & Shared Application Context Verification):** **COMPLETE**
- **Phase 4 (Deterministic Sizing & Portfolio Risk Completion):** **COMPLETE**
- **Phase 5 (Domain Contracts, Provenance & PIT Integrity):** **COMPLETE**
- **Phase 6 (Proposal Lifecycle & Automatic Expiry):** **COMPLETE**
- **Phase 7 (Learning & Retrieval Completion):** **COMPLETE**
- **Phase 8 (Runtime & UI Verification):** **COMPLETE**
- **Phase 9 (Optional / Low-Priority Capabilities):** **COMPLETE**
- **Phase 10 (Cleanup & Maintainability):** **COMPLETE**
- **Phase 11 (Full Verification & Release Evidence):** **COMPLETE**
- **Overall Status:** **ENGINEERING COMPLETE — READY FOR XM DEMO ACCEPTANCE**

---

## 21. Target-State Financial-Path Remediation Update (2026-10-07)

- **Baseline:** `57e0eb4b282ee7f715a5fb18e7c46a95d80f7b4c`; changes remain intentionally uncommitted.
- **Implemented:** one sizing authority, policy-risk propagation, broker contract-size and precision authority, causal next-observation historical fills, fail-closed zero/unavailable lots, point-in-time ATR, pending-exposure parity, and configurable UTC daily-loss reset.
- **Verification:** 2,378 non-E2E tests and 9 Chromium E2E tests passed; Ruff, compileall, JavaScript syntax, dependency integrity, and whitespace checks passed.
- **Browser:** verified 3-second polling, UTC reset-hour control, manual/read-only mode, and truthful unavailable states in the running application.
- **External boundary:** GitHub CI and the real XM manual lifecycle checklist remain pending. This is not an XM acceptance or trading-performance claim.
- **Requirement evidence:** see `docs/TARGET_STATE_REQUIREMENT_MATRIX.md`.
