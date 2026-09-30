"""Deterministic Chromium quality gate for the local Forex dashboard."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import uvicorn

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # The dedicated E2E job must fail, never skip, if the extra is absent.
    sync_playwright = None

from tradingagents.agents.schemas_forex import ForexAction, OrderType, SetupType
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord, ProposalStatus, TradeExitReason
from tradingagents.dataflows import config as config_module
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.learning.manager import ForexLearningManager
from tradingagents.learning.models import ForexLesson
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.mt5.models import (
    MT5AccountInfo,
    MT5ConnectionStatus,
    MT5Position,
    MT5SymbolInfo,
    MT5Tick,
)
from tradingagents.mt5.observer import MT5Observer
from web import forex_routes, server

pytestmark = pytest.mark.e2e
E2E_API_KEY = "e2e-dashboard-secret"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_backtests() -> None:
    common = {
        "run_type": "FOREX_BACKTEST",
        "status": "completed",
        "pair": "EURUSD",
        "timeframe": "H1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "deterministic_fixture",
        "validated_strategy_performance": False,
        "validation_status": "EXPLORATORY_ONLY",
        "notice": "Deterministic browser fixture; not validated strategy performance.",
        "validation_reasons": ["Seeded deterministic browser evidence."],
    }
    forex_routes._backtest_runs.update(
        {
            "bt_demo_e2e": {
                **common,
                "backtest_id": "bt_demo_e2e",
                "mode": "DEMO",
                "demo_mode": True,
                "result": {
                    "total_trades": 2,
                    "winning_trades": 1,
                    "losing_trades": 1,
                    "win_rate_pct": 50.0,
                    "profit_factor": 1.2,
                    "total_net_profit": 25.0,
                    "max_drawdown_pct": 0.4,
                },
            },
            "wf_e2e": {
                **common,
                "backtest_id": "wf_e2e",
                "mode": "WALK_FORWARD",
                "validation_report": {
                    "robustness_verdict": "INSUFFICIENT_SAMPLE",
                    "splits": [
                        {
                            "split_id": "split-1",
                            "development": {"start_time": "2025-01-01", "end_time": "2025-03-31"},
                            "out_of_sample": {"start_time": "2025-04-01", "end_time": "2025-04-30"},
                            "forward_demo": None,
                        }
                    ],
                },
            },
            "ablation_e2e": {
                **common,
                "backtest_id": "ablation_e2e",
                "mode": "HISTORICAL_AGENT_ABLATION",
                "result": {
                    "study_id": "ablation_e2e",
                    "pair": "USDJPY",
                    "timeframe": "H1",
                    "sample_size_warning": "No statistically meaningful winner.",
                    "selection_status": "DESCRIPTIVE_ONLY",
                    "best_observed_variant_name": "Technical Only",
                    "validation_reasons": ["Comparative historical evidence only."],
                    "variants": [{
                        "variant_id": "tech_only", "name": "Technical Only",
                        "description": "Fixture", "analyses_performed": 2,
                        "trade_count": 0, "win_rate_pct": 0.0, "profit_factor": 0.0,
                        "total_net_profit": 0.0, "estimated_llm_cost_usd": 0.01,
                        "latency_seconds": 0.1, "sample_size_adequate": False,
                    }],
                },
            },
        }
    )


def _deterministic_analysis_worker(run_id, request) -> None:
    report = {
        "run_id": run_id,
        "pair": request.pair,
        "signal": "LONG",
        "proposal": {
            "action": "LONG",
            "setup_type": "PULLBACK",
            "entry_price": 1.085,
            "stop_loss": 1.08,
            "take_profit_1": 1.095,
            "confidence": None,
            "trade_rationale_summary": "Deterministic structure confirmation.",
            "applied_lesson_ids": ["lsn_e2e"],
        },
        "risk_decision": {
            "decision": "APPROVE",
            "risk_checks_passed": ["Maximum loss bounded"],
            "executive_rationale": "Risk geometry accepted.",
        },
        "sizing": {"recommended_lot_size": 0.5},
        "context": {"technical": "Bullish market structure"},
        "research": {
            "technical": "Higher-low confirmation.",
            "macro": "Rate differential stable.",
            "news": "No high-impact conflict.",
        },
        "provenance": {"sources": ["deterministic_fixture"]},
        "report": "Deterministic E2E decision report.",
    }
    for event_name in (
        "preparing_data", "technical_analyst", "macro_analyst", "news_analyst", "risk_evaluator"
    ):
        forex_routes._forex_run_events[run_id].append(
            {"type": event_name, "data": {"status": "complete"}, "ts": time.time()}
        )
    forex_routes._forex_completed_reports[run_id] = report
    forex_routes._forex_runs[run_id].update(
        status="completed", signal="LONG", finished_at=datetime.now(timezone.utc).isoformat()
    )
    forex_routes._forex_run_events[run_id].append(
        {"type": "complete", "data": {"run_id": run_id, "status": "completed"}, "ts": time.time()}
    )


@pytest.fixture(scope="module")
def e2e_environment(tmp_path_factory):
    if sync_playwright is None:
        pytest.fail('Playwright is required. Install with `pip install -e ".[dev,e2e]"`.')

    root = tmp_path_factory.mktemp("browser-e2e")
    original_settings_path = config_module._RUNTIME_CONFIG_PATH
    config_module._RUNTIME_CONFIG_PATH = Path(root) / "runtime-settings.json"
    config_module.reset_runtime_settings()
    forex_routes.reset_forex_state()

    journal = ForexTradeJournal(db_path=Path(root) / "journal.db", auto_migrate=True)
    journal_manager = ForexJournalManager(journal=journal)
    learning_manager = ForexLearningManager(journal=journal)
    metrics_manager = ForexMetricsManager(journal=journal)

    proposal = ProposalRecord(
        proposal_id="prop_e2e",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.BUY_LIMIT,
        setup_type=SetupType.PULLBACK,
        timeframe="M15",
        entry_price=1.085,
        stop_loss=1.08,
        take_profit_1=1.095,
        risk_reward_ratio=2.0,
        confidence=72.0,
        status=ProposalStatus.PROPOSED,
        risk_decision={"decision": "APPROVE", "executive_rationale": "Fixture risk review."},
    )
    journal.save_proposal(proposal)
    trade = journal.record_trade_open(
        proposal_id=None,
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.085,
        stop_loss=1.08,
        take_profit=1.095,
        lots=0.5,
        confidence=72.0,
        open_time_utc=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    journal.record_event(
        "TRADE_OPENED", trade_id=trade.trade_id, proposal_id=proposal.proposal_id,
        description="Deterministic trade opened", source="E2E",
    )
    journal.record_trade_close(
        trade.trade_id,
        close_price=1.09,
        exit_reason=TradeExitReason.TAKE_PROFIT,
        reflection="Followed the plan and respected the structural stop.",
        metadata={"mfe_r": 1.4, "mae_r": -0.2, "timeframe": "M15", "setup": "PULLBACK"},
    )
    learning_manager.store.save_lesson(
        ForexLesson(
            lesson_id="lsn_e2e",
            source_trade_id=trade.trade_id,
            proposal_id=proposal.proposal_id,
            pair="EURUSD",
            timeframe="M15",
            setup="PULLBACK",
            direction="LONG",
            evidence_count=3,
            observation="Confirmation reduced adverse excursion.",
            root_cause="Earlier entries lacked confirmation.",
            actionable_rule="Require a closed confirmation candle.",
        )
    )

    connection = MagicMock()
    connection.get_status.return_value = MT5ConnectionStatus.DISCONNECTED
    connection.is_connected.return_value = False
    connection.server = None
    connection.login = None
    connection.terminal_path = None
    observer = MagicMock(spec=MT5Observer)
    observer.connection = connection
    observer.get_account_info.return_value = None
    observer.get_open_positions.return_value = []
    observer.get_pending_orders.return_value = []
    observer.get_deals.return_value = []
    observer.get_symbol_info.side_effect = lambda symbol: MT5SymbolInfo(
        name=symbol,
        canonical_symbol=symbol,
        digits=3 if symbol.endswith("JPY") else 5,
        point=0.001 if symbol.endswith("JPY") else 0.00001,
        pip_size=0.01 if symbol.endswith("JPY") else 0.0001,
        spread_points=12,
        spread_pips=1.2,
        bid=150.123 if symbol.endswith("JPY") else 1.085,
        ask=150.135 if symbol.endswith("JPY") else 1.08512,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    observer.get_current_tick.side_effect = lambda symbol: MT5Tick(
        symbol=symbol,
        time=datetime.now(timezone.utc),
        bid=150.123 if symbol.endswith("JPY") else 1.085,
        ask=150.135 if symbol.endswith("JPY") else 1.08512,
        spread_pips=1.2,
    )
    forex_routes.set_forex_dependencies(
        journal=journal,
        journal_manager=journal_manager,
        learning_manager=learning_manager,
        metrics_manager=metrics_manager,
        mt5_observer=observer,
    )
    _seed_backtests()
    original_worker = forex_routes._run_forex_analysis
    forex_routes._run_forex_analysis = _deterministic_analysis_worker
    original_key = server.DASHBOARD_API_KEY
    server.DASHBOARD_API_KEY = None

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if requests.get(f"{base_url}/api/config", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        raise RuntimeError("Dashboard server did not start in time")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield SimpleNamespace(
            browser=browser,
            base_url=base_url,
            observer=observer,
            connection=connection,
            trade_id=trade.trade_id,
            journal=journal,
        )
        browser.close()

    uvicorn_server.should_exit = True
    thread.join(timeout=10)
    forex_routes._run_forex_analysis = original_worker
    server.DASHBOARD_API_KEY = original_key
    forex_routes.reset_forex_state()
    config_module._RUNTIME_CONFIG_PATH = original_settings_path


@contextmanager
def quality_page(environment, *, allowed_failed_paths=()):
    context = environment.browser.new_context()
    page = context.new_page()
    page_errors: list[str] = []
    console_errors: list[str] = []
    failed_required_requests: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    def record_failed_response(response):
        if response.status < 500:
            return
        if any(response.url.endswith(path) for path in allowed_failed_paths):
            return
        failed_required_requests.append(f"{response.status} {response.url}")

    page.on("response", record_failed_response)
    page.goto(environment.base_url, wait_until="domcontentloaded")
    page.wait_for_function("document.querySelector('#provider')?.options.length > 1")
    try:
        yield page
    finally:
        assert page_errors == []
        assert failed_required_requests == []
        unexpected_console = [
            message for message in console_errors
            if not (allowed_failed_paths and message.startswith("Failed to load resource:"))
        ]
        assert unexpected_console == []
        context.close()


def test_dashboard_boot_authentication_and_secret_storage(e2e_environment):
    with quality_page(e2e_environment) as page:
        assert page.locator("#view-dashboard").is_visible()
        assert page.locator("#tab-dashboard").get_attribute("aria-selected") == "true"
        config = page.evaluate("fetch('/api/config').then(async r => ({status:r.status, body:await r.json()}))")
        assert config["status"] == 200
        assert config["body"]["auth_required"] is False

    server.DASHBOARD_API_KEY = E2E_API_KEY
    try:
        context = e2e_environment.browser.new_context()
        page = context.new_page()
        page.goto(e2e_environment.base_url, wait_until="domcontentloaded")
        page.wait_for_function("document.querySelector('#provider')?.options.length > 1")
        page.locator("#tab-analyze").click()
        page.locator("#apiKeyGroup").wait_for(state="visible")
        wrong = page.evaluate(
            "fetch('/api/forex/settings',{headers:{'X-API-Key':'wrong'}}).then(r=>r.status)"
        )
        assert wrong == 401
        assert page.locator("#apiKeyGroup").is_visible()
        page.locator("#apiKey").fill(E2E_API_KEY)
        authenticated = page.evaluate(
            "fetch('/api/auth/session',{method:'POST',headers:{'X-API-Key':document.querySelector('#apiKey').value}}).then(r=>r.status)"
        )
        assert authenticated == 200
        assert page.evaluate("fetch('/api/forex/settings').then(r=>r.status)") == 200
        assert E2E_API_KEY not in page.url
        assert E2E_API_KEY not in page.locator("body").inner_text()
        assert page.evaluate("Object.values(localStorage).concat(Object.values(sessionStorage))") == []
        assert "tradingagents_api_session" not in page.evaluate("document.cookie")
        context.close()
    finally:
        server.DASHBOARD_API_KEY = None


def test_analysis_form_sse_and_decision_report(e2e_environment):
    with quality_page(e2e_environment) as page:
        captured: list[dict] = []
        page.on(
            "request",
            lambda request: captured.append(request.post_data_json)
            if request.url.endswith("/api/forex/analyze") and request.method == "POST" else None,
        )
        page.locator("#tab-analyze").click()
        page.locator("#forexPair").select_option("USDJPY")
        page.locator("#forexTimeframe").select_option("M15")
        page.locator('[data-ctx-tf="W1"]').click()
        page.locator('[data-fxanalyst="news"]').click()
        mandatory = page.locator(".mandatory-stage")
        assert mandatory.count() == 2
        assert all(mandatory.nth(index).is_disabled() for index in range(mandatory.count()))
        assert page.locator("#btnRun").is_enabled()
        invalid = page.evaluate("[...document.querySelectorAll('#analysisForm :invalid')].map(element => element.id)")
        assert invalid == []
        page.locator("#analysisForm").evaluate("form => form.requestSubmit()")
        page.wait_for_timeout(1000)
        assert captured, page.locator("#toast").inner_text()
        page.locator("#progressPercent").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#progressPercent')?.textContent === '100%'")
        payload = captured[0]
        assert payload["pair"] == "USDJPY"
        assert payload["execution_timeframe"] == "M15"
        assert payload["context_timeframes"] == ["H4", "D1", "W1"]
        assert payload["analysts"] == ["forex_technical", "forex_macro"]
        report = page.locator("#reportContent").inner_text()
        assert "LONG" in report
        assert "Higher-low confirmation" in report
        assert "Rate differential stable" in report
        assert "No high-impact conflict" in report
        assert "RISK ENGINE: APPROVE" in report
        assert "Model confidence: Unavailable" in report
        assert "Model confidence: 0" not in report


def test_analysis_cancellation_is_terminal_in_browser(e2e_environment):
    def cancellable_worker(run_id, _request):
        deadline = time.time() + 5
        while time.time() < deadline and not forex_routes._forex_cancel_requested(run_id):
            time.sleep(0.02)

    original_worker = forex_routes._run_forex_analysis
    forex_routes._run_forex_analysis = cancellable_worker
    try:
        with quality_page(e2e_environment) as page:
            page.locator("#tab-analyze").click()
            page.locator("#analysisForm").evaluate("form => form.requestSubmit()")
            cancel = page.locator("#btnCancelAnalysis")
            cancel.wait_for(state="visible")
            cancel.click()
            page.get_by_text("Forex analysis cancelled", exact=True).wait_for()
            assert cancel.is_hidden()
            cancelled = page.evaluate(
                "fetch('/api/forex/runs').then(r=>r.json()).then(d=>d.runs.find(x=>x.status==='cancelled'))"
            )
            assert cancelled["status"] == "cancelled"
            assert cancelled["finished_at"]
    finally:
        forex_routes._run_forex_analysis = original_worker


def test_proposals_mt5_journal_and_learning_workflows(e2e_environment):
    with quality_page(e2e_environment, allowed_failed_paths=("/api/forex/mt5/account",)) as page:
        page.locator("#tab-proposals").click()
        page.locator("#proposalsTableContainer").get_by_text("prop_e2e").wait_for()
        page.locator("#proposalsTableContainer button", has_text="View").click()
        page.locator("#reportModalBody").get_by_text("Immutable Original Proposal").wait_for()
        page.get_by_role("button", name="Approve").click()
        page.locator("#reportModalBody").get_by_text("APPROVED", exact=True).wait_for()
        assert e2e_environment.journal.get_proposal("prop_e2e").status == ProposalStatus.APPROVED
        assert not any(call[0].startswith("place") for call in e2e_environment.observer.method_calls)
        page.locator("#reportModalClose").click()

        page.locator("#tab-mt5").click()
        assert "DISCONNECTED" in page.locator("#mt5ConnBadge").inner_text()
        assert page.locator('input[type="password"]#mt5Password').count() == 0
        account = MT5AccountInfo(
            login=654321, name="E2E", server="Fixture-Demo", currency="USD", leverage=100,
            balance=100000.0, equity=100250.0, profit=250.0, margin=1000.0,
            margin_free=99250.0, margin_level=10025.0,
        )
        positions = [
            MT5Position(ticket=1, time=datetime.now(timezone.utc), type=ForexAction.LONG, symbol="EURUSD", volume=0.5, price_open=1.08, sl=1.075, tp=1.09, price_current=1.085, profit=250.0),
            MT5Position(ticket=2, time=datetime.now(timezone.utc), type=ForexAction.SHORT, symbol="USDJPY", volume=0.2, price_open=150.123, sl=150.5, tp=149.5, price_current=150.001, profit=24.0),
        ]
        e2e_environment.connection.is_connected.return_value = True
        e2e_environment.connection.get_status.return_value = MT5ConnectionStatus.CONNECTED
        e2e_environment.connection.login = 654321
        e2e_environment.connection.server = "Fixture-Demo"
        e2e_environment.connection.get_account_info.return_value = account
        e2e_environment.connection.get_positions.return_value = positions
        e2e_environment.connection.get_orders.return_value = []
        e2e_environment.observer.get_account_info.return_value = account
        e2e_environment.observer.get_open_positions.return_value = positions
        page.locator("#btnMT5RefreshStatus").click()
        page.locator("#mt5Balance").get_by_text("$100,000.00").wait_for()
        positions_text = page.locator("#mt5PositionsContainer").inner_text()
        assert "BUY" in positions_text and "SELL" in positions_text
        assert "150.001" in positions_text and "1.08500" in positions_text

        page.locator("#tab-journal").click()
        page.locator("#journalTableContainer [data-trade-id]").click()
        detail = page.locator("#tradeDetailBody")
        detail.get_by_text("Deterministic trade opened", exact=True).wait_for()
        detail.get_by_text("Followed the plan", exact=False).first.wait_for()
        assert "Unavailable" in detail.inner_text()
        page.locator("#tradeDetailClose").click()

        page.locator("#tab-learning").click()
        page.locator("#lessonsSetupFilter").fill("PULLBACK")
        page.locator("#lessonsDirectionFilter").select_option("LONG")
        page.locator("#lessonsEvidenceFilter").select_option("3")
        page.locator("#lessonsTimeframeFilter").fill("M15")
        page.locator("#btnRefreshLessons").click()
        page.locator("#lessonsContainer").get_by_text("Require a closed confirmation candle.").wait_for()
        page.locator("#lessonsContainer [data-trade-id]").click()
        assert page.locator("#view-journal").is_visible()
        page.locator("#tradeDetailBody").get_by_text("Followed the plan").wait_for()


def test_truthful_forex_rendering_and_validation_contracts(e2e_environment):
    with quality_page(e2e_environment) as page:
        def proposals(route):
            route.fulfill(json={"proposals": [
                {"proposal_id": "jpy_no_trade", "pair": "USDJPY", "action": "NO_TRADE",
                 "entry_price": None, "stop_loss": 150.1234, "take_profit_1": 0,
                 "status": "PROPOSED", "timeframe": "H1", "setup_type": None,
                 "risk_reward_ratio": None, "suggested_risk_percent": None},
            ]})

        page.route("**/api/forex/proposals?*", proposals)
        page.route("**/api/forex/proposals/jpy_no_trade", lambda route: route.fulfill(json={
            "proposal": {"proposal_id": "jpy_no_trade", "pair": "USDJPY", "action": "NO_TRADE",
                         "entry_price": None, "stop_loss": 150.1234, "take_profit_1": 0,
                         "status": "PROPOSED"},
            "risk_review": {}, "lessons": [], "matched_execution": None,
        }))
        page.locator("#tab-proposals").click()
        page.locator("#btnRefreshProposals").click()
        row = page.locator("#proposalsTableContainer tr", has_text="jpy_no_trade")
        row.wait_for()
        text = row.inner_text()
        assert "150.123" in text
        assert "Unavailable" in text
        assert "0.000" in text
        assert "0.00000" not in text
        assert "neutral" in row.locator(".signal-badge").get_attribute("class")
        row.get_by_role("button", name="View").click()
        detail = page.locator("#reportModalBody")
        detail.get_by_text("Risk checks unavailable").wait_for()
        assert "Verified Risk:Reward" not in detail.inner_text()
        assert "Account equity protection" not in detail.inner_text()
        page.locator("#reportModalClose").click()

        page.locator("#tab-backtest").click()
        page.locator('[data-backtest-id="wf_e2e"]').click()
        page.get_by_text("split-1", exact=True).wait_for()
        assert "EURUSD" in page.locator("#backtestRunsContainer").inner_text()
        awaitable = page.locator("#btnRefreshBacktests")
        awaitable.click()
        page.locator('[data-backtest-id="ablation_e2e"]').click()
        page.get_by_text("Technical Only", exact=True).wait_for()
        assert "No statistically meaningful winner" in page.locator("#backtestRunsContainer").inner_text()


def test_performance_backtests_and_settings(e2e_environment):
    with quality_page(e2e_environment) as page:
        page.locator("#tab-performance").click()
        page.locator("#performanceOverallContainer").get_by_text("Total trades").wait_for()
        overall = page.locator("#performanceOverallContainer").inner_text()
        assert "WIN RATE" in overall and "PROFIT FACTOR" in overall
        assert page.locator("#sampleSizeWarning").is_visible()
        page.locator("#performanceSegmentSelect").select_option("by_direction")
        page.locator("#perfBreakdownContainer").get_by_text("LONG", exact=True).wait_for()

        page.locator("#tab-backtest").click()
        runs = page.locator("#backtestRunsContainer")
        runs.get_by_text("DEMO", exact=True).wait_for()
        runs.get_by_text("WALK_FORWARD", exact=True).wait_for()
        runs.get_by_text("wf_e2e").click()
        runs.get_by_text("Walk-forward splits (out-of-sample)").wait_for()
        assert "split-1" in runs.inner_text()

        with page.expect_response(lambda response: response.url.endswith("/api/forex/settings")):
            page.locator("#tab-settings").click()
        page.wait_for_function("document.querySelector('#settingProviderInput')?.options.length > 0")
        page.locator("#settingPair").fill("GBPUSD")
        page.locator("#settingTimeframe").select_option("M15")
        page.locator("#settingRiskPercent").fill("1.4")
        page.locator("#btnSaveSettings").click()
        page.locator("#settingsStatus").get_by_text("Saved and applied.").wait_for()
        persisted = page.evaluate("fetch('/api/forex/settings').then(r => r.json())")
        assert persisted["settings"]["forex_default_pair"] == "GBPUSD"
        assert persisted["settings"]["forex_default_execution_timeframe"] == "M15"
        assert persisted["settings"]["forex_default_risk_percent"] == 1.4
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.querySelector('#provider')?.options.length > 1")
        with page.expect_response(lambda response: response.url.endswith("/api/forex/settings")):
            page.locator("#tab-settings").click()
        page.wait_for_function("document.querySelector('#settingPair')?.value === 'GBPUSD'")
        assert page.locator("#settingPair").input_value() == "GBPUSD"
        assert page.locator("#settingTimeframe").input_value() == "M15"
        assert page.locator("#settingRiskPercent").input_value() == "1.4"
        page.locator("#btnResetSettings").click()
        page.locator("#settingsStatus").get_by_text("Defaults restored").wait_for()
        assert E2E_API_KEY not in page.locator("body").inner_text()
        assert page.evaluate("Object.values(localStorage).concat(Object.values(sessionStorage))") == []
