# TradingAgents stabilization report — 2026-10-15

## Scope and revision

- Baseline branch: `main`
- Baseline Git revision: `ca29b78d9cc8826ff4d2877ca1babba17f4a52ad`
- Verification target: that revision plus the explicitly listed working-tree
  changes. This is not a clean committed release revision.
- No paid LLM request, broker write, real-money action, or destructive Git
  operation was performed.
- Existing user-owned UI and Groq retry changes were preserved.

## Readiness

Classification: **Locally Tested**.

The implementation passes the local non-E2E and Chromium gates. It is not XM
demo accepted, remotely CI-verified for these uncommitted changes, statistically
validated for profitability, or production ready.

## Finding dispositions

| Finding | Status | Result |
|---|---|---|
| F-01 request provenance | Fixed for new runs | The validated request is passed into graph execution and persisted as the canonical immutable request. Dashboard and canonical IDs, revision, models, stage/run budgets, timeframes, risk/account settings, depth, blackout, and analyst selection are retained. Failure events also retain the canonical ID. Legacy absent fields remain `null`; no defaults are fabricated. |
| F-02 LLM completion and cost | Partially fixed | Existing completion validation and error-specific retries were retained; stage-specific output caps and hard run token/call ceilings were added. The call ceiling also works when providers omit usage metadata. Budget or incomplete output fails explicitly and cannot become a completed proposal. No paid-provider call was made, and a live preflight cost estimate remains unavailable. |
| F-03 exact-revision CI | Partially fixed | CI now names the exact SHA, injects it into manifests, and emits a SHA-named release-evidence artifact only after all jobs pass. Local gates passed. Remote CI and branch-protection enforcement are unverified for the working tree. |
| F-04 XM and strategy validation | Blocked externally | Manual-only/read-only boundaries remain. The 25-step XM lifecycle is still incomplete. A predeclared causal/OOS/walk-forward/cost/uncertainty protocol was added in `docs/STRATEGY_VALIDATION_PROTOCOL.md`; no profitability claim is made. |
| F-05 admission control | Fixed | A thread-safe controller bounds active and queued analyses, enforces FIFO admission, supports idempotency keys, rejects conflicts, returns HTTP 429 with retry guidance when full, and releases capacity on cancellation/failure. Per-run call/token ceilings bound provider work. |
| F-06 documentation accuracy | Fixed for this audit | This evidence record separates local checks, remote CI, broker acceptance, and strategy validation and records the dirty baseline honestly. |
| F-07 route responsibilities | Partially fixed | High-risk admission/idempotency state was extracted to `web/analysis_admission.py` behind the existing route contract. Further orchestration, journal, analytics, MT5, and settings extraction remains incremental work. |
| F-08 supervision/recovery | Partially fixed | Tests now write to an isolated temporary log directory. Startup logs include PID/revision; shutdown logs include reason and active worker count. Existing liveness/readiness and restart recovery tests pass. The historical process disappearance has not been causally explained; a real-host soak remains required. |
| F-09 model warnings | Fixed | Models are classified as supported, legacy, deprecated, provider-unverified, or unknown. Common compatibility IDs no longer emit false unknown warnings; deprecated and genuinely unknown IDs remain visible. |

## Additional confirmed finding

On Windows, multiple lifecycle operations can share one coarse clock tick. A
human WAIT/SKIP decision could therefore appear to occur at the exact proposal
creation time. User-decision events now advance at least one microsecond beyond
prior proposal events, preserving deterministic audit chronology. The focused
route regression passes.

## Local verification evidence

| Gate | Result |
|---|---|
| Focused admission, usage, provenance, routes, warning, runtime and recovery tests | Passed; the final combined focused runs were green after correcting the timeline edge case. |
| `.venv\Scripts\python.exe -m pytest -q -m "not e2e"` | Exit 0: **2409 passed, 5 skipped, 10 deselected, 10 warnings, 102 subtests passed** in 76.74s. Warnings were model-drift diagnostics and documented platform/optional skips. |
| `.venv\Scripts\python.exe -m pytest -q -m e2e` | Exit 0: **10 passed, 2414 deselected** in 50.10s. |
| `.venv\Scripts\python.exe -m ruff check .` | Exit 0. |
| Existing-environment dependency check | Final `pip check` exit 0: no broken requirements. |
| SQLite read-only integrity | Final check: `ok`, zero foreign-key violations, WAL mode. |
| Fresh Python 3.12 install | Blocked locally: dependency download/build completed, but installation made no progress during the final install phase and was interrupted; the package was not installed in that isolated environment. CI retains the clean-install gate. |
| Remote GitHub Actions | Not run/inspected for this uncommitted working tree. |
| XM connected lifecycle | Items 6–25 remain incomplete; **NOT XM DEMO ACCEPTED**. |
| Strategy profitability | Not established. |

The skipped non-E2E checks were three POSIX-mode tests on Windows, one optional
Bedrock dependency test, and one credential-gated live DeepSeek test. No live
integration tests were enabled.

## Remaining blockers and shortest next steps

1. Review and commit the intended working-tree changes to create one immutable
   revision.
2. Push that revision and require all matrix, E2E, lint, clean-install, and
   exact-revision evidence jobs through branch protection.
3. Run the revision-locked XM demo checklist manually through item 25 without
   enabling automated order placement.
4. Freeze the strategy configuration and execute the predefined out-of-sample
   and paper-forward protocol; report insufficient or failed evidence honestly.
5. Run the documented real-host operational soak to investigate process exits;
   do not assign a cause without captured evidence.
