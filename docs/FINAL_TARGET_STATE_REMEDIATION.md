# Final Target-State Remediation

## Authority and baseline

- Target specification: `TradingAgents Forex Target-State System Requirements Specification`, 45 pages, supplied as `TradingAgents_Target_State_System_Requirements_Specification (1).pdf`.
- Repository branch: `main`.
- Baseline revision: `57e0eb4b282ee7f715a5fb18e7c46a95d80f7b4c` (`Version 3`).
- Remote `origin/main`: `57e0eb4b282ee7f715a5fb18e7c46a95d80f7b4c`.
- Baseline working tree: clean before this remediation began.
- Baseline local verification (2026-10-07): Ruff passed; non-E2E suite passed with 2361 tests, 5 environment-only skips, 9 deselected tests, and 88 passing subtests.
- GitHub verification for this new remediation: pending until the user reviews, commits, and pushes the finished working tree.

The SRS is the target behavior. Existing source, tests, and completion claims are evidence only and do not override normative SRS requirements.

## Non-negotiable invariants

1. Broker execution remains manual. Production runtime must not place, modify, cancel, or close MT5 orders or positions.
2. MT5 access remains read-only.
3. Deterministic risk policy and the single sizing authority cannot be overridden by an LLM.
4. Missing, stale, invalid, or inconsistent authoritative financial data fails closed; no plausible values are fabricated.
5. Historical calculations remain point-in-time and cannot use future data or decision-bar execution.
6. Secrets and unmasked broker identity must not appear in repository content or public output.
7. Python 3.10-3.13 and inherited equity behavior remain supported.

## Confirmed defects and planned corrections

| Phase | Requirement IDs | Confirmed defect | Principal files | Required evidence |
|---|---|---|---|---|
| 1 | TEST-001, TEST-008, SEC-001 | Tracked inspection scripts, screenshots, logs, and generated market cache remain under `tmp/` and `data/cache/`. | `.gitignore`, tracked transient files | Clean tracked-file inventory; Ruff and non-E2E suite remain green. |
| 2 | ARCH-003, SIZE-001, SIZE-006 | `ForexRiskEngine.validate_proposal()` calls `lot_size_from_risk()` before `ForexPositionSizingEngine`, creating two sizing authorities; the sizing engine consumes proposal risk rather than the policy-approved effective risk. | `tradingagents/risk/engine.py`, `tradingagents/risk/sizing.py`, `tradingagents/graph/forex_graph.py` | Graph tests prove one sizing call, policy clamp/scaling propagation, cross-pair handling, and fail-closed rejection. |
| 3 | FX-001, SIZE-008 | Pip risk can use static pair contract size while margin/units use broker contract size; historical/broker-aware execution contains hard-coded five-decimal rounding. | `tradingagents/forex/pips.py`, `tradingagents/risk/sizing.py`, `tradingagents/mt5/observer.py`, backtest engine | Contract-size and price-normalization tests across 3/5/custom digits and nonstandard contract sizes. |
| 4 | BT-001 through BT-005, TEST-005 | Historical execution turns `None` or zero lot into 0.1 and permits decision-bar/same-close fills. | `tradingagents/backtest/forex_engine.py`, `agent_backtester.py`, `walk_forward.py` | Zero/None remain unfilled; proposals queue until the next eligible observation; final-bar proposals remain unfilled. |
| 5 | BT-006 through BT-014 | Historical ATR, configured broker assumptions, conversion reuse, evolving portfolio context, ambiguity handling, and disclosure require parity review and targeted completion. | historical pipeline/data, backtest engine, conversion and cost paths | PIT ATR/conversion, evolving account/pending risk, conservative ambiguity, and performance-truth tests. |
| 6 | PORT-001 through PORT-012, MT5-001 through MT5-013 | MT5 application context silently substitutes zero pending exposure when the observer lacks the required capability; daily-loss boundary is fixed rather than explicitly configurable. | `tradingagents/forex/application.py`, MT5 observer/runtime/config/API | Fail-closed pending exposure and consistent configurable reset-boundary tests. |
| 7 | CFG-*, RUN-*, NFR-*, API-* | Operational reliability must be reverified after financial-path changes. | runtime, routes, persistence and cancellation code | Focused cancellation, checkpoint, retention, restore, serialization, liveness/readiness tests. |
| 8 | SEC-* | Repository contains tracked transient outputs and must be checked for secrets, account identifiers, local paths, databases, and caches. | repository hygiene and current evidence docs | Sanitized static search; no production broker-write primitive. |
| 9 | REL-001 through REL-005 | Completion documents may describe earlier revisions and must not overstate current GitHub/XM validation. | README, final report, changelog, remediation docs | Wording states local verification and pending external gates truthfully. |
| 10 | MT5-*, EXEC-* | XM lifecycle remains an external human acceptance activity. | XM acceptance tooling/docs/tests | Read-only tooling passes; real lifecycle remains `MANUAL_ACCEPTANCE_PENDING`. |
| 11 | all P0/P1 requirements | Existing matrices cannot substitute for a final source-and-test recheck. | this document and implementation evidence | Requirement-by-requirement final matrix with exact status and limitations. |

## Phase order

1. Restore repository hygiene and retain the green engineering baseline.
2. Establish one authoritative sizing path and propagate effective policy risk.
3. Make broker contract size and quote precision authoritative.
4. Correct historical zero-lot behavior and execution causality.
5. Complete historical/live deterministic parity.
6. Close portfolio/MT5 edge cases and configure the daily-loss boundary.
7. Reverify configuration, runtime, persistence, cancellation, and health semantics.
8. Perform targeted security/privacy and repository-hygiene checks.
9. Update documentation only after implementation verification.
10. Validate read-only XM acceptance support without broker writes.
11. Re-read the SRS and produce the final P0/P1 compliance matrix.

## External and manual boundaries

- No paid provider call is required for remediation tests.
- A connected XM terminal is not required for deterministic unit/integration tests.
- Opening, modifying, partially closing, or closing a demo trade remains a human action in MT5.
- Real XM lifecycle evidence, reconciliation, real-trade MFE/MAE, post-close learning, and non-empty restart persistence remain `MANUAL_ACCEPTANCE_PENDING` until performed by the user.
- GitHub CI remains pending until the user commits and pushes; no commit or push will be performed by this remediation.

## Verification gates

- Focused regression tests for every affected phase.
- `ruff check .`
- `pytest -q -m "not e2e"`
- canonical browser E2E suite.
- `python -m compileall tradingagents web cli tests`
- JavaScript syntax validation.
- `git diff --check`.
- `pip check` and the safe clean-install smoke workflow where practical.
- Static classification of broker-write, fallback sizing, fixed precision, conversion, and failure-swallowing patterns.

## Current completion state

Local implementation and verification completed on 2026-10-07. The non-E2E suite
passed with 2,378 tests, 5 environment-only skips, 9 deselected tests, 20 warnings,
and 88 passing subtests. The Chromium E2E suite passed 9 tests. Ruff, compileall,
JavaScript syntax, dependency integrity, and Git whitespace checks passed. Live
browser inspection confirmed the 3-second MT5 polling value, configurable UTC
daily-loss boundary, immutable manual/read-only mode, and truthful unavailable
states.

The detailed normative mapping is in
[`TARGET_STATE_REQUIREMENT_MATRIX.md`](TARGET_STATE_REQUIREMENT_MATRIX.md).
GitHub CI remains pending until the user commits and pushes. Real XM manual
lifecycle acceptance remains `IMPLEMENTED_EXTERNAL_VALIDATION_PENDING`; it is not
substituted by fixtures or automated tests.
