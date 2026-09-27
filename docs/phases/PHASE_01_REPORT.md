# Phase 1 — shared contracts and persistence

Status: **PASS**. Verified after Phase 0 on `codex/sequential-forex-phases`.

## Inspection and implementation

The existing journal already provided proposal, trade, execution, lesson and strategy-version models. These were retained. Inspection found no persisted research runs, snapshots, agent reports or run/version links, and found that migration `executescript` could commit DDL before a failure.

- Added a shared validated analysis request, versioned run/snapshot/report/broker-event envelopes, UTC validation, canonical JSON, stable identities and explicit run transitions.
- Added migration 2 for research records and provenance links in the existing journal database. Foreign keys reject unknown references and cross-run snapshot links; immutable evidence has content hashes and database mutation guards.
- Made migrations transactional, including DDL, and rechecked versions under the write transaction for concurrent startup.
- Preserved complete proposal payloads and original risk evidence without fabricating missing legacy history.
- Made the web request use the shared contract. The graph now returns the actual persisted proposal ID and propagates journal failures.

## Verification

- Targeted database, contracts, graph, routes and Phase 1 tests: **129 passed**.
- `python -m ruff check .`: **passed**.
- `python -m pytest -q`: **1,761 passed, 5 skipped, 88 subtests passed**, 20 warnings.
- New tests cover upgrade preservation, failed-DDL rollback, concurrent migrations, reopen/recovery, immutable snapshots, foreign keys, cross-run links, state transitions, request validation, broker identity and journal failure propagation.
- Reviewed the complete diff and checked whitespace before committing.

The five skips concern three POSIX permission checks on Windows, optional `langchain_aws`, and a live DeepSeek test without credentials. No application test failed.

## Boundaries

Existing source-domain candle, quote, event, proposal, risk, trade and lesson models remain canonical; the new records provide persistence envelopes rather than competing calculation models. Legacy proposals remain readable with absent provenance explicitly represented as missing. Connecting every workflow node to a persisted run and snapshot is Phase 5; broker observation is Phase 6. This report establishes storage behavior, not live provider readiness.
