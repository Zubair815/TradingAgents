# Phase 0 — truthful behavior and execution separation

Status: **GREEN — existing safeguards verified; two remaining defects fixed.**

## Inspection

- Started from clean local `main`, commit `5dd0dbf`.
- Read the current route worker, graph portfolio manager, demo-backtest route,
  regression tests and documentation. Historical audit/report claims were not
  treated as proof.
- The real graph is now connected to web analysis. Restoring the old blanket
  HTTP 503 response would remove functioning later-phase work, so it was not done.
- Confirmed proposals do not create actual trades; all three legacy
  `auto_record_trades=True` entry points reject that setting.
- Confirmed web backtests require explicit demo opt-in, retain demo/source labels
  in responses and reports, and do not write simulation trades into the actual journal.

## Changes

- Reject incomplete graph results instead of marking them completed and emitting
  a success event without a proposal or deterministic risk decision.
- Remove random proposal identifiers and the unrelated latest-proposal fallback.
  Missing identity remains `null`; durable identity propagation belongs to Phase 1.
- Correct obsolete README and route documentation. Mark `FINAL_REPORT.md` as a
  historical report whose broad completion claims still need phase-by-phase verification.

## Tests and gates

Commands use the repository virtual environment.

| Gate | Result |
| --- | --- |
| Initial targeted route/graph suite | 63 passed |
| Initial full suite on main | 1,735 passed, 5 skipped, 88 subtests passed |
| Four new regressions | Missing final state, proposal or risk decision; no invented/borrowed identity |
| `python -m pytest -q tests/test_forex_routes.py tests/test_forex_graph.py` | 67 passed |
| `python -m ruff check .` | All checks passed |
| `python -m pytest -q` | 1,739 passed, 5 skipped, 88 subtests passed |
| Diff review / `git diff --check` | Reviewed; no whitespace errors |

The five skips are three POSIX permission tests on Windows, optional AWS support,
and a live DeepSeek test without credentials. Existing model-list warnings remain.
External provider calls were not used to establish this phase's safety contracts.

## Scope review and next phase

The patch is limited to Phase 0 behavior, regression tests and truthful documentation.
Existing correctly implemented safeguards were retained. Phase 1 may now inspect
shared contracts, stable identities and persistence against this verified baseline.
