# Pair observer fixtures

These fixtures are synthetic, deterministic market scenarios created for the
Headless Pair Observer. They contain no broker, customer, or production data.

Each JSON fixture must declare:

- `fixture_schema_version`;
- `provenance`;
- `as_of_utc`;
- expected behavior at that exact cutoff.

Candles after `as_of_utc` are forbidden. Later implementation phases extend
this directory with continuation, breakout-retest, invalidation, stale-data,
session, spread, exposure, and news-state scenarios.
