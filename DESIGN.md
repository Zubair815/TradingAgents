---
version: "1.0"
name: "TradingAgents Market Operations Desk"
description: "An evidence-first decision-support terminal for multi-agent analysis and read-only MT5 observation."
colors:
  ink: "#070B12"
  panel: "#0E1520"
  panelRaised: "#141D2A"
  line: "#263244"
  paper: "#F2F5F7"
  muted: "#93A0B3"
  telemetry: "#58B8E8"
  decision: "#F2B84B"
  positive: "#4FD19A"
  danger: "#FF6B72"
typography:
  sans: { fontFamily: "'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif" }
  display: { fontFamily: "'IBM Plex Sans Condensed', 'Arial Narrow', sans-serif" }
  mono: { fontFamily: "'IBM Plex Mono', 'Cascadia Code', monospace" }
rounded:
  DEFAULT: "0.625rem"
  sm: "0.375rem"
  md: "0.625rem"
  lg: "0.875rem"
spacing:
  section-gap: "1.25rem"
  page-max: "100rem"
components:
  button: {}
  card: {}
  dialog: {}
  table: {}
  input: {}
  toast: {}
---

# TradingAgents Design System

## Overview

### Creative North Star

A contemporary market-operations desk: dense enough for repeated professional use, calm enough for risk review, and visibly different from a speculative crypto dashboard. It borrows the discipline of a trading blotter and the legibility of aircraft instrumentation without becoming skeuomorphic.

### Product context and register

- **Audience and primary job:** Analysts and manual MT5 traders observe broker truth, run multi-agent analysis, validate risk, choose execute-or-skip manually, and reconcile outcomes.
- **Target market and evidence:** Global Forex and equities research, established by `README.md`, `FINAL_REPORT.md`, and the browser acceptance report.
- **Locale and language policy:** English (`en`). Dates and numbers retain source/domain timezone labels. Missing broker evidence says `Unavailable`, never a plausible zero.
- **Usage scene:** Desktop-first, long-running local dashboard with dense data, repeated monitoring, and occasional narrow-screen access.
- **Register:** Product/admin dashboard.
- **Memorable signature:** The left signal rail combines subsystem navigation, observer state, and manual-only policy as a compact operations tape.
- **Restraint:** Tables, forms, reports, warnings, and destructive decisions remain familiar and quiet. Color indicates semantic state, not decoration.
- **Anti-references:** Neon crypto exchanges, glassmorphism, gradient-heavy SaaS dashboards, and playful emoji navigation.
- **Token ownership/runtime mapping:** This file is normative. `web/static/styles.css` is the runtime adapter; `premium-ui.json` and browser tests are drift gates.

## Colors

`ink` and `panel` create a near-black blue field. `telemetry` is reserved for observed/system data, while `decision` marks proposals and human decisions. `positive` and `danger` retain their semantic roles. `paper`, `muted`, and `line` create readable hierarchy. Forced-colors mode uses system colors.

## Typography

IBM Plex Sans carries controls and prose; IBM Plex Sans Condensed gives section headings an operations-manual character; IBM Plex Mono is used for identifiers, prices, timestamps, and evidence. Labels use sentence case. Numeric columns use tabular figures.

## Layout

Desktop uses a 216px signal rail plus a flexible content canvas capped at 100rem. Cards use a 20px content rhythm and compact 12px internal gaps. Below 980px the rail becomes a horizontally scrollable subsystem bar; below 700px cards and forms stack. Tables own horizontal overflow and stable scroll gutters.

## Elevation & Depth

Hierarchy comes from tonal surfaces and crisp borders, not glow. Raised dialogs use one deep shadow; routine cards remain flat. Sticky header and rail preserve readability over scrolling content.

## Shapes

Controls use 6px corners, cards 10px, and dialogs 14px. Pills are limited to compact status tags. Hairline dividers and a 3px active-rail marker encode grouping and selection.

## Components

### Foundational visual states

Hover raises contrast without moving geometry. Focus uses a 2px telemetry ring with offset. Disabled controls reduce contrast and reject pointer interaction. Busy controls preserve dimensions. Loading, empty, no-results, and error regions reserve compatible space and use direct recovery copy.

### Buttons and actions

Solid decision buttons commit or launch; outlined neutral buttons refresh or navigate; danger is reserved for cancellation or irreversible actions. Icon-only controls require an accessible name. Busy state keeps the original width.

### Navigation and data display

Primary navigation is the signal rail on desktop and a scrollable tab bar on narrow screens. Native tables remain semantic and scroll within a table owner. Evidence values use mono type and never rely on color alone.

### Forms and overlays

Product forms use app-owned validation with `novalidate`. Native selects and dates are intentional because operating-system popup behavior is accepted for this local desktop tool. Dialogs are app-owned, viewport-bounded, trap focus, restore focus, and close with Escape when safe. Toasts use one fixed live region.

### Iconography

Use the existing 24px outline SVG language with 1.75–2px strokes. Text labels remain visible for all primary actions.

### Motion

Motion is functional: 120ms control feedback and 180ms view/overlay transitions. No ambient pulse except an active connection dot. Reduced-motion disables non-essential animation and smooth scrolling.

### Content and data visualization

Voice is direct, factual, and safety-aware. `Unavailable` means evidence is absent. `Observed` describes MT5 telemetry; `Proposed` describes model output; `Manual` describes broker execution. Charts use telemetry blue, decision amber, positive green, and danger red with text equivalents.

## Do's and Don'ts

- **Do:** Keep read-only observation and manual execution visible at decision points.
- **Do:** Use the same evidence/status vocabulary across dashboard, MT5, proposals, and journal.
- **Don't:** imply profitability, broker execution, or certainty with decorative color.
- **Don't:** add screen-local colors, dialogs, toasts, or scrollbar themes.
