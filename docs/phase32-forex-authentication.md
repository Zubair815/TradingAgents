# Phase 32: Forex API authentication and safe errors

## Policy

Every registered `/api/forex/*` operation requires authentication, including GET
requests, SSE streams, journal/account reads and computational endpoints. There
are no public Forex exceptions. The router's shared dependency is the canonical
check and applies automatically to future operations on this router. Import,
configuration and unexpected authentication failures deny access.

The dashboard HTML, static assets and `/api/config` remain public bootstrap
resources. Config uses an explicit allowlist of model/provider settings; it never
returns configured LLM keys or session tokens. There are no key-value settings
endpoints in this phase. A future settings mutation must use the same protection.

## Browser and API authentication

- Without a configured dashboard key, visiting `/` from a loopback peer and a
  localhost/loopback host establishes the local browser session. Remote peers and
  arbitrary Host names cannot obtain an automatic session. Remote deployments
  must configure a dashboard API key.
- With a configured key, `X-API-Key` or `Authorization: Bearer` authenticates API
  calls. An explicitly wrong credential cannot fall back to a valid cookie.
- The browser keeps the entered key only in memory. An authenticated
  `POST /api/auth/session` sets an HttpOnly cookie for EventSource, whose API
  cannot send custom headers. The browser renews this exchange before expiry.
  Keys and session tokens never enter URLs, rendered HTML or web storage.
- Cookies are host-only, path `/`, HttpOnly, SameSite=Strict and Secure on HTTPS.
  HTTP localhost uses a non-Secure cookie. Expiry is eight hours and is checked
  server-side using a signed timestamp. A process restart invalidates sessions;
  changing the configured key invalidates key-derived sessions. Cookie requests
  with a different Origin are rejected, including different localhost ports.
- The internal `X-Session-Token` compatibility header remains process-scoped; the
  secret is never sent to JavaScript. Normal browser use needs no token copying.
- This is a single-user, single-process local dashboard policy. HTTPS proxies
  must pass the correct scheme through a trusted ASGI proxy configuration.

## Credentials and errors

MT5 passwords are request-only credentials. Constructor compatibility credentials
are consumed once and cleared before connecting, including failure paths. Connect
without a password can reuse the terminal's existing login. No credential is
written to storage or returned by status/config. Broker exception descriptions
and account identifiers are removed from connection failures.

Forex errors use `{"error":{"code":"...","message":"...","details":{}}}`.
A sanitized `detail` field preserves compatibility with existing API clients.
Validation errors omit rejected input values. Unexpected exceptions, analysis
events and post-close failures expose safe messages rather than raw exceptions or
tracebacks. Application logs record error types rather than provider exception
text. All Forex responses use `Cache-Control: no-store`.

Codes include UNAUTHORIZED, AUTH_UNAVAILABLE, INVALID_REQUEST, DATA_UNAVAILABLE,
STALE_DATA, HISTORICAL_DATA_UNAVAILABLE, MT5_DISCONNECTED, MT5_CONNECTION_FAILED,
PROPOSAL_NOT_FOUND, TRADE_NOT_FOUND, RUN_NOT_FOUND, RISK_REJECTED, BACKTEST_FAILED
and PROVIDER_ERROR. Existing historical/demo error codes remain compatible.
Risk decisions and retryable post-close results retain their existing domain
response semantics; a rejected decision is not an HTTP authentication failure.
The symbols endpoint retains its explicitly marked offline fallback with a safe
MT5_DISCONNECTED error object.

Frontend changes are limited to authenticated requests, key-session bootstrap,
error parsing, credential autocomplete and removal of a syntax-breaking stray
brace. No screen redesign is included.

## Verification

- `ruff check .`: passed.
- `pytest -q`: 2,035 passed, 5 skipped, 20 warnings, 88 subtests passed.
- `pytest -q tests/test_forex_routes.py`: 85 passed.
- `pytest -q tests/test_web_server.py`: 17 passed.
- Security plus affected analytics suites: 91 passed, including 64 security tests
  covering all 55 registered Forex operations.
- `node --check web/static/app.js`, executable JavaScript error-parser/request
  tests, and `git diff --check`: passed.

Verification uses TestClient and mocked MT5/provider failures. No live broker
credentials were used and no broker execution was introduced.

## Route audit

All operations below are protected by the same dependency. Public exceptions: none.

| Method | Route | Authentication |
| --- | --- | --- |
| GET | `/api/forex/journal/trades` | Required |
| GET | `/api/forex/journal/trades/{trade_id}` | Required |
| POST | `/api/forex/journal/trades/manual-open` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/close` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/modify-sl` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/modify-tp` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/partial-close` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/reflection` | Required |
| GET | `/api/forex/journal/summary` | Required |
| GET | `/api/forex/journal/timeline` | Required |
| GET | `/api/forex/journal/performance` | Required |
| GET | `/api/forex/proposals` | Required |
| GET | `/api/forex/proposals/{proposal_id}` | Required |
| POST | `/api/forex/proposals` | Required |
| POST | `/api/forex/proposals/evaluate-risk` | Required |
| POST | `/api/forex/proposals/size` | Required |
| POST | `/api/forex/proposals/{proposal_id}/status` | Required |
| POST | `/api/forex/proposals/reconcile` | Required |
| GET | `/api/forex/mt5/status` | Required |
| POST | `/api/forex/mt5/connect` | Required |
| POST | `/api/forex/mt5/disconnect` | Required |
| POST | `/api/forex/journal/trades/{trade_id}/post-close/retry` | Required |
| GET | `/api/forex/mt5/account` | Required |
| GET | `/api/forex/mt5/symbols` | Required |
| GET | `/api/forex/mt5/symbol/{symbol}` | Required |
| GET | `/api/forex/mt5/tick/{symbol}` | Required |
| GET | `/api/forex/mt5/positions` | Required |
| GET | `/api/forex/mt5/orders` | Required |
| GET | `/api/forex/mt5/deals` | Required |
| POST | `/api/forex/analyze` | Required |
| GET | `/api/forex/runs` | Required |
| GET | `/api/forex/runs/{run_id}` | Required |
| GET | `/api/forex/runs/{run_id}/events` | Required |
| GET | `/api/forex/analyze/{run_id}/stream` | Required |
| GET | `/api/forex/analyze/{run_id}/report` | Required |
| GET | `/api/forex/analyze/{run_id}/status` | Required |
| POST | `/api/forex/backtest/estimate` | Required |
| POST | `/api/forex/backtest/run` | Required |
| GET | `/api/forex/backtest/runs` | Required |
| GET | `/api/forex/backtest/{backtest_id}` | Required |
| GET | `/api/forex/dashboard/overview` | Required |
| GET | `/api/forex/analytics/dashboard` | Required |
| GET | `/api/forex/analytics/performance` | Required |
| GET | `/api/forex/analytics/metrics` | Required |
| POST | `/api/forex/analytics/monte-carlo` | Required |
| GET | `/api/forex/analytics/calibration` | Required |
| POST | `/api/forex/analytics/ablation` | Required |
| GET | `/api/forex/learning/lessons` | Required |
| POST | `/api/forex/learning/reflect/{trade_id}` | Required |
| GET | `/api/forex/learning/retrieve` | Required |
| GET | `/api/forex/metrics/skipped` | Required |
| POST | `/api/forex/metrics/skipped/evaluate` | Required |
| GET | `/api/forex/metrics/comparative` | Required |
| GET | `/api/forex/metrics/confidence-calibration` | Required |
| POST | `/api/forex/metrics/calibrate-confidence` | Required |
