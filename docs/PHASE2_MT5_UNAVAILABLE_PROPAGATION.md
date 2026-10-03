# Phase 2 - End-to-end MT5 unavailable-state propagation

Evidence date: 2026-10-02 (Asia/Karachi)

Status: **COMPLETE**

Requirements: `PORT-002`, `MT5-009`, `DATA-003`, `TEST-004`

## Implemented contract

- The shared MT5 application-context builder requires both verified position
  and pending-order snapshots before constructing deterministic risk context.
- Pending-order availability and count are retained in the immutable portfolio
  context. Unknown pending exposure stops analysis rather than becoming zero.
- Web account, symbol catalogue, quote, position, order, deal-history, and
  reconciliation failures return a sanitized `MT5_DATA_UNAVAILABLE` code.
- The symbol catalogue no longer substitutes a successful major-pair list when
  its MT5 query fails.
- Proposal reconciliation returns service unavailable and performs no journal
  reconciliation when the portfolio snapshot cannot be verified.
- Background observation obtains positions, pending orders, and deal history
  before committing snapshot changes. Failed polls retain prior known state,
  record the safe exception type, and recover normally after a successful poll.
- CLI MT5 mode returns a nonzero result and never constructs or runs the graph
  when authoritative exposure is unavailable.
- Dashboard collection failures render `Unavailable` with a stale-data warning;
  a later successful refresh replaces that state with current broker data.

## Safety boundaries preserved

- MT5 remains read-only and manual execution remains mandatory.
- Vendor error text and credentials are not returned to browser clients.
- A failed exposure read cannot reach proposal approval or deterministic sizing.
- Verified-empty tuples/lists remain valid broker snapshots.
- Pending orders remain excluded from the existing open-position limit; this
  phase makes their availability explicit without changing risk policy.

## Regression coverage

- Shared context: failed positions and pending orders stop context construction.
- Analysis worker: unavailable exposure never instantiates the graph.
- REST endpoints: collection failures return 503 plus the structured safe code.
- Reconciliation: no journal operation occurs after a failed position read.
- Observer: failure after success preserves state; deal-history failure commits
  no partial snapshot; the next successful poll recovers normally.
- Chromium: positions and orders visibly become unavailable and then recover.

## Verification evidence

- Focused context, observer-service, route, and CLI suites: `137 passed`.
- Full non-browser suite: `2212 passed, 5 skipped, 88 subtests passed`.
- Chromium end-to-end suite: `6 passed`.
- Ruff full-repository check: passed.
- Python byte-compilation (`tradingagents`, `web`, and `cli`): passed.
- JavaScript syntax checks for every file under `web`: passed.
- Git whitespace/error check: passed; Git emitted only line-ending notices.
- Broker-write static search: no runtime broker-write call exists; the only
  match is the read-only service docstring stating that `order_send()` is never
  invoked.

The five skips are the existing Windows-inapplicable POSIX permission tests and
optional/live-provider tests whose dependencies or credentials are unavailable.
