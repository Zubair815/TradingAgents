# Phase 0 - Comprehensive audit baseline

Evidence date: 2026-10-02 (Asia/Karachi)

Status: **COMPLETE - DEFECTS REPRODUCED; PRODUCTION FIXES DEFERRED TO PHASE 1**

## Baseline identity

- Repository: `Zubair815/TradingAgents`
- Branch: `main`
- Baseline commit: `4c226137477c4d6cb3907c2397361d8ad45f7a26`
- GitHub Actions run: `36926316180`
- GitHub result: seven of seven jobs passed.
- Existing user-owned edits preserved: `FINAL_REPORT.md` and
  `docs/phase30-historical-integrity.md`.

Passing CI is baseline evidence only. The four broker-failure cases below were
not represented by the passing suite and therefore were not disproved by it.

## Reproduced MT5 defects

Strict expected-failure tests now document the required Phase 1 behavior. They
remain `xfail(strict=True)` so an unexpected implementation change cannot silently
convert them into an unreviewed pass.

| Reproduction | Current incorrect behavior | Required Phase 1 behavior |
| --- | --- | --- |
| `symbols_get() -> None` | Returns a successful empty catalogue | Raise sanitized `MT5DataError` |
| `positions_get() -> None` | Returns a successful empty portfolio | Raise sanitized `MT5DataError`; risk fails closed |
| `orders_get() -> None` | Returns successful zero pending exposure | Raise sanitized `MT5DataError`; risk fails closed |
| `history_orders_get() -> None` | Returns successful empty history | Raise sanitized `MT5DataError`; reconciliation reports unavailable |

Successful empty tuples and lists are not defects. Phase 1 must retain them as
verified-empty broker responses and add the full None/empty/populated test matrix.

## Pre-run cost-estimator baseline

Inputs for both samples: 200 bars, sampling interval 4, maximum 25 analysis
points, 4,000 estimated tokens per analysis, and the current fixed price.

| Case | Analysis points | Estimated calls | Estimated tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| One analyst | 25 | 100 | 100,000 | $0.30 |
| Three analysts | 25 | 150 | 100,000 | $0.30 |

This baseline confirms the Phase 3 gap: analyst count changes call count but not
tokens or cost, and the estimator does not accept debate/risk rounds, quick/deep
model pricing, separate input/output tokens, pricing availability, or uncertainty
disclosures. Production estimator behavior is intentionally unchanged in Phase 0.

## Phase 0 exit decision

- All four MT5 defects have deterministic reproductions.
- Current cost behavior has exact captured outputs.
- The baseline commit and successful GitHub run are recorded.
- Existing uncommitted work was preserved.
- No production runtime behavior was changed.

Phase 1 may now implement fail-closed MT5 data semantics and convert the four
strict expected failures into ordinary passing regression tests.

## Verification evidence

- Focused MT5 suite: `30 passed, 4 xfailed`.
- Full non-browser suite: `2193 passed, 5 skipped, 4 xfailed, 88 subtests passed`.
- Browser end-to-end suite: `6 passed`.
- Ruff full-repository check: passed.
- Python byte-compilation (`tradingagents`, `web`, and `cli`): passed.
- JavaScript syntax checks for every file under `web`: passed.
- Git whitespace/error check: passed (Git emitted only the existing LF-to-CRLF
  working-copy notice for `docs/phase30-historical-integrity.md`).
- Baseline GitHub Actions: all seven jobs passed in run `36926316180`.

The four expected failures are the Phase 0 defect reproductions listed above;
they are not unclassified test failures or release regressions.
