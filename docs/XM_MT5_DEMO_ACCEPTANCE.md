# Windows + XM Demo manual acceptance checklist

Status: **NOT EXECUTED**

This checklist requires a Windows machine with XM MetaTrader 5, a demo account,
market connectivity, and user-controlled broker actions. Repository tests use
mocks and cannot substitute for this acceptance run.

Record the date, commit, MT5 build, XM demo server, tester, and evidence links
before marking any item complete. Never record a password or API key.

- [ ] 1. Launch XM MT5 and log into the demo account.
- [ ] 2. Start the TradingAgents dashboard from the audited commit.
- [ ] 3. Connect the read-only MT5 integration.
- [ ] 4. Confirm masked account and server status are correct.
- [ ] 5. Confirm open positions, pending orders, and deals are visible.
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

Acceptance result: **NOT EXECUTED — no live XM/MT5 evidence was supplied during
the final repository audit.**
