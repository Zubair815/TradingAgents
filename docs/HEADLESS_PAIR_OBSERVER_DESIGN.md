# Headless Pair Observer

Status: **IMPLEMENTED AND LIVE-CYCLE VERIFIED — EXTENDED SOAK PENDING**
Prepared: 2026-10-16
Target repository: `D:\TradingAgents`
Baseline revision inspected: `ca29b78d9cc8826ff4d2877ca1babba17f4a52ad`

## 1. Purpose

The Headless Pair Observer continuously studies one configured Forex pair in the
same top-down sequence used by a disciplined discretionary intraday trader. It
maintains D1, H4, H1, M15 and M5 market state internally, discovers important
levels and zones, follows trend and structure changes, detects developing
setups, and emits evidence-backed alerts.

The observer does not need a chart or a permanent dashboard view. It does not
call an LLM during monitoring, and it never places, modifies or closes broker
orders. Its output is decision-support evidence for manual review.

The selected operating profile is:

- one configured pair;
- D1/H4 directional bias;
- H1 intraday structure and location;
- M15 trend-continuation and breakout-retest setups;
- completed M5 candle confirmation;
- London and New York qualification sessions;
- early `DEVELOPING` alerts and closed-candle `TECHNICALLY_READY` alerts;
- complete automatic structure and level detection;
- balanced sensitivity;
- internal persistent alert history plus Windows notification;
- technical alerts remain possible when news coverage is unavailable, but are
  prominently marked `NEWS_UNVERIFIED`;
- no automatic LLM call and no automated broker execution.

## 2. Safety and truthfulness boundaries

These rules are architectural invariants, not configurable preferences:

1. MT5 integration remains read-only. The observer must not import or call any
   order-placement, order-modification or position-closing operation.
2. Monitoring must not instantiate an LLM client, submit an LLM request or
   consume an LLM token budget.
3. A technical setup is not a profitability claim, trading recommendation or
   broker instruction.
4. Incomplete, future-dated, stale or internally inconsistent broker data must
   fail closed. It must never be repaired with plausible invented values.
5. Only completed candles may modify structural trend, confirm a breakout or
   produce a `TECHNICALLY_READY` state. Live ticks may update proximity only.
6. Missing economic-calendar coverage must be represented as
   `NEWS_UNVERIFIED`; it must never be converted to `NO_NEWS_RISK`.
7. Account exposure and spread blocks cannot be overridden by pattern strength.
8. A generated alert must be reproducible from its stored evidence snapshot.
9. Restart, reconnect and repeated polling must not duplicate alerts or mutate
   historical evidence.
10. The optional future action “Analyze this snapshot” must require an explicit
    user request and must operate on the immutable observer snapshot.

## 3. Scope

### 3.1 In scope

- Observe the canonical `forex_default_pair` from MT5.
- Normalize the canonical pair to its broker-specific symbol.
- Maintain completed-candle windows for M5, M15, H1, H4 and D1.
- Detect non-repainting swing structure, directional bias and ranges.
- Detect and lifecycle-manage automatic levels and zones.
- Detect trend-continuation and breakout-retest setup candidates.
- Confirm candidates using completed M5 evidence.
- Enforce session, spread, freshness and exposure eligibility checks.
- Persist setup transitions, evidence snapshots and alerts.
- Produce deduplicated Windows notifications with an internal fallback.
- Restore watermarks and active setup state after restart.
- Expose status and evidence through internal application services and small API
  or CLI surfaces without requiring a chart.

### 3.2 Explicitly out of scope

- Automated order execution or trade management.
- Continuous or automatically triggered LLM analysis.
- Multi-pair scanning in the first release.
- Countertrend reversal and range-trading strategies.
- Tick-level scalping or M1 confirmation.
- A charting package or drawing interface.
- Strategy optimization against final evaluation data.
- Claims of expected return, win rate or production readiness.

## 4. Deployment and relationship to existing components

The observer remains part of TradingAgents and uses the same authenticated web
server. One new **Market Observer** button on the existing dashboard opens a
standalone page in a new browser tab:

```text
Existing dashboard: http://127.0.0.1:8050/
Observer page:      http://127.0.0.1:8050/observer
```

The page owns a separate HTML, CSS and JavaScript bundle. It must not extend the
existing dashboard application script or depend on its in-memory view state.
The observer also owns its engine, API namespace, worker lifecycle, settings,
database and logs.

The existing `MT5ObservationService` reconciles account positions, pending
orders and deals with the trade journal. That responsibility must remain
separate.

The new observer should consume the existing read-only `MT5Observer` through a
new bounded service rather than adding market-structure logic to
`tradingagents/mt5/service.py`.

Proposed dependency direction:

```text
MT5Observer
    ├── existing MT5ObservationService
    │       └── positions / orders / deals / reconciliation
    │
    └── proposed HeadlessPairObserverService
            ├── candle synchronization
            ├── market structure engine
            ├── level engine
            ├── setup engine
            ├── eligibility/risk gates
            ├── persistence
            └── alert dispatcher
```

Both services share the existing serialized `MT5Observer` and connection. They
must not create
independent MT5 connections or issue concurrent native MT5 calls outside the
observer's existing serialization boundary.

The integration surface is deliberately narrow: one dashboard link, one static
page mount, one API router registration, one lifecycle registration, existing
authentication and the read-only MT5 interface. The feature is disabled by
default and performs no polling while stopped. Observer failures must remain
contained and must not change existing analysis or reconciliation state.

## 5. Top-down market model

The observer does not literally switch charts. It maintains a synchronized
state object for each timeframe and recomputes only when that timeframe receives
a newly completed candle.

| Timeframe | Responsibility | Setup authority |
|---|---|---|
| D1 | Major direction, dominant structure, prior-day/week context and major zones | May permit, oppose or mark the market transitional |
| H4 | Active swing trend, pullback structure and high-value zones | Must not structurally contradict the intended direction |
| H1 | Intraday structure, location and active support/resistance | Defines whether current location is acceptable |
| M15 | Forms trend-continuation or breakout-retest candidate | Creates and invalidates setup candidates |
| M5 | Closed-candle trigger confirmation | May promote a candidate to technically ready |

### 5.1 Update schedule

- Ticks: update bid, ask, spread, market-data freshness and zone proximity.
- M5 close: update confirmation structure and evaluate pending candidates.
- M15 close: update setup structure and create/invalidate candidates.
- H1 close: update intraday structure, location and active zones.
- H4 close: update swing bias and zones.
- D1 close: update dominant bias and daily/weekly reference levels.

The scheduler must detect candle identity from its UTC open and close timestamps,
not from wall-clock modulo arithmetic. This accommodates downtime, delayed polls
and broker sessions.

## 6. Candle synchronization and watermarks

For every timeframe, persist the most recently processed completed-candle open
time. On each polling cycle:

1. Query MT5 using one shared `as_of_utc` cutoff.
2. Reject candles closing after the cutoff.
3. Validate ordering, uniqueness, OHLC consistency and expected timeframe.
4. Compare candle identities with the persisted watermark.
5. Process missing completed candles oldest-to-newest.
6. Commit the resulting state and watermark atomically.

A restart must replay missing completed candles without repeating already
committed setup transitions or alerts. A large gap must be bounded by a
configurable recovery limit. When the gap exceeds that limit, rebuild current
state from a bounded history window and record `STATE_REBUILT_AFTER_GAP`.

Initial minimum history proposal:

| Timeframe | Completed candles requested |
|---|---:|
| M5 | 500 |
| M15 | 500 |
| H1 | 400 |
| H4 | 300 |
| D1 | 260 |

These are engineering warm-up defaults, not claims of statistical sufficiency.

## 7. Deterministic market structure

### 7.1 Confirmed pivots

Use confirmed fractal pivots with configurable left/right strength. A pivot is
not available until its right-side confirmation candles have closed. Therefore,
historical processing and live processing produce the same pivot timing and do
not repaint past structure.

Initial balanced defaults:

- D1/H4: 3 candles left and 3 right;
- H1/M15: 2 left and 2 right;
- M5: 2 left and 2 right.

Equal highs/lows within a tolerance should form one liquidity cluster instead of
arbitrarily alternating pivots. Tolerance should be the greater of broker tick
size and a small configurable ATR fraction.

### 7.2 Structure states

Each timeframe has one of:

- `BULLISH`: confirmed higher-high/higher-low structure;
- `BEARISH`: confirmed lower-high/lower-low structure;
- `RANGING`: price remains inside confirmed opposing boundaries;
- `TRANSITIONING`: a counter-structure break occurred but reversal structure is
  not yet confirmed;
- `INSUFFICIENT_DATA`: required pivots or valid candles are unavailable.

A break of structure requires a completed candle close beyond the structural
boundary by a configurable tolerance. A wick alone cannot confirm it.

### 7.3 Moving-average support

An exponential moving-average slope may strengthen or weaken structure evidence,
but it cannot reverse the price-structure classification. The first version
should use one medium trend reference and one slow context reference, with
periods stored in configuration. Their slope must be normalized by ATR so the
same rule is meaningful across price scales.

Proposed balanced defaults are EMA 20 and EMA 50. These defaults must remain
explicit and versioned; they are not profitability-optimized constants.

### 7.4 Multi-timeframe bias

The intended direction is eligible when:

- D1 and H4 are aligned; or
- one is aligned and the other is `RANGING`/`TRANSITIONING` without a confirmed
  opposing structural break.

The direction is blocked when D1 and H4 have confirmed opposing structures.
H1 may be temporarily corrective, but the setup must occur at an eligible H1 or
higher-timeframe location.

## 8. Automatic levels and zones

### 8.1 Level sources

The first release should maintain:

- confirmed D1/H4/H1 swing highs and lows;
- previous day high and low;
- previous week high and low;
- Asian-session high and low;
- active London/New York session high and low;
- confirmed M15 breakout boundaries;
- broken structural levels awaiting retest.

### 8.2 Zone construction

A zone is a price interval, not an exact line. Its origin must retain:

- source timeframe and candle identity;
- source type;
- proximal and distal prices;
- creation cutoff;
- ATR at creation;
- structural direction;
- initial departure strength;
- touch count and last-touch time;
- state and invalidation reason.

Initial zone width should be derived from the origin candle and capped by an ATR
fraction. It must respect broker tick size and digits. Fixed universal pip widths
must not be used for every pair and timeframe.

### 8.3 Zone lifecycle

```text
DETECTED → ACTIVE → APPROACHED → TOUCHED → REACTED
                         └──────→ CONSUMED
                         └──────→ BROKEN → RETEST_ELIGIBLE
                                           └→ RETESTED
                                           └→ EXPIRED
```

A zone becomes weaker after repeated penetrations. A completed close through its
distal boundary invalidates it. Broken resistance may become retest support and
vice versa only after a confirmed breakout close.

### 8.4 Level ranking

Ranking inputs may include:

- source timeframe;
- recency;
- number and quality of reactions;
- departure distance normalized by ATR;
- alignment with D1/H4 structure;
- whether the level is fresh or already consumed;
- confluence with prior-day/week or session boundaries.

The rank is an evidence score for prioritization, not a probability of success.
Every component and its contribution must be stored with the level.

## 9. Setup engines

### 9.1 Trend-continuation setup

Long candidate requirements:

1. Multi-timeframe bias permits long exposure.
2. H1 price location is an active support/demand or valid broken-resistance
   retest area.
3. M15 is pulling back without a confirmed higher-timeframe invalidation.
4. Price enters the configured proximity boundary around the eligible zone.
5. Spread and data freshness remain valid.

Candidate creation moves the setup to `DEVELOPING`. M15 stabilization or a
corrective-structure break moves it to `AWAITING_M5_CONFIRMATION`.

Short logic is the exact directional inverse and should be implemented through
shared directional primitives rather than copied code.

### 9.2 Breakout-retest setup

Long candidate requirements:

1. Multi-timeframe bias permits long exposure.
2. M15 closes above a confirmed eligible resistance boundary by the breakout
   tolerance.
3. The breakout candle meets minimum body and normalized-range quality.
4. The system records the broken boundary and waits; the initial breakout alone
   cannot become ready.
5. Price returns to the configured retest band before expiry.
6. M15 does not close back through the invalidation boundary.
7. A completed M5 confirmation occurs at the retest.

Short logic is the inverse.

### 9.3 M5 confirmation types

At least one accepted confirmation is required:

- `STRUCTURE_BREAK`: completed M5 close beyond the last relevant corrective
  pivot;
- `BREAKOUT_RETEST_CLOSE`: completed close rejects and leaves the retest band in
  the intended direction;
- `STRONG_REJECTION`: wick tests the zone and the body closes away from it with
  minimum body/range quality;
- `ENGULFING`: real body directionally engulfs the prior real body and closes in
  an eligible location;
- `DIRECTIONAL_CLOSE`: strong normalized body closes near its intended extreme
  and away from the zone.

Pattern names alone cannot confirm a setup. Each detector must enforce minimum
body, opposing-wick, ATR-normalized movement and location rules.

## 10. Eligibility gates

Eligibility is evaluated after technical evidence but before ready alerting.

### 10.1 Required gates

- MT5 connected.
- All required candle windows available and fresh.
- Current quote valid and not future-dated.
- Spread no greater than the configured Forex maximum.
- London or New York qualification session active.
- No conflicting open position or pending exposure under the configured policy.
- Account and risk-critical broker data available.
- Setup not expired or invalidated.
- Alert fingerprint not previously emitted.

### 10.2 News state

News state is one of:

- `VERIFIED_CLEAR`;
- `VERIFIED_BLACKOUT`;
- `UNVERIFIED_COVERAGE`.

`VERIFIED_BLACKOUT` blocks readiness. `UNVERIFIED_COVERAGE` follows the selected
policy: a technically ready alert may be emitted, but its public state and
notification must say `NEWS_UNVERIFIED`. It must not be described simply as
`READY` or `CLEAR`.

### 10.3 Session handling

Session conversion must use IANA zones (`Europe/London` and
`America/New_York`) so daylight-saving transitions are handled correctly. UTC
is authoritative in storage.

The initial qualifying windows should be configuration values rather than
embedded strategy constants. Overlapping London/New York time is one session
eligibility condition, not two separate alerts.

## 11. Setup state machine

Canonical states:

```text
SCANNING
  └── DEVELOPING
        └── AWAITING_M5_CONFIRMATION
              └── TECHNICALLY_READY
                    └── ALERTED

Any active state may transition to:
  BLOCKED_SPREAD
  BLOCKED_EXPOSURE
  OUTSIDE_SESSION
  VERIFIED_NEWS_BLACKOUT
  NEWS_UNVERIFIED
  INVALIDATED
  EXPIRED
  DATA_UNAVAILABLE
  SUPERSEDED
```

`NEWS_UNVERIFIED` is an eligibility modifier as well as a persisted public
label. Internally, the technical state should remain available so restored news
coverage can re-evaluate it without reconstructing the setup.

Every transition must include:

- previous and next state;
- UTC timestamp and triggering candle identity;
- deterministic reason code;
- human-readable explanation derived from structured evidence;
- evidence version;
- software revision when available.

Illegal backward transitions must be rejected. A new setup after invalidation
receives a new ID rather than reopening old history.

## 12. Alert behavior

### 12.1 Developing alert

Emit once when price enters the proximity region of an eligible active zone and
the higher-timeframe bias permits the setup.

Example:

```text
EURUSD long setup developing
H4 bullish; price is approaching H1 support 1.0840–1.0850.
Waiting for M15 stabilization and completed M5 confirmation.
```

### 12.2 Technically ready alert

Emit only after a completed M5 confirmation and all non-news required gates.

Example with missing news coverage:

```text
EURUSD technically ready — LONG — NEWS UNVERIFIED
M15 breakout-retest confirmed; M5 bullish structure break.
Manual review required. No order has been sent.
```

### 12.3 Deduplication

The alert fingerprint should be derived from:

- pair;
- direction;
- setup type;
- source zone/breakout identity;
- M15 setup candle;
- M5 confirmation candle;
- evidence schema version.

The same fingerprint is persisted with a unique database constraint. Repeated
polls, reconnects and restarts therefore cannot emit the same alert twice.

Re-alerting is allowed only for a new setup ID caused by new structure, a new
zone, a materially new M15 setup candle or a valid post-cooldown reconstruction.

### 12.4 Notification delivery

Use an alert dispatcher interface:

```text
AlertDispatcher
    ├── PersistentAlertSink (mandatory)
    └── WindowsNotificationSink (best effort)
```

Persistence is authoritative. Windows notification failure must not roll back
the alert or retry endlessly. The notification adapter should expose
`DELIVERED`, `UNAVAILABLE` or `FAILED`, with sanitized failure detail.

Implementation recommendation: place Windows notification support behind an
optional dependency/adapter. If unavailable, retain the alert internally and
log one bounded diagnostic rather than silently losing it.

## 13. Persistence design

Use a dedicated SQLite database at
`~/.tradingagents/pair_observer/observer.db`. Do not add observer tables to
`forex_journal.db` and do not overload research runs, proposals or trade events
with observer-only semantics. Observer migrations, backup, restore and
integrity checks remain independent from the existing journal.

### 13.1 Proposed tables

#### `market_observer_watermarks`

- `pair`
- `timeframe`
- `last_processed_open_utc`
- `last_processed_close_utc`
- `state_version`
- `updated_at_utc`
- primary key: `(pair, timeframe)`

#### `market_levels`

- `level_id`
- `pair`
- `source_timeframe`
- `level_type`
- `direction`
- `proximal_price`
- `distal_price`
- `origin_open_utc`
- `detected_at_utc`
- `state`
- `touch_count`
- `last_touch_utc`
- `invalidated_at_utc`
- `invalidation_reason`
- `strength_components_json`
- `evidence_version`
- `software_revision`

#### `market_setups`

- `setup_id`
- `pair`
- `direction`
- `setup_type`
- `state`
- `technical_state`
- `news_status`
- `source_level_id`
- `created_at_utc`
- `updated_at_utc`
- `expires_at_utc`
- `m15_setup_open_utc`
- `m5_confirmation_open_utc`
- `confirmation_type`
- `invalidation_reason`
- `configuration_json`
- `current_evidence_json`
- `evidence_version`
- `software_revision`

#### `market_setup_events`

- `event_id`
- `setup_id`
- `from_state`
- `to_state`
- `reason_code`
- `description`
- `trigger_timeframe`
- `trigger_candle_open_utc`
- `timestamp_utc`
- `evidence_json`
- unique deterministic event identity

#### `market_alerts`

- `alert_id`
- `setup_id`
- `alert_type`
- `fingerprint`
- `created_at_utc`
- `headline`
- `message`
- `news_status`
- `evidence_snapshot_json`
- `notification_status`
- `notification_error_code`
- unique constraint on `fingerprint`

### 13.2 Transaction boundaries

A candle-driven state update must atomically persist:

1. level changes;
2. setup changes and transition events;
3. any alert record;
4. the processed-candle watermark.

Windows notification occurs only after commit. If the process stops after commit
but before notification, startup may retry undelivered notification once without
creating another alert.

### 13.3 Evidence snapshot

Each ready alert stores the exact decision inputs:

- canonical and broker symbols;
- shared `as_of_utc` cutoff;
- contributing completed-candle identities;
- timeframe structure states and decisive pivots;
- active levels/zones and strength components;
- setup and confirmation evidence;
- bid, ask, spread and quote time;
- ATR/volatility values;
- session identity;
- account/open-position/pending-order exposure summary;
- news state and coverage cutoff;
- relevant configuration;
- software revision and evidence schema version.

Raw credentials, account login and unnecessary personal information must never
enter the snapshot.

## 14. Proposed configuration

All new settings require validation, atomic persistence and reset support.

```text
pair_observer_enabled: false
pair_observer_pair: copied from forex_default_pair on first initialization
pair_observer_profile: intraday_balanced_v1
pair_observer_poll_seconds: inherit mt5_poll_interval_seconds
pair_observer_timeframes: [M5, M15, H1, H4, D1]
pair_observer_setup_types: [trend_continuation, breakout_retest]
pair_observer_sessions: [london, new_york]
pair_observer_developing_alerts: true
pair_observer_ready_alerts: true
pair_observer_news_policy: technical_alert_news_unverified
pair_observer_windows_notifications: true
pair_observer_max_recovery_candles: bounded integer
pair_observer_setup_expiry_m15_bars: bounded integer
pair_observer_realert_cooldown_minutes: bounded integer
pair_observer_zone_proximity_atr: bounded decimal
pair_observer_breakout_tolerance_atr: bounded decimal
pair_observer_retest_tolerance_atr: bounded decimal
pair_observer_ema_fast: 20
pair_observer_ema_slow: 50
```

Observer settings are persisted separately at
`~/.tradingagents/pair_observer/settings.json`. The existing Forex default pair
initializes an absent observer setting only; later Forex-setting changes do not
silently switch an active observer.

`pair_observer_enabled` defaults to `false` for a safe rollout. Enabling it must
not implicitly connect MT5, enable LLM calls or enable execution.

Changing the configured pair, timeframe profile or structural parameters must
supersede active setups and rebuild state under a new configuration fingerprint.
Historical alerts remain immutable under their original configuration.

## 15. Proposed service boundaries

No files in this section exist merely because they are named here; they are the
recommended implementation boundaries.

```text
tradingagents/pair_observer/
    models.py          immutable contracts and enums
    structure.py       pivots, structure and normalized trend evidence
    levels.py          automatic level/zone detection and lifecycle
    confirmations.py   deterministic M5 confirmation detectors
    setups.py          continuation and breakout-retest state machines
    eligibility.py     session, spread, freshness, exposure and news gates
    repository.py      SQLite persistence and atomic transitions
    alerts.py          formatting, deduplication and dispatcher contracts
    service.py         scheduling, candle watermarks and orchestration

tradingagents/notifications/
    windows.py         optional Windows notification adapter

web/
    observer_routes.py        isolated status/settings/evidence API router
    observer_page.py          standalone page/static mount
    static/observer/
        index.html
        observer.js
        observer.css
```

The core engines must accept typed inputs and have no dependency on FastAPI,
global web dictionaries, Windows APIs or LLM clients.

## 16. Proposed external contracts

No chart UI is required. Minimal authenticated endpoints are sufficient:

- `GET /api/observer/status`
- `POST /api/observer/start`
- `POST /api/observer/stop`
- `GET /api/observer/structure`
- `GET /api/observer/levels`
- `GET /api/observer/setups`
- `GET /api/observer/setups/{setup_id}`
- `GET /api/observer/alerts`
- `POST /api/observer/alerts/{alert_id}/acknowledge`
- `GET /api/observer/settings`
- `PATCH /api/observer/settings`
- `POST /api/observer/settings/reset`

Status should expose service health, pair, connection, watermarks, latest valid
quote, current structure summary, active setup count, last successful cycle and
sanitized last error. It must not expose secrets or raw account identity.

Optional CLI operations:

- `tradingagents forex observer status`
- `tradingagents forex observer alerts --limit 20`
- `tradingagents forex observer start`
- `tradingagents forex observer stop`

## 17. Failure and recovery behavior

| Condition | Required behavior |
|---|---|
| MT5 disconnected | Service remains idle/degraded; no synthetic candles or alerts |
| Stale/future quote | Mark data unavailable and block readiness |
| Missing timeframe candles | Preserve last committed state, record degraded reason, emit no new setup |
| Spread excessive | Preserve technical candidate as blocked; do not emit ready alert |
| Calendar unavailable | Apply `NEWS_UNVERIFIED` policy visibly |
| Calendar confirms blackout | Block ready alert until blackout clears or setup expires |
| Windows notification fails | Keep persisted alert; record bounded delivery failure |
| Process restart | Restore configuration, levels, active setups and watermarks; replay idempotently |
| Long downtime | Rebuild bounded state and supersede stale candidates |
| Pair configuration changes | Supersede active setups and rebuild under new fingerprint |
| Database write fails | Do not advance watermark or send notification |
| Unexpected engine error | Preserve previous committed state and expose sanitized error/health status |

## 18. Security and resource limits

- Reuse existing Forex authentication for all observer endpoints.
- Validate pair, timeframe and all numeric configuration bounds.
- Serialize service start/stop and prevent duplicate workers.
- Bound in-memory candles, active zones, active setups and event buffers.
- Rate-limit or coalesce manual start/stop and refresh operations.
- Avoid logging full evidence snapshots at info/error level.
- Never log credentials, account numbers or unmasked broker identity.
- Use structured reason codes rather than raw native MT5 exception text in
  public responses.
- Keep notification text free of sensitive account data.

## 19. Deterministic testing strategy

### 19.1 Unit tests

- Pivot confirmation timing and no-repaint behavior.
- Equal-high/low clustering and tick/ATR tolerances.
- Bullish, bearish, ranging, transitioning and insufficient-data structure.
- EMA slope as supporting—not overriding—evidence.
- Zone construction, touch, reaction, consumption, break, retest and expiry.
- Long/short symmetry for both setup types.
- Every accepted M5 confirmation and every rejection rule.
- Session/DST boundaries for London and New York.
- News clear, blackout and unavailable coverage states.
- Spread, quote freshness and exposure gates.
- Fingerprint stability and deduplication.
- State-machine legal and illegal transitions.

### 19.2 Integration tests

- Multiple timeframe updates sharing one cutoff.
- Missing candles and recovery replay.
- Restart from persisted watermarks with no duplicate alerts.
- MT5 reconnect and delayed candle arrival.
- Database rollback leaves watermark unchanged.
- Alert persistence succeeds when Windows notification fails.
- Pair/settings change supersedes active state.
- Existing position/order/deal observer continues unchanged.
- No LLM client factory is invoked during all observer workflows.
- No MT5 write method exists in the observer call trace.

### 19.3 Scenario fixtures

Create fixed completed-candle fixtures for:

1. bullish continuation that confirms;
2. bullish continuation invalidated before confirmation;
3. bearish continuation;
4. true breakout, valid retest and M5 confirmation;
5. wick-only false breakout;
6. breakout that never retests and expires;
7. ranging D1/H4 market;
8. conflicting D1/H4 structure;
9. excessive spread at confirmation;
10. stale quote;
11. news blackout;
12. news coverage unavailable with technical alert;
13. restart immediately before alert commit;
14. duplicate polling of the same candles;
15. DST session transition.

### 19.4 Property and invariant tests

- Reprocessing identical input produces identical state and no extra records.
- Reversing directional fixtures produces symmetric classifications.
- No future candle can influence an earlier state.
- No ready alert exists without a completed M5 confirmation.
- No alert exists without a persisted evidence snapshot.
- No Windows notification precedes database commit.
- No monitoring path imports or invokes an LLM client.
- No monitoring path calls an MT5 mutation method.

### 19.5 System verification

- Targeted suites per phase.
- Full non-E2E regression.
- Ruff and repository diff checks.
- SQLite integrity and foreign-key checks.
- Browser/API contract tests even though no chart is added.
- Short CI stability run with accelerated fixtures.
- Controlled Windows/MT5 soak recording cycle duration, failures, memory,
  duplicate-alert count and reconnect recovery.

## 20. Observability

Service health should include:

- running/stopped/degraded state;
- configured canonical and broker symbol;
- last cycle start/end and duration;
- last successful tick and candle sync;
- per-timeframe watermarks;
- active level/setup counts;
- last transition/alert time;
- notification availability;
- sanitized last error code;
- bounded consecutive-failure count.

Recommended metrics:

- polling cycles and failures;
- MT5 call duration;
- new candles processed per timeframe;
- state rebuilds;
- setup transitions by reason;
- alerts created, deduplicated and delivery outcomes;
- stale-data and spread blocks;
- news-unverified and verified-blackout counts.

Operational logs and test logs must remain separated.

## 21. Implementation plan and gates

The authoritative phased delivery plan is
`docs/HEADLESS_PAIR_OBSERVER_IMPLEMENTATION_PLAN.md`. Its phases cover checkout
protection, contracts, deterministic engines, dedicated persistence, the
isolated runtime, notifications, the `/api/observer` router, the standalone
page, recovery, and full controlled-rollout verification.

Implementation must follow that plan in order. Every phase has an explicit
entry condition, deliverables, verification gate and stopping point. In
particular, the observer receives its own lifecycle registration; it must not
be added to `ForexRuntime` or allowed to change the existing reconciliation
lifecycle.

## 22. Requirements traceability

| ID | Requirement |
|---|---|
| `HPO-001` | Observe exactly one validated configured pair in release one. |
| `HPO-002` | Maintain D1/H4/H1/M15/M5 state using completed candles. |
| `HPO-003` | Use D1/H4 bias, H1 location, M15 setup and M5 confirmation. |
| `HPO-004` | Support continuation and breakout-retest setups. |
| `HPO-005` | Emit developing and technically-ready alerts with deduplication. |
| `HPO-006` | Restrict qualification to London/New York configured sessions. |
| `HPO-007` | Represent unavailable calendar coverage as `NEWS_UNVERIFIED`. |
| `HPO-008` | Persist immutable evidence and exact configuration for every alert. |
| `HPO-009` | Restore/replay without duplicate setup events or alerts. |
| `HPO-010` | Monitoring must make zero LLM calls. |
| `HPO-011` | Monitoring must perform zero MT5 write operations. |
| `HPO-012` | Stale, future or incomplete market data must fail closed. |
| `HPO-013` | Windows notification is best-effort; persistence is authoritative. |
| `HPO-014` | Changing pair/profile supersedes active state without rewriting history. |
| `HPO-015` | Every structural decision must be point-in-time reproducible. |
| `HPO-016` | Service resources, recovery replay and stored active state must be bounded. |
| `HPO-017` | Existing MT5 reconciliation and manual-only execution remain unchanged. |
| `HPO-018` | Observer is disabled by default until explicitly enabled. |

## 23. Definition of done

The feature is implemented only when:

- all `HPO-*` requirements have code and test evidence;
- deterministic fixtures cover positive, negative and unavailable-data paths;
- full existing regression and applicable E2E gates pass;
- SQLite migration and restart recovery are verified;
- repeated polling/restart produces no duplicate alerts;
- call tracing proves zero LLM calls and zero MT5 write operations;
- settings and endpoints preserve existing compatibility and authentication;
- one revision-locked Windows/MT5 read-only soak completes with recorded
  evidence;
- documentation explicitly retains manual execution, news-verification and
  profitability limitations.

## 24. Readiness statement

The isolated feature described here is implemented locally. Deterministic and
full regression gates pass, but a revision-locked connected Windows/MT5 soak is
still external acceptance evidence. This document does not assert that the
setup rules are profitable, that XM demo acceptance has been completed, or that
any broker action is safe to automate.
