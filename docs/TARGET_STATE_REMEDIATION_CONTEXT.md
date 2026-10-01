# Target-State Remediation Context

This is the compact continuation record for work derived from the 2026-10-01
target-state compliance audit. Use it instead of re-auditing the repository before
the next planned phase.

## Phase 1 - Shared Context and CLI Correctness

Status: complete in the current working tree; not yet committed.

### Files changed

- `tradingagents/forex/application.py` - new framework-independent manual and MT5
  application-context builders.
- `web/forex_routes.py` - web analysis now consumes the shared builders; route,
  authentication, streaming and graph behavior remain unchanged.
- `cli/main.py` - explicit account source, complete manual inputs, authoritative
  MT5 context, execution/context timeframe forwarding, deterministic summary and
  manual-only warning.
- `tests/test_forex_application.py` - shared builder contract and fail-closed tests.
- `tests/test_cli_forex.py` - account-source, manual completeness, zero margin,
  timeframe and MT5 integration regressions.
- `README.md` and `docs/FOREX_REMEDIATION_CONTEXT.md` - focused CLI contract updates.

### Architectural decisions

- `tradingagents.forex.application` is the single presentation-neutral context
  assembly boundary for both browser and CLI.
- Connection lifecycle remains owned by the caller. The builder only accepts a
  connected read-only observer and fails closed when it is unavailable.
- Manual mode has no fabricated broker/market risk context. It supplies a complete
  caller-provided account and is explicitly marked `broker_verified=false`.
- MT5 mode carries account, tick/spread, deterministic ATR, broker constraints,
  open positions, conversion observations and optional daily P&L into the existing
  immutable `ForexRiskContext`.
- No broker order placement, modification or cancellation capability was added.

### Requirements and bugs completed

- `SCOPE-005`
- `ARCH-002`
- `API-007`
- `CLI-001`
- `CLI-002`
- `CLI-003`
- `CLI-004`
- `BUG-001`
- `BUG-002`

### Verification

- Focused shared-context/CLI/web/MT5/risk suite: 159 passed.
- Full non-E2E suite: 2,168 passed, 5 skipped, 6 deselected, 22 warnings,
  88 subtests passed.
- Chromium E2E: 6 passed, 2,173 deselected.
- Full Ruff: passed.
- Python compileall: passed.
- JavaScript syntax checks: passed.
- CLI help/import smoke: passed.
- `git diff --check`: passed.

The five skips remain environment/optional: three POSIX permission tests on
Windows, optional Bedrock without `langchain_aws`, and optional live DeepSeek
without credentials.

### Unresolved items affecting later phases

- Phase 2 backup/restore remains entirely unimplemented.
- Phase 3 health/readiness, provider usage accounting and remaining E2E expansion
  remain pending.
- Phase 4 operational reliability, Phase 5 real Windows/XM evidence, and Phase 6
  final release provenance remain pending.

### Suggested commit

`fix(forex-cli): use authoritative account and timeframe context`

## Phase 2 - Backup, Restore, and Disaster Recovery

Status: complete in the current working tree; not yet committed.

### Files changed

- `tradingagents/database/backup.py` - new SQLite backup, validation, metadata,
  guarded restore, safety-backup and rollback service.
- `tradingagents/database/journal.py` - refuses new file connections while a
  restore maintenance marker is active.
- `tradingagents/database/__init__.py` - exports the maintenance contracts.
- `cli/main.py` - adds `journal backup` and confirmation-gated `journal restore`.
- `tests/test_database_backup_restore.py` - focused backup/restore acceptance and
  failure-path coverage.
- `.gitignore` - excludes SQLite journals, WAL/SHM files and backup metadata.
- `README.md` - documents the CLI recovery workflow and safety backup.

### Architectural decisions

- Backups use `sqlite3.Connection.backup()` and are validated before publication;
  an active database file is never copied raw.
- Every published backup has UTC creation time, application/schema version,
  SHA-256 hash and source filename metadata. No environment or credential values
  are recorded.
- Restore validates integrity, expected TradingAgents tables, supported schema and
  optional metadata hash before obtaining the maintenance boundary.
- The journal lock plus a filesystem maintenance marker blocks in-process work and
  new connections from other application processes. SQLite exclusive-lock probing
  rejects restore while an existing writer remains active.
- Restore creates and retains a validated pre-restore safety backup, stages through
  SQLite backup semantics, atomically publishes the replacement, applies compatible
  migrations and validates the result. A failed reinitialization restores the
  safety snapshot.
- Administration is CLI-only in this phase; no web restore attack surface was added.

### Requirements completed

- `BACKUP-001`
- `BACKUP-002`
- `BACKUP-003`
- `BACKUP-004`
- `RESTORE-001`
- `RESTORE-002`
- `RESTORE-003`
- `RESTORE-004`
- `TEST-010`

### Verification

- Focused database/journal/CLI suite: 75 passed.
- Full non-E2E suite: 2,176 passed, 5 skipped, 6 deselected, 22 warnings,
  88 subtests passed.
- Chromium E2E: 6 passed, 2,181 deselected.
- Full Ruff: passed.

### Unresolved items affecting later phases

- Phase 3 health/readiness, provider usage accounting and E2E expansion remain
  pending.
- Phase 4 operational reliability should include stale-maintenance-marker recovery
  guidance alongside its shutdown/logging work.
- Phase 5 must exercise backup/restore on the real Windows target host while MT5
  observation is stopped and then restarted.
- Phase 6 final release provenance remains pending.

### Suggested commit

`feat(database): add verified SQLite backup and guarded restore`

## Phase 3 - Operational Health, Actual LLM Usage, and Browser Gate

Status: complete in the current working tree; not yet committed.

### Files changed

- `tradingagents/llm_clients/usage.py` - run-scoped, provider-metadata-only token
  accounting with duplicate-response protection and no prompt/content retention.
- `tradingagents/llm_clients/response_validation.py` and Forex structured-output
  handling - capture both ordinary and structured model response metadata.
- `tradingagents/database/migrations.py` and `journal.py` - migration 4 plus
  idempotent actual-usage persistence and aggregation.
- `web/server.py` - stable public liveness and authenticated readiness contracts;
  equity runs expose actual usage or explicit `unavailable` status.
- `web/forex_routes.py` - Forex workers persist and expose the same usage contract.
- `tests/test_llm_usage.py`, `tests/test_web_server.py`, and
  `tests/test_browser_e2e.py` - privacy, de-duplication, persistence, authentication,
  degraded dependency, and real-browser health coverage.

### Architectural decisions

- Usage is actual provider-reported metadata only. The system never estimates
  missing counters and reports `status=unavailable` instead.
- Usage storage contains only provider/model identifiers and numeric token counts;
  prompt and response bodies are excluded.
- Liveness proves that the web process can answer and has no provider dependency.
  Readiness requires the journal database, while disconnected MT5 is an explicit
  degraded optional component rather than a false application outage.
- Readiness details require dashboard authentication and contain no credentials,
  filesystem paths, connection errors, or account data.

### Requirements completed

- `API-003`
- `COST-003`
- `TEST-007`

### Verification

- Focused health/usage/backup suite: 30 passed.
- Full non-E2E suite: 2,181 passed, 5 skipped, 22 warnings, 88 subtests passed.
- Chromium E2E: 6 passed.
- Full Ruff: passed.
- `git diff --check`: passed (Git emitted line-ending notices only).

The five skips remain environment/optional: three POSIX permission tests on
Windows, optional Bedrock without `langchain_aws`, and optional live DeepSeek
without credentials.

### Unresolved items affecting later phases

- Phase 4 operational reliability and stale-maintenance-marker recovery remain
  pending.
- Phase 5 must produce real Windows/XM evidence, including actual provider usage
  availability and backup/restore while observation is stopped.
- Phase 6 final release provenance remains pending.

### Suggested commit

`feat(operations): add health probes and actual LLM usage accounting`

## Phase 4 - Operational Reliability

Status: complete for implementable local reliability work in the current working
tree; real-host soak/resource acceptance remains Phase 5 evidence.

### Implemented

- Size-bounded rotating application logs with validated directory, file-size and
  retention controls, enabled for CLI and dashboard lifespan startup.
- Explicit provider defaults of two retries and a 120-second per-request timeout,
  range-validated and forwarded through generic and Forex graph factories.
- Tracked equity analysis workers, cooperative shutdown checks between graph
  stages, and bounded joins before Forex runtime/database teardown.
- Stale active runs now terminate as `stale` rather than retaining a misleading
  running status in expiration diagnostics.
- Restore maintenance markers include PID/time ownership. Markers are recovered
  only after a five-minute grace period when the recorded process is provably
  dead; malformed, recent and live-owner markers fail closed.
- Synthetic rotation/load, marker recovery, provider forwarding, run-retention,
  MT5 stalled-stop and reconnect tests cover the implementable reliability paths.

### Requirements completed

- `RUN-007`
- `DEPLOY-005`
- `DEPLOY-006`
- `NFR-005`

### Verification

- Focused operational/database/retention/MT5 suite: 75 passed.
- Full non-E2E suite: 2,192 passed, 5 skipped, 22 warnings, 88 subtests passed.
- Chromium E2E: 6 passed.
- Full Ruff, Python compilation, JavaScript syntax and `git diff --check`: passed.

### Remaining acceptance boundary

- Phase 5 must measure memory/CPU and long-duration stability on the real Windows
  target, exercise actual provider retry/timeout behavior, and execute shutdown,
  restart and recovery with XM MT5. Automated tests are not reported as real soak
  or broker evidence.

### Suggested commit

`feat(operations): bound logs providers and graceful shutdown`

## Phase 5 - Windows and XM Acceptance

Status: partial. Windows-host, offline lifecycle, restart, short resource/load and
log-rotation evidence were captured. XM demo acceptance is blocked because MT5
Python IPC timed out before a connected account could be verified.

See `docs/PHASE5_ACCEPTANCE_EVIDENCE.md` for measured evidence and
`docs/XM_MT5_DEMO_ACCEPTANCE.md` for the still-unchecked manual broker workflow.

The project MUST remain below `XM_DEMO_ACCEPTED` until the connected-account,
manual trade lifecycle, costs, learning and non-empty restart checks are completed.

## Phase 6 - Final Release Verification

Status: local engineering gates complete in the current working tree; commit,
GitHub CI, and the manual XM trade lifecycle remain pending.

### Implemented and verified

- Corrected MT5 deal and order history calls to use the vendor API's positional
  date-range signature while retaining keyword filters for position and ticket.
- Serialized native MT5 reads with a process-wide reentrant lock because the
  vendor module is process-global and is accessed by both background observation
  and dashboard request threads.
- Added regression coverage for date-range signatures, keyword filters, and
  cross-observer concurrent access.
- Verified the XM Global demo terminal read-only path: connected demo account,
  1,639 symbols, EURUSD tick, positions, pending orders, 11 recent deals, and 8
  historical orders.
- Verified 20 concurrent dashboard read requests on a fresh server with zero
  failures. No broker order submission, modification, or close path exists.

### Release gates

- Full non-browser suite: 2,193 passed, 5 skipped, 22 warnings, 88 subtests.
- Chromium E2E: 6 passed.
- Ruff, Python compilation, JavaScript syntax, and `git diff --check`: passed.
- GitHub CI: pending commit and push of the Phase 6 patch.

### Acceptance boundary

The local working tree is `READY_FOR_XM_DEMO`, not `XM_DEMO_ACCEPTED`. The
analysis-to-proposal flow, user-performed open/modify/partial-close/final-close
lifecycle, resulting costs and learning, and non-empty restart persistence still
require manual evidence in `docs/XM_MT5_DEMO_ACCEPTANCE.md`.

### Suggested commit

`fix(mt5): serialize reads and correct history queries`
