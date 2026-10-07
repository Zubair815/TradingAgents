"""Inspection harness: boot the real dashboard against an isolated profile and
drive the Journal module through the actual UI with Playwright."""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, r"D:\TradingAgents")

# Isolate ~/.tradingagents (journal db, caches) BEFORE importing app modules.
PROFILE = tempfile.mkdtemp(prefix="ta_inspect_")
os.environ["USERPROFILE"] = PROFILE
os.environ["HOME"] = PROFILE

import uvicorn  # noqa: E402

import web.server as server  # noqa: E402

PORT = 8731
BASE = f"http://127.0.0.1:{PORT}"

config = uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning")
uv = uvicorn.Server(config)


def run_server():
    uv.run()


threading.Thread(target=run_server, daemon=True).start()

for _ in range(100):
    try:
        with urllib.request.urlopen(BASE + "/", timeout=1) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(0.2)
else:
    raise SystemExit("server did not start")

print("PROFILE:", PROFILE)
print("SERVER UP:", BASE)

from playwright.sync_api import sync_playwright  # noqa: E402

SEED_JS = r"""
async () => {
  const out = {};
  const post = async (path, body) => {
    const r = await fetch('/api/forex' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    let j = null; try { j = await r.json(); } catch(e) { j = {parse_error:String(e)}; }
    return {status:r.status, body:j};
  };
  const get = async (path) => {
    const r = await fetch('/api/forex' + path);
    let j = null; try { j = await r.json(); } catch(e) { j = {parse_error:String(e)}; }
    return {status:r.status, body:j};
  };

  // Trade A: planned open/close, user supplies open_time + gross_profit + commission + swap.
  const a = await post('/journal/trades/manual-open', {
    pair:'EURUSD', action:'LONG', entry_price:1.0850, lots:1.0,
    stop_loss:1.0810, take_profit:1.0920, open_time:'2020-01-01T00:00:00+00:00', actor:'Inspector'});
  out.a_open = a;
  const aId = a.body.trade_id;
  out.a_close = await post(`/journal/trades/${aId}/close`, {
    close_price:1.0920, exit_reason:'TAKE_PROFIT', gross_profit:9999.0, commission:5.0, swap:-1.0, actor:'Inspector'});
  out.a_detail_after_close = await get(`/journal/trades/${aId}`);

  // Trade B: NO stop loss (optional) to observe derived risk/R handling.
  const b = await post('/journal/trades/manual-open',
    {pair:'EURUSD', action:'SHORT', entry_price:1.1000, lots:0.5, take_profit:1.0900, actor:'Inspector'});
  out.b_open = b;
  const bId = b.body.trade_id;
  out.b_close = await post(`/journal/trades/${bId}/close`, {close_price:1.0950, exit_reason:'TAKE_PROFIT'});
  out.b_detail = await get(`/journal/trades/${bId}`);

  // Trade C: partial close then final close (scale-out P&L accounting).
  const c = await post('/journal/trades/manual-open',
    {pair:'EURUSD', action:'LONG', entry_price:1.0850, lots:1.0, stop_loss:1.0810, actor:'Inspector'});
  const cId = c.body.trade_id;
  out.c_partial = await post(`/journal/trades/${cId}/partial-close`, {lots_to_close:0.5, close_price:1.0890, exit_reason:'TAKE_PROFIT'});
  out.c_final = await post(`/journal/trades/${cId}/close`, {close_price:1.0930, exit_reason:'TAKE_PROFIT'});
  out.c_detail = await get(`/journal/trades/${cId}`);

  // Modify SL/TP on an open trade + reflection.
  const d = await post('/journal/trades/manual-open',
    {pair:'EURUSD', action:'LONG', entry_price:1.0850, lots:0.7, stop_loss:1.0800, actor:'Inspector'});
  const dId = d.body.trade_id;
  out.d_sl = await post(`/journal/trades/${dId}/modify-sl`, {new_stop_loss:1.0850, reason:'move to BE'});
  out.d_tp = await post(`/journal/trades/${dId}/modify-tp`, {new_take_profit:1.0950, reason:'extend'});
  out.d_reflect = await post(`/journal/trades/${dId}/reflection`, {reflection_text:'Good discipline', category_tag:'BREAKOUT', execution_quality:'A'});
  out.d_detail = await get(`/journal/trades/${dId}`);

  // Validation probes.
  out.bad_status = await get('/journal/trades?status=NOPE');
  out.zero_price_close = await post(`/journal/trades/${dId}/close`, {close_price:0, exit_reason:'TAKE_PROFIT'});
  out.bad_exit = await post(`/journal/trades/${dId}/close`, {close_price:1.10, exit_reason:'NOT_A_REASON'});
  out.bad_pair = await post('/journal/trades/manual-open', {pair:'NOTAPAIR', action:'LONG', entry_price:1.0, lots:1, stop_loss:0.9});
  out.neg_lots = await post('/journal/trades/manual-open', {pair:'EURUSD', action:'LONG', entry_price:1.0, lots:-1, stop_loss:0.9});
  out.bad_action = await post('/journal/trades/manual-open', {pair:'EURUSD', action:'SIDEWAYS', entry_price:1.0, lots:1, stop_loss:0.9});
  out.missing_trade = await get('/journal/trades/trd_does_not_exist');
  out.list = await get('/journal/trades?limit=50');
  out.summary = await get('/journal/summary');
  return out;
}
"""

findings = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: console_msgs.append(f"pageerror: {e}"))
    page.goto(BASE + "/", wait_until="networkidle")
    findings = page.evaluate(SEED_JS)

    # Drive the actual Journal UI.
    page.click('#tab-journal')
    page.click('#btnRefreshJournal')
    try:
        page.wait_for_selector('.journal-table tbody tr', timeout=8000)
    except Exception as e:
        findings['ui_table_error'] = str(e)
    findings['ui_row_count'] = page.locator('.journal-table tbody tr').count()
    findings['ui_first_row_text'] = page.locator('.journal-table tbody tr').first.inner_text()
    page.screenshot(path=os.path.join(r"D:\TradingAgents\tmp", "journal_list.png"), full_page=False)

    # Open the first trade's detail modal.
    page.locator('.journal-table tbody tr button[data-trade-id]').first.click()
    try:
        page.wait_for_selector('#tradeDetailBody[data-state="success"]', timeout=8000)
    except Exception as e:
        findings['ui_detail_error'] = str(e)
    findings['ui_detail_contains'] = {
        "Identity": "Identity" in page.locator('#tradeDetailBody').inner_text(),
        "timeline": "Chronological lifecycle timeline" in page.locator('#tradeDetailBody').inner_text(),
        "unavailable": "Unavailable" in page.locator('#tradeDetailBody').inner_text(),
    }
    page.screenshot(path=os.path.join(r"D:\TradingAgents\tmp", "journal_detail.png"))
    findings['console'] = console_msgs[:40]
    browser.close()

print("==== RESULTS ====")
print(json.dumps(findings, indent=2, default=str))
