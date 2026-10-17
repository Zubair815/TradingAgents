# UX Contract

## Product context

- Audience: Analysts and manual MT5 users.
- Primary jobs: Observe, analyze, validate, manually execute or skip, and reconcile.
- Target markets: Global Forex and equities research.
- Active locales: English (`en`).
- Timezone/calendar policy: Explicitly label UTC or broker/source time; Gregorian calendar.
- Accessibility target: WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Manual-only execution | `README.md`, `FINAL_REPORT.md` | Product and release contract | 2026-10-13 |
| MT5 observer safety | `docs/TARGET_STATE_REMEDIATION_CONTEXT.md` | Domain contract | 2026-10-13 |
| Browser workflows | `docs/BROWSER_SYSTEM_ACCEPTANCE_REPORT.md` | Acceptance evidence | 2026-10-13 |
| Authentication | `docs/phase32-forex-authentication.md` | Security contract | 2026-10-13 |

## Visual contract

- Project design system: `DESIGN.md`.
- Token ownership: `DESIGN.md` is normative; `web/static/styles.css` is the runtime adapter.
- Supported theme: Dark operations desk; forced-colors system adaptation.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Native `select.form-select` | DESIGN + this contract | native | browser keyboard + popup |
| Date | Native date input | this contract | native | browser keyboard |
| Form | Dashboard field system and Observer settings form | server schema + this contract | analysis / backtest / settings / observer | E2E validation |
| Scrollbar | Global rules in `styles.css` | DESIGN | stable-gutter exception | static + browser |
| Toast | `#toast` + `showToast` | this contract | success / warning / info / error | live-region browser test |
| CRUD | Existing API routes and modal workflows | server API | stay-in-context | browser workflow |

## Component behavior

Buttons, inputs, tabs, tables, and icon controls require default, hover, focus-visible, disabled, and busy states. Search controls require explicit clear and stale-request handling when introduced. Textareas use `resize: none`. Product forms use `novalidate`, inline errors, and duplicate-submit prevention.

## Dataset navigation

Current operational tables are bounded local snapshots. Table surfaces own horizontal scrolling and stable gutters. When result counts become unbounded, server pagination is required. Filter, sort, and page state enter URL parameters when shareable.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome |
|---|---|---|---|---|---|---|
| Analyze | Launch analysis | Fixed-size busy button + pipeline | Report in current view | Inline completion + toast | Inline error, values preserved, retry | Report heading |
| Cancel run | Cancel analysis | Button disabled | Current pipeline | Status + toast | Retry remains available | Cancel trigger |
| Save settings | Save changes | Button busy/disabled | Settings remains open | Inline status + toast | Fields preserved with inline error | Status region |
| Inspect proposal/trade | View action | Stable loading region | Modal detail | None | Inline modal error/retry | Dialog then trigger |
| Connect/disconnect MT5 | Explicit action | Button busy | MT5 remains open | Status + toast | Diagnostic copy and retry | Trigger |
| Start/stop pair observer | Explicit action | Trigger busy; opposite action follows server state | Observer remains open | Status chip + toast | Persistent page error and retry | Trigger |
| Save observer settings | Save settings | Fixed-size busy button | Observer remains open | Inline saved state + toast | Fields preserved, inline field error, retry | First invalid field or save status |

## Navigation and responsive behavior

- Document title: active subsystem followed by `TradingAgents`.
- Tab state: one selected tab, arrow-key navigation, and roving tabindex.
- Desktop uses the signal rail; narrow screens use a horizontal tab strip.
- Tables scroll inside their own surfaces and never force page clipping.

## Overlays and feedback

App-owned modal overlays use Escape, focus trap, inert background semantics, responsive bounds, and trigger focus restoration. Toasts are bottom-right, deduplicated, and announced. Layer tokens in `styles.css` are canonical.

## Async and resilience

Mutations are pessimistic and block duplicate submission. SSE progress is the canonical long-running state. MT5 offline is a normal degraded condition and must not fabricate data. Retriable failures preserve context and values.

## Validation and sensitive values

Server validation is authoritative; client validation prevents obvious invalid requests and focuses the first invalid field. Secrets remain masked and never enter URLs, storage, or toasts.

## Verification

- Static: premium audit, Ruff, JavaScript syntax, and `git diff --check`.
- Runtime: focused browser E2E plus narrow viewport, keyboard tabs, dialogs, loading, empty, offline, and reduced-motion checks.
- Canonical sibling: Dashboard, MT5, and Proposals use the same status/evidence language.
