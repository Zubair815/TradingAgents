# Phase 31: Continuous read-only MT5 runtime

The FastAPI lifespan owns one Forex runtime. API dependencies, observation,
journal reconciliation, history retrieval and learning share the same observer,
connection, journal and managers. No frontend changes are included.

## Lifecycle policy

- Startup initializes shared services without launching or logging into MT5.
  Missing MT5 packages, credentials or terminal connectivity leave the app usable.
- `POST /api/forex/mt5/connect` connects the shared observer and starts one worker.
  Repeated connect requests do not create another worker.
- Disconnect stops polling before shutting down the connection. Reconnect joins
  any old worker and reuses the runtime and journal.
- Shutdown uses bounded joins. A stalled native call keeps its worker reference
  and journal alive; daemon cleanup releases resources after the call returns.
  Reconnect is rejected while cleanup is pending, preventing overlapping workers.
- One application process must own a terminal/journal runtime. Multiple ASGI
  worker processes are not coordinated by this in-process ownership mechanism.

## Automatic journal and learning flow

Positions use the existing proposal matcher. Confident matches link the proposal;
unmatched positions remain manual/unplanned, with reconciliation evidence retained.
The journal records observed SL/TP changes and breakeven moves. Broker fills are
stored atomically with lifecycle events and signed profit, commission, fee and swap
totals. A stable deal key prevents replay from duplicating executions or reductions.
The broker position identifier is retained independently from its ticket.

Only confirmed exit deals close a trade; an absent position alone does not.
Final close queues the shared post-close processor automatically. It obtains M1
history, computes excursions using initial stop risk, compares the proposal,
classifies the outcome, generates reflection and persists lessons. Interrupted
PENDING/PROCESSING work is recovered after restart. Missing holding-window history,
reflection failures and lesson-store failures leave the trade CLOSED and processing
FAILED, allowing observation of other positions to continue.

Authenticated `POST /api/forex/journal/trades/{trade_id}/post-close/retry` retries
processing. Successful repeated requests are idempotent. Open or missing trades
return 409 or 404. No broker state is changed by retry.

`GET /api/forex/mt5/status` includes service_running, mt5_connected, last_poll_at,
last_successful_poll_at, last_error, poll_interval, tracked_positions and
tracked_orders. Login values are masked; password values are not returned.

## Verification and limits

Baseline: Ruff passed; 1,954 tests passed, 5 skipped, 88 subtests passed.
Final full suite: **1,971 passed, 5 skipped, 20 warnings, 88 subtests passed**
(`.venv\Scripts\python.exe -m pytest -q`, 98.91 seconds).
Focused final runtime/learning/metrics/closed-loop verification: 65 passed.
The four requested individual test commands also passed: MT5 service (5),
closed-trade pipeline (4), Forex closed loop (4), and Forex routes (85).
Ruff and `git diff --check` passed. Three existing integration fixtures were
corrected to place their candles inside the recorded trade holding interval;
their outcome assertions were retained.
The runtime test module exercises offline startup, shared instances, connect and
disconnect, reconnect, stalled shutdown, the automatic approved-proposal-to-lesson
flow, idempotent deal replay, restart recovery, failed history/reflection/persistence,
retry API behavior and absence of broker execution calls.

Repository search and runtime AST inspection find no `order_send` call. All broker
interaction remains read-only. Tests use mocked MT5; live terminal/broker access
and live M1 availability have not been verified. Native calls cannot be forcibly
cancelled, so a permanently stalled native call may retain deferred resources until
process exit. Netting reversal deals require manual reconciliation. Discovery is
based on observed open positions; trades opened and closed entirely between polls
are not backfilled by this phase.
