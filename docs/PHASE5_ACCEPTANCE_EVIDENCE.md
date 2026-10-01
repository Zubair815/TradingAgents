# Phase 5 Windows Acceptance Evidence

Evidence date: 2026-10-01 (Asia/Karachi)

Status: **PARTIAL - XM DEMO ACCEPTANCE BLOCKED**

This record separates directly observed target-host evidence from automated tests
and from broker checks that require a connected XM demo account and manual tester
actions. It contains no account login, password, API key, or broker secret.

## Audited working state

- Base commit: `87e23b7695886ca44b2f7015e71901c10cd4452b`
- Branch: `main`
- Working tree: dirty, with Phase 1-5 changes not committed (30 status entries at
  evidence capture time).
- Release implication: this is not exact-commit release evidence and must not be
  labeled XM demo accepted.

## Windows target host

- OS: Microsoft Windows 11 Pro, version 10.0.26200, 64-bit.
- Installed RAM: 7.86 GB.
- Python: 3.12.10.
- MetaTrader5 Python package: 5.0.6180.
- Terminal executable: `C:\Program Files\MetaTrader 5\terminal64.exe`.

## MT5/XM attempt

- No MT5 process was running at the start of acceptance.
- The installed terminal was launched through the read-only initialization path.
- Python IPC initialization failed with MT5 error `-10005` (IPC timeout).
- No connected account, terminal build, XM demo server, quotes, positions, orders,
  or deals could be verified.
- No broker-write operation was attempted.
- The terminal remains open for the human tester to log into the XM demo account.

Result: checklist items requiring XM connectivity or manual broker action remain
unchecked. `XM_DEMO_ACCEPTED` is not established.

## Dashboard startup, restart, and shutdown

- Dashboard bound to `127.0.0.1:8061` and started successfully with MT5 offline.
- `/api/health/live` returned `alive`.
- Authenticated `/api/health/ready` returned overall `ready`, database `ready`, MT5
  `degraded`, and observation service `idle`.
- Authenticated journal access returned HTTP 200 before controlled shutdown.
- Two controlled Ctrl+C shutdowns completed through Uvicorn application shutdown.
- A subsequent dashboard restart reopened health and journal services successfully.
- Journal record count was zero on this host, so service persistence was verified
  but non-empty live trade persistence still requires the XM checklist.

## Short resource/load probe

This is a short operational probe, not a long-duration soak test.

- Idle sample duration: 10 seconds, 10 samples.
- Idle working set: 146.64 MB minimum/maximum.
- Idle private memory maximum: 347.94 MB.
- Idle handles/threads maximum: 341 / 16.
- Idle CPU use: approximately 0.15% of one logical core.
- Bounded-load probe: 500 local health/readiness requests in 2.743 seconds.
- Working-set delta: +1.85 MB.
- Private-memory delta: +2.16 MB.
- Handle delta: 0.
- Thread delta: 0.
- CPU consumed during the probe: 1.625 seconds.
- Follow-up verification: 100 requests, zero failures, completed in 0.454 seconds.
- With a 4,096-byte limit and two backups, logs remained bounded to the active log
  plus two rotated files.

## Automated evidence on this Windows host

- Focused Phase 5 logging check after live probing: 10 passed.
- Final Phase 5 non-browser regression: 2,192 passed, 5 skipped, 20 warnings,
  88 subtests passed.
- Chromium E2E: 6 passed.

Automated evidence does not replace the manual XM demo checklist.

## Provider operational verification

- Active provider: Google.
- Configured quick/deep model: `google/gemini-2.5-flash`.
- Effective request timeout/retry limits: 120 seconds / 2 retries.
- No Google credential was present in the process environment.
- No live provider request was made; real timeout/retry and actual-token metadata
  verification remains pending. No credential value was inspected or recorded.

## Remaining human acceptance

1. Log into an XM demo account in the open MT5 terminal.
2. Record sanitized MT5 build and demo-server evidence.
3. Execute all 25 items in `docs/XM_MT5_DEMO_ACCEPTANCE.md`, with every broker
   action performed manually in MT5.
4. Run a longer resource soak during real analysis and observation.
5. Repeat restart/recovery with non-empty trade, deal, reflection, and lesson data.
