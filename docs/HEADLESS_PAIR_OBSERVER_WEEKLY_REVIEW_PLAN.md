# Headless Pair Observer — Previous-Week Review Plan

Status: **IMPLEMENTED AND VERIFIED — 2026-10-18**

## Purpose

Make closed-market time useful without presenting historical data as live. For
the selected Observer pair, one button reviews the most recently completed
Forex week, stores a compact context record, and makes that record available to
the next live Observer session.

## User flow

1. While Forex is closed, the command deck shows **Review previous week**.
2. The user selects EUR/USD, GBP/USD, or USD/JPY and starts the review.
3. The button remains busy while completed MT5 candles are read.
4. The page displays the reviewed period, final top-down direction, number of
   carried-forward levels, and storage time.
5. Repeating the same pair/week review replaces the same record; it never
   creates duplicates.
6. When live watching begins after reopening, the Observer automatically loads
   the latest eligible review for that pair and marks the context as loaded.

## Stored contract

Each record contains:

- deterministic identity from pair and week start;
- exact UTC week boundaries and creation time;
- final D1/H4/H1/M15/M5 structure;
- final top-down direction;
- bounded carried-forward support/resistance levels;
- source candle counts and data warnings;
- integrity SHA-256 over the canonical payload;
- explicit `WEEKLY_REVIEW` origin so it cannot be confused with live alerts.

The dedicated Observer SQLite database owns these records. The Forex journal,
analysis history, and execution systems are not modified.

## Analysis rules

- Only completed MT5 candles at or before the weekly cutoff are used.
- Structure uses confirmed pivots, so later candles cannot rewrite the record.
- The previous week high/low and confirmed D1/H4/H1 swing zones are eligible
  for carry-forward.
- Reviews never send notifications, create live alerts, call an LLM, or place
  broker orders.
- A stored weekly direction is context, not a permanent bias. Current live
  structure and invalidation rules remain authoritative.

## Live integration

At each live cycle the Observer loads the newest review whose week ended before
the cycle cutoff. Its active carried levels are merged by stable level ID before
normal live level advancement. Live levels win on identity collision. Status
reports whether weekly context is loaded and identifies the review.

## API and UI

- `POST /api/observer/weekly-review` — closed-market, stopped-observer mutation.
- `GET /api/observer/weekly-review/latest` — latest stored review for the
  selected pair, or `null`.
- The review button is unavailable while the market is open or the Observer is
  running.
- Loading, success, unavailable-data, and failure messages remain in the page;
  no browser-native dialogs are used.

## Verification gates

- deterministic week boundary and record identity tests;
- no future/incomplete candle use;
- SQLite migration, integrity, idempotent upsert, and restart tests;
- live-cycle merge and live-overrides-review tests;
- authenticated API open/closed/running-state tests;
- desktop/mobile browser checks for busy, success, failure, and stored summary;
- focused Observer suite, Ruff, JavaScript syntax, and diff checks.

## Verification result

- The focused Observer suite passes with 34 tests, including deterministic
  construction, idempotent storage, integrity validation, and automatic live
  context loading.
- Ruff, JavaScript syntax validation, and Git whitespace checks pass.
- The running application connected to MT5 while Forex was closed, reviewed
  EUR/USD for October 12–16, stored a neutral context with 26 carried levels,
  and returned the same record through the latest-review endpoint.
- Browser verification confirmed the closed-market control state: Start and
  Stop are hidden, Review previous week is available, success is announced,
  no error is shown, and the stored summary appears on the page.

