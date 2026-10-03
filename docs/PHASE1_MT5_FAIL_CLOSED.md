# Phase 1 - Fail-closed MT5 data semantics

Evidence date: 2026-10-02 (Asia/Karachi)

Status: **COMPLETE**

## Implemented contract

The MT5 observer now distinguishes a broker API failure (`None`) from a
broker-confirmed empty result (an empty tuple or list) for:

- symbol catalogue reads;
- open-position reads;
- pending-order reads; and
- historical-order reads.

A `None` response raises `MT5DataError` with the MT5 error code. Verified-empty
responses continue to return an empty list, and populated responses retain their
existing model conversion behavior.

## Fail-closed reconciliation

Proposal reconciliation no longer substitutes an empty portfolio when the MT5
position read fails. It returns a service-unavailable response and does not call
the journal reconciliation operation. The global API error handler keeps the
broker's internal error description out of the client response.

The background observer poll is also fail-closed: it fetches positions, pending
orders, and deals before mutating its known broker state, so any `MT5DataError`
aborts that poll without inferring an empty exposure snapshot.

## Regression coverage

The Phase 0 strict expected failures are now ordinary passing tests. Coverage
includes failure, verified-empty, and populated responses, plus the API-level
reconciliation failure path.

## Scope

This phase changes read and reconciliation error semantics only. It adds no MT5
trade-placement capability and does not alter the system's manual-only execution
policy.

## Verification evidence

- Focused MT5 and Forex route suites: `144 passed`.
- Full non-browser suite: `2202 passed, 5 skipped, 88 subtests passed`.
- Browser end-to-end suite: `6 passed`.
- Ruff full-repository check: passed.
- Python byte-compilation (`tradingagents`, `web`, and `cli`): passed.
- JavaScript syntax checks for every file under `web`: passed.
- Git whitespace/error check: passed (Git emitted only the pre-existing
  LF-to-CRLF working-copy notice for `docs/phase30-historical-integrity.md`).
