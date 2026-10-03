# Phase 5 - XM demo lifecycle acceptance

Evidence date: 2026-10-02 (Asia/Karachi)

Implementation status: **COMPLETE**

External acceptance status: **NOT XM DEMO ACCEPTED**

Requirement: `GAP-01` and XM demo execution/journal/learning acceptance.

## Implemented acceptance control

The CLI now provides a revision-locked evidence workflow:

```powershell
.venv\Scripts\tradingagents.exe operations xm-acceptance start `
  --tester "your-name" `
  --output artifacts\xm-acceptance.json
```

The record contains all 25 checklist items and cannot become accepted unless:

- every required item passes in dependency order;
- each item uses the required `SYSTEM` or `HUMAN` evidence class;
- only the account-mode-dependent partial-close item is skipped;
- every item belongs to the same exact 40-character Git revision;
- the acceptance session was started from a clean working tree; and
- evidence summaries contain no obvious credential, API-key, bearer-token, or
  unmasked account-login material.

Record one completed item with:

```powershell
.venv\Scripts\tradingagents.exe operations xm-acceptance record `
  --evidence artifacts\xm-acceptance.json `
  --step 1 `
  --kind HUMAN `
  --status PASS `
  --actor "your-name" `
  --summary "XM demo terminal open; account identity checked in terminal and not recorded"
```

Check the current truthful status and next required action with:

```powershell
.venv\Scripts\tradingagents.exe operations xm-acceptance status `
  --evidence artifacts\xm-acceptance.json
```

The evidence file is replaced atomically after each update. Failed, mismatched,
wrong-kind, out-of-order, sensitive, and non-skippable `SKIP` evidence is rejected.

## Safety boundary

This implementation does not place, modify, partially close, or close an MT5
order. Checklist items requiring broker changes remain manual actions performed
only by the tester in XM MT5. TradingAgents continues to observe and reconcile
the resulting broker state read-only.

## Current live prerequisite check

At implementation time:

- the XM terminal was initially stopped, then a direct read-only probe connected
  successfully to terminal build 6230 on `XMGlobal-MT5 2`;
- the probe observed a USD demo context, 1,631 symbols, zero open positions,
  zero pending orders and 14 recent deals without recording the account login;
- the current EURUSD tick was unavailable and failed explicitly with
  `DataInsufficientError` rather than producing fabricated quote data;
- no dashboard listener was present on ports 8050 or 8061; and
- no OpenAI, Google, Anthropic, or OpenRouter credential was present in the
  current process environment.

The probe disconnected cleanly and performed no broker write. Consequently,
checklist items 6-25 were not executed and no acceptance record
was falsely advanced. The existing connectivity evidence for items 1-5 remains
historical evidence only and cannot be attached to a new dirty working-tree
revision as final release acceptance.

## Verification

- Focused Phase 5 evidence, operational and MT5 suites: `89 passed` before the
  final lint-only correction.
- Final focused/full/browser/static totals are recorded after the phase gate.

Suggested commit: `feat(acceptance): add revision-locked XM lifecycle evidence`
