# Windows + XM Demo manual acceptance checklist

Status: **IN PROGRESS - CONNECTIVITY VERIFIED 2026-10-02**

This checklist requires a Windows machine with XM MetaTrader 5, a demo account,
market connectivity, and user-controlled broker actions. Repository tests use
mocks and cannot substitute for this acceptance run.

Record the date, commit, MT5 build, XM demo server, tester, and evidence links
before marking any item complete. Never record a password or API key.

- [x] 1. Launch XM MT5 and log into the demo account.
- [x] 2. Start the TradingAgents dashboard from the audited implementation.
- [x] 3. Connect the read-only MT5 integration.
- [x] 4. Confirm masked account and server status are correct.
- [x] 5. Confirm open positions, pending orders, and deals are visible.
- [ ] 6. Launch a Forex analysis.
- [ ] 7. Confirm the selected execution and context timeframes reach the report.
- [ ] 8. Confirm the result is `LONG`, `SHORT`, or `NO_TRADE` with a proposal.
- [ ] 9. Confirm the deterministic risk decision and sizing evidence are present.
- [ ] 10. Confirm TradingAgents sends no broker order.
- [ ] 11. If the tester chooses, manually place a tiny demo trade in XM MT5.
- [ ] 12. Confirm the observer detects the position without changing it.
- [ ] 13. Confirm proposal matching or explicit unplanned/manual classification.
- [ ] 14. Modify the stop loss manually in XM MT5.
- [ ] 15. Confirm a journal stop-loss modification event is recorded.
- [ ] 16. If supported by the account mode, partially close the trade manually.
- [ ] 17. Close the remaining position manually.
- [ ] 18. Confirm exit deals, commission, fees, swap, and realized result are recorded.
- [ ] 19. Confirm holding-window history is retrieved and MFE/MAE is calculated.
- [ ] 20. Confirm post-close reflection completes or fails explicitly and retryably.
- [ ] 21. Confirm structured lessons persist.
- [ ] 22. Confirm lesson navigation reaches its source trade.
- [ ] 23. Confirm performance, execution, skipped-proposal, and calibration views update.
- [ ] 24. Restart the dashboard.
- [ ] 25. Confirm journal, deal, reflection, and lesson data remain available.

Acceptance result: **NOT XM DEMO ACCEPTED - the manual trade lifecycle remains incomplete.**

## 2026-10-01 attempt

The Windows host, MetaTrader5 Python package, and installed terminal executable
were verified. The terminal was launched, but Python IPC initialization timed out
with error `-10005`; no connected account or XM demo server was available. No
checklist item above is marked complete because the required live evidence was not
obtained and no manual broker action was performed.

Host, restart, resource, and sanitized failure evidence is recorded in
`docs/PHASE5_ACCEPTANCE_EVIDENCE.md`.

The original IPC blocker was resolved on 2026-10-02 by selecting the XM-specific
terminal executable. Items 1, 3, 4 and 5 are now supported by sanitized live
evidence. The tested Phase 6 implementation is now committed and passed all
seven GitHub CI jobs; items 6-25 still require the manual acceptance workflow.

Acceptance result remains: **NOT XM DEMO ACCEPTED**.
