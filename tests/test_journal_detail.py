"""Phases 33-34: stored trade investigation contract and frontend behavior."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord
from web import server
from web.forex_routes import reset_forex_state, set_forex_dependencies


@pytest.fixture
def journal_client(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_API_KEY", None)
    reset_forex_state()
    journal = ForexTradeJournal(":memory:")
    set_forex_dependencies(journal=journal)
    client = TestClient(server.app)
    client.get("/")
    yield journal, client
    reset_forex_state()
    journal.close()


def test_investigation_joins_only_stored_trade_evidence(journal_client):
    journal, client = journal_client
    snapshot = {"entry_price": 1.08, "stop_loss": 1.075, "reasoning": "Original reasoning", "provider": "test-provider"}
    journal.save_proposal(ProposalRecord(
        proposal_id="proposal", pair="EURUSD", action="LONG", entry_price=1.081,
        stop_loss=1.076, proposal_payload=snapshot,
        risk_decision={"decision": "APPROVE", "approved_lot_size": 1.0},
    ))
    trade = journal.record_trade_open(trade_id="trade", pair="EURUSD", action="LONG",
        open_price=1.0801, stop_loss=1.075, lots=1, proposal_id="proposal",
        open_time_utc="2026-01-01T09:00:00+00:00")
    journal.record_trade_close(trade.trade_id, close_price=1.09)
    metrics = {"mfe_price": 1.095, "mfe_r": 3, "source": "MT5", "precision": "BAR_APPROXIMATION"}
    journal.update_trade_metadata("trade", {"mfe_mae": metrics, "execution_quality": {"slippage_pips": 1}})
    journal.update_trade_reflection("trade", reflection="Recorded reflection", tags=["disciplined"])
    journal.record_event(event_id="prelude", proposal_id="proposal", event_type="PROPOSAL_CREATED",
                         timestamp_utc="2026-01-01T10:00:00+02:00")
    journal.record_event(event_id="matched", proposal_id="proposal", trade_id="trade",
                         event_type="RECONCILIATION_MATCH", timestamp_utc="2026-01-01T09:00:00+00:00")
    journal.record_event(event_id="other-trade", proposal_id="proposal", trade_id="other",
                         event_type="POSITION_OPENED")
    for i in range(120):
        journal.record_event(event_id=f"fill-{i:03d}", trade_id="trade", event_type="DEAL_FILLED",
                             timestamp_utc="2026-01-01T09:30:00+00:00", payload={"volume": .01})
    journal.record_event(event_id="reflection", trade_id="trade", event_type="NOTE_ADDED",
                         actor="ForexReflectionAgent", payload={"rating": "GOOD"})
    lesson_id = journal.record_lesson(trade_id="trade", proposal_id="proposal", pair="EURUSD",
        setup_type="BREAKOUT", outcome_category="STANDARD_WIN", actionable_rule="Keep the initial risk plan",
        observation="Stored observation", root_cause="Stored evidence", confidence_score=.7)
    journal.record_lesson(trade_id="other", pair="EURUSD", setup_type="BREAKOUT",
                           outcome_category="STANDARD_WIN", actionable_rule="Unrelated lesson")
    before = len(journal.get_events(trade_id="trade", limit=-1))
    response = client.get("/api/forex/journal/trades/trade")
    assert response.status_code == 200
    data = response.json()
    assert data["original_proposal"] == snapshot
    assert data["proposal"]["entry_price"] == 1.081  # Never replace the immutable snapshot.
    assert data["risk_decision"]["decision"] == "APPROVE"
    assert data["metrics"] == metrics
    assert data["execution_comparison"]["slippage_pips"] == 1
    assert data["reflection"] == {"summary": "Recorded reflection", "rating": "GOOD", "tags": ["disciplined"]}
    assert [lesson["lesson_id"] for lesson in data["lessons"]] == [lesson_id]
    ids = [event["event_id"] for event in data["events"]]
    assert ids[0] == "prelude" and ids.count("matched") == 1
    assert "other-trade" not in ids and "fill-119" in ids
    assert len(ids) == len(set(ids))
    assert client.get("/api/forex/journal/trades/trade").json() == data
    assert len(journal.get_events(trade_id="trade", limit=-1)) == before


def test_investigation_missing_data_is_explicit(journal_client):
    journal, client = journal_client
    journal.record_trade_open(trade_id="manual", pair="EURUSD", action="LONG", open_price=1.08,
                              stop_loss=1.075, lots=1)
    data = client.get("/api/forex/journal/trades/manual").json()
    for field in ("original_proposal", "proposal", "metrics", "risk_decision", "execution_comparison"):
        assert data[field] is None
    assert data["reflection"]["rating"] is None
    assert data["lessons"] == []
    assert client.get("/api/forex/journal/trades/missing").status_code == 404
    assert TestClient(server.app).get("/api/forex/journal/trades/manual").status_code == 401


def run_node(tmp_path, script):
    if not shutil.which("node"):
        pytest.skip("Node.js is required for the lightweight frontend contracts")
    module = Path(__file__).resolve().parents[1] / "web/static/journal.js"
    path = tmp_path / "journal-contract.cjs"
    path.write_text("const ui = require(" + json.dumps(str(module)) + ");\n" + script, encoding="utf-8")
    subprocess.run(["node", str(path)], check=True, capture_output=True, text=True)


def test_frontend_investigation_and_timeline_render_stored_evidence(tmp_path):
    run_node(tmp_path, r'''
const assert = require('node:assert/strict');
const trade = {trade_id:'t<"id', pair:'EURUSD', status:'CLOSED', open_price:1.08, lots:1,
  close_price:null, net_profit:0, pips_gained:null, metadata:{}};
const table = ui.table([trade]);
for (const label of ['Trade ID','Direction','Open Time','Close Time','Lots','Net PnL','Details']) assert.ok(table.includes(label));
assert.ok(table.includes('data-trade-id="t&lt;&quot;id"'));
assert.ok(table.includes('Unavailable'));
assert.ok(table.includes('>0.00<')); // A real zero remains visible.
assert.ok(ui.table([]).includes('Journal empty'));
const events = [
 {event_id:'last', timestamp_utc:'2026-01-01T11:00:00Z', event_type:'POSITION_CLOSED', actor:'Broker', payload:{volume:.4}},
 {event_id:'first', timestamp_utc:'2026-01-01T12:00:00+02:00', event_type:'STOP_LOSS_MODIFIED', description:'<script>bad()</script>', price:1.08},
 {event_id:'unknown', event_type:'CUSTOM_AUDIT_EVENT', metadata:{rating:'GOOD'}},
];
const timeline = ui.timeline(events);
assert.ok(timeline.indexOf('STOP_LOSS_MODIFIED') < timeline.indexOf('POSITION_CLOSED'));
assert.ok(timeline.indexOf('POSITION_CLOSED') < timeline.indexOf('CUSTOM_AUDIT_EVENT'));
assert.ok(!timeline.includes('<script>'));
assert.ok(timeline.includes('&lt;script&gt;'));
assert.ok(timeline.includes('<details><summary>Raw event metadata'));
assert.ok(ui.timeline([]).includes('No lifecycle events recorded'));
const html = ui.detail({trade, events, proposal:{entry_price:999}, metrics:{is_available:false, mfe_pips:0, unavailable_reason:'No M1 bars'},
  lessons:[{lesson_id:'lesson-1', actionable_rule:'<img onerror="bad()">', confidence_score:.8, observation:'Observed'}]});
for (const heading of ['Identity','Original proposal','Risk decision','Actual execution','Exit','Excursion metrics','Execution comparison','Reflection','Lessons','Chronological lifecycle timeline']) assert.ok(html.includes('<h3>'+heading+'</h3>'));
assert.ok(html.includes('id="tradeTimeline"'));
assert.ok(html.includes('No M1 bars'));
assert.ok(!html.includes('999')); // No mutable-record fallback for an absent original snapshot.
assert.ok(!html.includes('<img'));
assert.ok(html.includes('MFE pips</dt><dd>Unavailable'));
assert.ok(ui.detail({}).includes('No trade detail'));
''')


def test_detail_loader_states_and_stale_request_guard(tmp_path):
    run_node(tmp_path, r'''
const assert = require('node:assert/strict');
function element() { return {dataset:{}, listeners:{}, innerHTML:'', open:false,
  setAttribute(key,val){this[key]=val;}, addEventListener(name,cb){this.listeners[name]=cb;},
  focus(){this.focused=true;}, showModal(){this.open=true;}, close(){this.open=false;this.listeners.close();}}; }
const ids = Object.fromEntries(['tradeDetailDialog','tradeDetailBody','tradeDetailTitle','tradeDetailClose'].map(id=>[id,element()]));
const opener = element();
const requests = [];
const controller = ui.createController({document:{getElementById:id=>ids[id], activeElement:opener},
  request:(url,opts)=>new Promise((resolve,reject)=>requests.push({url,opts,resolve,reject}))});
const response = (status,body) => ({status,ok:status===200,json:async()=>body});
(async()=>{
 let a = controller.loadTradeDetail('old/id',opener);
 assert.ok(requests[0].url.endsWith('old%2Fid'));
 assert.equal(ids.tradeDetailBody.dataset.state,'loading');
 assert.ok(ids.tradeDetailClose.focused);
 let b = controller.loadTradeDetail('new');
 assert.ok(requests[0].opts.signal.aborted);
 requests[1].resolve(response(200,{trade:{trade_id:'new'},events:[]})); await b;
 requests[0].resolve(response(200,{trade:{trade_id:'old'},events:[]})); await a;
 assert.ok(ids.tradeDetailBody.innerHTML.includes('new'));
 assert.ok(!ids.tradeDetailBody.innerHTML.includes('>old<'));
 assert.equal(ids.tradeDetailBody.dataset.state,'success');
 a=controller.loadTradeDetail('missing');requests[2].resolve(response(404,{}));await a;
 assert.equal(ids.tradeDetailBody.dataset.state,'not-found');
 a=controller.loadTradeDetail('empty');requests[3].resolve(response(200,{}));await a;
 assert.equal(ids.tradeDetailBody.dataset.state,'empty');
 a=controller.loadTradeDetail('network');requests[4].reject(new Error('private stack trace'));await a;
 assert.equal(ids.tradeDetailBody.dataset.state,'error');
 assert.ok(ids.tradeDetailBody.innerHTML.includes('data-retry-trade'));
 assert.ok(!ids.tradeDetailBody.innerHTML.includes('private stack'));
 ids.tradeDetailClose.listeners.click();assert.ok(opener.focused);
 assert.ok(requests[4].opts.signal.aborted);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_static_journal_wiring():
    root = Path(__file__).resolve().parents[1] / "web/static"
    html = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert '<dialog id="tradeDetailDialog"' in html
    assert 'aria-labelledby="tradeDetailTitle"' in html
    assert html.index('/static/journal.js') < html.index('/static/app.js')
    assert "JournalUI.createController({ request: apiFetch, document })" in app
    assert "tradeDetail.loadTradeDetail(button.dataset.tradeId, button)" in app
    assert "journalTab.click()" in app
    assert "Source trade unavailable: no stored ID." in app
    assert "data-proposal-id" in app
    assert "openAppliedLesson" in app
    assert "No applied lesson IDs were stored" in app
    assert '.journal-scroll { overflow-x: auto;' in css
    assert '@media (max-width: 850px)' in css
