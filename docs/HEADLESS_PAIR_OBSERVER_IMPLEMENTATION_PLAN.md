# Headless Pair Observer — implementation plan

Status: **IMPLEMENTED AND LIVE-CYCLE VERIFIED — EXTENDED SOAK PENDING**
Prepared: 2026-10-16
Companion design: `docs/HEADLESS_PAIR_OBSERVER_DESIGN.md`

## 1. Objective

Implement the Headless Pair Observer as an isolated vertical feature inside the
TradingAgents repository and authenticated web server. The existing dashboard
receives one **Market Observer** button that opens `/observer` in a separate
browser tab. The observer page, API, runtime, settings, database, logs and tests
remain separate from existing analysis and journal workflows.

The architecture must make a future user-controlled connection to the existing
LLM analysis workflow straightforward without requiring either feature to read
or write the other's database.

This plan does not authorize implementation by itself. Each phase begins only
after the current dirty worktree is reviewed and the previous phase gate passes.

## 2. Locked product decisions

| Area | Decision |
|---|---|
| Location | Same TradingAgents repository and web server |
| Entry point | Dashboard button opening `/observer` in a new tab |
| Frontend | Standalone observer HTML/CSS/JavaScript; no chart required |
| Pair scope | One independently configured pair |
| Market hierarchy | D1/H4 bias, H1 location, M15 setup, M5 confirmation |
| Setups | Trend continuation and breakout-retest |
| Alerts | Developing plus completed-M5 technically-ready alerts |
| Sensitivity | Balanced |
| Sessions | London and New York |
| News policy | Technical alert permitted but marked `NEWS_UNVERIFIED` |
| Notifications | Persistent internal alert plus best-effort Windows notification |
| MT5 | Shared existing read-only connection and serialized observer API |
| LLM | Zero calls during monitoring |
| Execution | Manual-only; zero MT5 mutation calls |
| Persistence | Dedicated `pair_observer/observer.db` |
| Settings | Dedicated `pair_observer/settings.json` |
| Default state | Disabled until explicitly started/enabled |
| Future integration | Versioned immutable snapshot and explicit user action |

## 3. Target architecture

```text
TradingAgents FastAPI process
│
├── Existing dashboard (/)
│   ├── existing static assets
│   ├── existing Forex/equity APIs
│   ├── existing analysis runtime
│   └── existing forex_journal.db
│
├── Market Observer (/observer)
│   ├── independent static assets
│   ├── /api/observer/* router
│   ├── HeadlessPairObserverRuntime
│   ├── pair_observer/settings.json
│   ├── pair_observer/observer.db
│   └── pair_observer/logs/observer.log
│
└── Narrow shared services
    ├── existing authentication/session verification
    ├── ForexRuntime-owned MT5Observer
    ├── symbol normalization and typed candle contracts
    └── application startup/shutdown lifecycle
```

### 3.1 Dependency rule

Observer modules may depend on stable shared read-only contracts. Existing
analysis, journal, reconciliation and UI modules must not depend on observer
modules. Disabling observer assembly must restore the existing dependency graph
without migrating or rewriting existing data.

### 3.2 Same-process limitation

The observer shares the FastAPI process and MT5 connection, so absolute physical
isolation is impossible. Impact is bounded through a disabled-by-default worker,
separate resources, serialized MT5 calls, bounded polling, independent error
handling and full regression gates. The runtime interface must permit a later
move to a sidecar process without changing the page or public API.

## 4. Planned files and responsibilities

Proposed paths must be reconfirmed against the current checkout before creation.

```text
tradingagents/pair_observer/
├── __init__.py
├── models.py          immutable contracts, enums and reason codes
├── config.py          observer-only settings validation/persistence
├── structure.py       pivots and multi-timeframe structure
├── levels.py          automatic level/zone lifecycle
├── confirmations.py   deterministic completed-M5 confirmation
├── setups.py          setup state machines
├── eligibility.py     session/news/spread/freshness/exposure gates
├── evidence.py        snapshot envelopes and integrity hashes
├── repository.py      transaction and query boundary
├── migrations.py      observer DB schema only
├── alerts.py          formatting, fingerprints and delivery contracts
├── service.py         candle scheduling, watermarks and recovery
└── runtime.py         start/stop/failure containment

tradingagents/notifications/
└── windows.py         optional Windows notification adapter

web/
├── observer_routes.py
├── observer_page.py
└── static/observer/
    ├── index.html
    ├── observer.js
    └── observer.css

tests/
├── pair_observer/
│   ├── fixtures/
│   ├── test_structure.py
│   ├── test_levels.py
│   ├── test_confirmations.py
│   ├── test_setups.py
│   ├── test_eligibility.py
│   ├── test_repository.py
│   ├── test_service.py
│   └── test_runtime.py
├── test_observer_routes.py
└── test_observer_browser_e2e.py
```

### 4.1 Minimal existing-system changes

| Existing area | Permitted change |
|---|---|
| Dashboard navigation | Add one link to `/observer` with `target="_blank"` |
| Web assembly | Register the independent observer page and API router |
| Application lifespan | Create/close observer runtime through one contained hook |
| Packaging | Add an optional Windows-notification dependency if selected |
| CI | Include observer tests without removing or weakening existing gates |

Observer strategy logic must not enter `web/forex_routes.py`, `web/server.py`,
the existing dashboard JavaScript, `MT5ObservationService`, Forex graph classes
or LLM clients.

## 5. Core implementation interfaces

### 5.1 Read-only market source

```python
class PairObserverMarketSource(Protocol):
    def connection_status(self) -> str: ...
    def resolve_symbol(self, pair: str) -> BrokerSymbol: ...
    def current_tick(self, pair: str) -> MT5Tick: ...
    def completed_candles(
        self, pair: str, timeframe: Timeframe, count: int, as_of_utc: datetime
    ) -> Sequence[ForexBar]: ...
    def account_exposure(self) -> ExposureSnapshot: ...
```

The concrete MT5 adapter delegates only to existing read-only methods. Tests use
an in-memory adapter. No protocol method may express order submission,
modification or closure.

### 5.2 Clock and scheduler

All engines receive an injectable UTC clock. The scheduler identifies completed
candles from broker timestamps and persisted watermarks rather than assuming a
poll occurred exactly on a candle boundary.

### 5.3 Repository

```python
class PairObserverRepository(Protocol):
    def load_runtime_state(self, pair: str) -> RestoredObserverState: ...
    def commit_candle_transition(self, transition: CandleTransition) -> None: ...
    def list_alerts(self, query: AlertQuery) -> Page[ObserverAlert]: ...
    def acknowledge_alert(self, alert_id: str, at_utc: datetime) -> None: ...
```

`commit_candle_transition` atomically stores level changes, setup transitions,
alerts and the processed-candle watermark.

### 5.4 Notification delivery

```python
class AlertSink(Protocol):
    def deliver(self, alert: ObserverAlert) -> DeliveryResult: ...
```

Database persistence occurs before delivery and is not an alert sink. Windows
delivery is best-effort and independently replaceable.

## 6. Observer page

The page is operational rather than chart-oriented and remains usable while the
main dashboard is open in another tab.

### 6.1 Sections

1. Runtime control: start, stop, state and sanitized error.
2. MT5 health: connection, symbol, quote freshness and latest cycle.
3. Profile: pair, timeframes, sessions, setup types and news policy.
4. Multi-timeframe state: D1/H4/H1/M15/M5 classification and cutoff.
5. Active levels: ranked zones and lifecycle state.
6. Current setup: direction, type, state, missing confirmation and blocks.
7. Alerts: developing/ready history, acknowledgement and evidence details.
8. Observer settings: independent validated settings and reset.

### 6.2 Browser behavior

- Use `/api/observer/*` only.
- Poll one lightweight status endpoint and load history on demand.
- Coalesce refreshes and pause when the tab is hidden.
- Never start monitoring merely because the page opened.
- Never retrieve raw candle history directly from the browser.
- Never expose secrets, raw DB records or account identity.
- Display `NO ORDER EXECUTION` and `NO AUTOMATIC AI` persistently.
- Keep future “Analyze snapshot” absent or disabled until its bridge is approved.

## 7. API plan

All endpoints reuse existing authentication and live in the observer router.

| Method and path | Responsibility |
|---|---|
| `GET /observer` | Standalone observer page |
| `GET /api/observer/status` | Runtime/MT5 health and current summary |
| `POST /api/observer/start` | Explicit idempotent start |
| `POST /api/observer/stop` | Bounded idempotent stop |
| `GET /api/observer/structure` | Versioned timeframe structure |
| `GET /api/observer/levels` | Paginated/filterable levels |
| `GET /api/observer/setups` | Paginated/filterable setup history |
| `GET /api/observer/setups/{id}` | Setup transitions/evidence |
| `GET /api/observer/alerts` | Paginated alerts |
| `GET /api/observer/alerts/{id}` | Immutable alert evidence metadata |
| `POST /api/observer/alerts/{id}/acknowledge` | User acknowledgement only |
| `GET /api/observer/settings` | Observer-only settings |
| `PATCH /api/observer/settings` | Atomic partial update |
| `POST /api/observer/settings/reset` | Explicit observer-only reset |

Start returns a clear conflict/unavailable response when MT5 or configuration is
unavailable. Repeated start/stop requests are safe. Pair/profile changes require
a stopped service or an explicit stop, supersede and rebuild operation.

## 8. Storage plan

```text
~/.tradingagents/pair_observer/
├── observer.db
├── settings.json
├── backups/
└── logs/
    └── observer.log
```

The observer database uses its own schema version, foreign keys, WAL mode and
backup/restore path. No migration opens or modifies `forex_journal.db`.

Initial tables:

- `market_observer_watermarks`
- `market_levels`
- `market_setups`
- `market_setup_events`
- `market_alerts`
- `observer_schema_version`

Indexes cover pair/state/time queries, setup transitions, alert time and unique
fingerprints. Retention cannot delete evidence referenced by an alert.

## 9. Future analysis connection

Future integration must use typed, versioned application contracts—not direct
database access.

### 9.1 Immutable snapshot envelope

```text
ObserverSnapshotEnvelope v1
├── schema_version
├── snapshot_id
├── observer_alert_id
├── setup_id
├── pair
├── direction
├── setup_type
├── as_of_utc
├── created_at_utc
├── configuration_fingerprint
├── software_revision
├── market_manifest
│   ├── timeframe candle cutoffs and identities
│   ├── canonical/broker symbol
│   └── source/retrieval metadata
├── structure_evidence
├── level_evidence
├── confirmation_evidence
├── quote_and_spread
├── session_state
├── exposure_summary
├── news_status
├── eligibility_results
└── integrity_sha256
```

The envelope excludes prompts, LLM output, credentials and private account
identity. Its hash covers canonical serialized evidence.

### 9.2 Snapshot provider reserved now

```python
class ObserverSnapshotProvider(Protocol):
    def get_snapshot(self, snapshot_id: str) -> ObserverSnapshotEnvelope: ...
```

The first release creates and retrieves snapshots for evidence details but does
not connect them to analysis.

### 9.3 Future user-controlled bridge

A later approved phase may add:

```text
POST /api/observer/snapshots/{snapshot_id}/analyze
```

The integration layer will:

1. require existing analysis authorization;
2. require an explicit human request;
3. retrieve and verify the immutable snapshot;
4. create a normal validated Forex analysis request;
5. attach `observer_snapshot_id` and integrity hash;
6. use the existing bounded analysis admission controller;
7. record dashboard and canonical research run IDs in a linkage record;
8. leave the observer alert/snapshot immutable.

The observer must never import graph or LLM clients. The bridge depends on the
public observer provider and public analysis starter.

### 9.4 Future linkage contract

```text
ObserverAnalysisLink
├── link_id
├── snapshot_id
├── observer_alert_id
├── dashboard_run_id
├── canonical_research_run_id
├── requested_at_utc
├── requested_by
└── status
```

The link does not copy or mutate either system's core records.

### 9.5 Compatibility rules

- Snapshot schemas are append-only and versioned.
- Unknown required versions fail explicitly.
- Missing historical fields remain unavailable; no defaults are fabricated.
- Analysis retains snapshot ID, hash and exact cutoff.
- Reanalysis creates a new run linked to the same immutable snapshot.
- Settings changes cannot alter an existing snapshot.

## 10. Implementation phases

### Phase 0 — Protect and map the checkout

Tasks:

- Reinspect branch, SHA, status and existing diffs.
- Identify overlap with current uncommitted dashboard/server work.
- Freeze `HPO-001` through `HPO-018` in a traceability matrix.
- Confirm MT5 serialization, lifespan and authentication integration points.
- Establish targeted existing-system baseline tests.

Gate: no feature code until overlaps and baseline results are recorded.

### Phase 1 — Contracts and fixtures

Tasks:

- Implement immutable models, enums, reason codes and schema versions.
- Define market-source, repository, clock, alert and snapshot interfaces.
- Create provenance-labelled fixtures for every planned scenario.
- Test serialization, validation and directional symmetry.

Gate: contracts contain no FastAPI, SQLite, Windows, LLM or concrete MT5 imports;
fixture cutoffs prove no future leakage.

### Phase 2 — Structure and levels

Tasks:

- Implement confirmed pivots, equality clustering and structure states.
- Implement ATR-normalized moving-average support evidence.
- Implement level construction, ranking and lifecycle.
- Test incremental versus historical replay and long/short symmetry.

Gate: identical inputs produce identical normalized evidence, and no pivot or
structure state repaints at an earlier cutoff.

### Phase 3 — Setups and eligibility

Tasks:

- Implement continuation and breakout-retest state machines.
- Implement accepted completed-M5 confirmations.
- Implement freshness, spread, exposure, session and news gates.
- Implement stable setup/alert fingerprints.

Gate: all positive and negative fixtures pass; no technically-ready state exists
without completed M5 evidence.

### Phase 4 — Dedicated persistence

Tasks:

- Implement isolated DB path, migration runner and repository.
- Add atomic candle-transition commits and unique fingerprints.
- Implement backup, restore, integrity and retention boundaries.
- Implement snapshot envelope and integrity hash.

Gate: migration, rollback, restart and deduplication tests pass; no test opens or
alters `forex_journal.db`.

### Phase 5 — Runtime and MT5 adapter

Tasks:

- Adapt existing MT5Observer to the read-only market-source protocol.
- Implement bounded scheduling, watermarks, gap recovery and runtime states.
- Add backoff, circuit breaking and resource bounds.
- Share ForexRuntime's connection without creating another connection.

Gate: call traces contain no MT5 mutation, LLM client or graph execution;
existing MT5 reconciliation tests remain green.

### Phase 6 — Alerts and Windows delivery

Tasks:

- Generate developing/ready messages from structured evidence.
- Implement acknowledgement and delivery states.
- Add optional Windows adapter and internal fallback.
- Retry committed-but-undelivered notification at most once after restart.

Gate: delivery occurs only after commit, and delivery failure cannot fail a
cycle or duplicate an alert.

### Phase 7 — Isolated API

Tasks:

- Implement authenticated `/api/observer/*` router.
- Add idempotent lifecycle and paginated evidence endpoints.
- Add atomic observer-only settings routes.
- Test auth, invalid states, contracts and concurrency.

Gate: existing Forex routes/schemas remain unchanged unless a shared bug fix is
separately justified; disabling the observer router cannot break startup.

### Phase 8 — Separate page and dashboard button

Tasks:

- Implement standalone observer assets.
- Implement status, structure, levels, setup, alerts and settings views.
- Add one dashboard link opening `/observer` in a new tab.
- Add accessibility and unavailable/empty/failure states.
- Add observer browser tests.

Gate: existing dashboard E2E remains green, and observer assets do not import
the existing dashboard application JavaScript.

### Phase 9 — Assembly and recovery

Tasks:

- Register page, router and runtime through contained assembly functions.
- Integrate bounded shutdown.
- Verify stopped mode creates no worker or MT5 calls.
- Test restart, reconnect, settings changes and long gaps.

Gate: observer failure does not fail existing health components unless the
shared MT5 dependency itself is unavailable; analysis and reconciliation remain
usable during observer degradation.

### Phase 10 — Full verification and rollout

Tasks:

- Run targeted suites after every phase.
- Run full non-E2E, Ruff, diff, dependency and SQLite checks.
- Run existing and observer browser E2E.
- Check observer DB and Forex journal integrity separately.
- Run deterministic replay and accelerated stability tests.
- Run one revision-locked Windows/MT5 read-only soak.

Gate: every `HPO-*` requirement has evidence, zero-LLM and zero-write traces are
recorded, existing regression gates pass, and the feature remains disabled until
explicitly enabled.

## 11. Impact-control matrix

| Risk | Control | Required evidence |
|---|---|---|
| Existing dashboard regression | Standalone observer assets | Existing + observer E2E |
| Forex router coupling | Separate router/models | Dependency review |
| Journal corruption | Dedicated observer DB | Path assertion + integrity tests |
| MT5 contention | Serialized bounded calls | Concurrency tests + soak |
| Duplicate worker | Locked idempotent lifecycle | Simultaneous start/stop tests |
| Duplicate alerts | Unique fingerprint + atomic watermark | Restart/replay tests |
| LLM cost | No graph/client dependency | Import and call trace |
| Broker mutation | Read-only protocol | MT5 method trace |
| Hidden news uncertainty | Three-state news status | API/UI fixtures |
| Process instability | Bounded failure-contained worker | Stability/shutdown tests |
| Future coupling | Versioned snapshot provider | Contract round-trip tests |

## 12. Rollback

Before future integration, rollback is:

1. stop and disable the observer;
2. unregister observer router/page/runtime assembly;
3. remove the dashboard link;
4. retain the dedicated observer directory as recoverable evidence.

No rollback touches the Forex journal, existing settings or analysis history.

## 13. Required documentation during implementation

- Requirement-to-code/test matrix for `HPO-001`–`HPO-018`.
- Settings reference.
- API and snapshot-schema reference.
- Migration, backup and recovery guide.
- Windows notification setup/fallback guide.
- Operational start/stop/reconnect guide.
- Deterministic rule explanation without profitability claims.
- Verification report separating local, remote-CI and connected-MT5 evidence.
- Future snapshot-analysis integration guide.

## 14. Completion classification

The first release may be called **Locally Tested Observer** only after all local
gates pass. It may be called **Operationally Observed** only after the
revision-locked Windows/MT5 soak succeeds. Neither classification establishes
strategy profitability, XM lifecycle acceptance or production readiness.
