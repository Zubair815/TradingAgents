# Phase 4 - Windows and MT5 operational soak

Evidence date: 2026-10-02 (Asia/Karachi)

Implementation status: **COMPLETE**

External acceptance status: **PENDING A REAL FOUR-HOUR WINDOWS/XM RUN**

Requirement: `GAP-03` / NFR and deployment operational reliability evidence.

## Implemented workflow

The `tradingagents operations soak-dashboard` command now runs a controlled
acceptance workload against an already-running authenticated dashboard. It:

- repeatedly checks readiness and the read-only MT5 account, positions, pending
  orders, and deal-history endpoints;
- launches real Forex analyses from an explicit JSON request;
- disconnects and reconnects the read-only MT5 runtime at a declared interval;
- samples the dashboard process working set, private bytes, CPU time, threads,
  Windows handles, and rotating-log sizes;
- records sanitized host/runtime details, Git revision, and whether the working
  tree was clean at launch;
- writes one atomic, sanitized JSON evidence file; and
- returns a nonzero exit code unless every objective acceptance check passes.

The authenticated `/api/health/resources` endpoint exposes aggregate process and
log measurements only. It does not expose environment variables, credentials,
account identifiers, file paths, model prompts, or broker response bodies.

## Acceptance rules

The default acceptance profile requires:

- at least four hours of observed runtime;
- at least two successful resource samples;
- no more than a one-percent combined probe failure rate;
- no more than three consecutive workload failures;
- working-set growth no greater than 128 MiB;
- thread growth no greater than four;
- Windows handle growth no greater than 32;
- every observed log file within its configured rotation bound;
- at least one successful disconnect/reconnect cycle; and
- at least one successfully queued real analysis.

A short development run, a run without analysis, or a run without reconnect
exercise is reported as `FAIL`. Automated unit tests are not represented as
Windows/XM soak evidence.

## Real-host command

Start the dashboard with XM MT5 connected and the observation service running.
Set `TRADINGAGENTS_DASHBOARD_API_KEY` in the same shell when explicit API-key
mode is enabled, then run:

```powershell
.venv\Scripts\tradingagents.exe operations soak-dashboard `
  --base-url http://127.0.0.1:8050 `
  --duration-hours 4 `
  --sample-seconds 30 `
  --reconnect-minutes 30 `
  --analysis-minutes 30 `
  --analysis-payload docs\operational-soak-analysis.example.json `
  --output artifacts\operational-soak.json
```

Provider credentials and sufficient provider allowance are required for the
analysis workload. The output records failure classes only and never writes the
credential or vendor error message.

## Verification

- Focused soak, reliability, health and MT5 runtime suites: `54 passed`.
- Full non-browser suite: `2230 passed, 5 skipped, 88 subtests passed`.
- Chromium browser E2E: `6 passed`.
- Full Ruff and Python byte-compilation: passed.

## Acceptance boundary

The implementation makes `GAP-03` reproducibly testable but does not manufacture
the missing external evidence. `GAP-03` moves from missing tooling to
**CANNOT VERIFY / acceptance pending** until the command passes for four hours on
the target Windows host with the connected XM demo terminal and real analyses.

Suggested commit: `feat(operations): add measurable Windows MT5 soak harness`
