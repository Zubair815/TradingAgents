"""Comprehensive unit and integration test suite for FastAPI Forex Routes (Phase 20).

Validates:
1. Trade Journal & Lifecycle endpoints (list, get, manual-open, close, modify SL/TP, partial close, reflections, summary, timeline, performance).
2. Proposal submission, status updates, risk evaluation, and position sizing endpoints.
3. MT5 read-only observer endpoints (status, connect, disconnect, account, symbols, tick, positions, orders, deals).
4. Unavailable Forex analysis produces no fabricated signals, runs, or journal records.
5. Explicitly labeled demo backtesting with journal isolation (run, list, get).
6. Quantitative analytics endpoints (dashboard, deep metrics, Monte Carlo simulation, stop calibration, ablation study).
7. Post-trade learning endpoints (lessons query, automated reflection, contextual prompt retrieval).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.dataflows import config as config_module
from tradingagents.forex.conversion import AvailabilityStatus, ForexConversionRate
from tradingagents.graph.forex_graph import create_forex_risk_evaluator
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.mt5.errors import MT5DataError
from tradingagents.mt5.models import (
    MT5AccountInfo,
    MT5ConnectionStatus,
    MT5Position,
    MT5SymbolInfo,
    MT5Tick,
)
from tradingagents.mt5.observer import MT5Observer
from tradingagents.risk.engine import ForexRiskLimits
from tradingagents.risk.sizing import (
    ForexAccountProfile,
    OpenPosition,
    PositionSizingMethod,
    PositionSizingResult,
)
from web.forex_routes import (
    ForexAnalysisRequest,
    _forex_completed_reports,
    _forex_run_events,
    _forex_runs,
    _run_forex_analysis,
    reset_forex_state,
    set_forex_dependencies,
)
from web.server import app


@pytest.fixture(autouse=True)
def isolated_forex_env():
    """Ensure clean in-memory journal and dependencies for each test."""
    reset_forex_state()
    in_memory_journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    journal_mgr = ForexJournalManager(journal=in_memory_journal)

    # Mock MT5Observer with safe defaults
    mock_conn = MagicMock()
    mock_conn.get_status.return_value = MT5ConnectionStatus.DISCONNECTED
    mock_conn.is_connected.return_value = False
    mock_conn.terminal_path = None
    mock_conn.server = None
    mock_conn.login = None

    mock_observer = MagicMock(spec=MT5Observer)
    mock_observer.connection = mock_conn

    set_forex_dependencies(
        journal=in_memory_journal,
        journal_manager=journal_mgr,
        mt5_observer=mock_observer,
    )
    yield in_memory_journal, journal_mgr, mock_observer
    reset_forex_state()


@pytest.fixture
def client():
    c = TestClient(app)
    c.get("/")
    return c


@pytest.fixture
def isolated_runtime_settings(monkeypatch, tmp_path):
    settings_path = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(config_module, "_RUNTIME_CONFIG_PATH", settings_path)
    config_module.reset_runtime_settings()
    yield settings_path
    config_module.reset_runtime_settings()


class TestRuntimeSettingsRoutes:
    def test_settings_persist_reset_and_never_return_secrets(
        self, client, isolated_runtime_settings, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "SENTINEL_SECRET")
        response = client.patch("/api/forex/settings", json={
            "forex_default_pair": "gbp/usd",
            "forex_default_risk_percent": 1.4,
            "forex_min_rr": 2.1,
        })
        assert response.status_code == 200
        assert response.json()["settings"]["forex_default_pair"] == "GBPUSD"
        assert "SENTINEL_SECRET" not in response.text
        assert json.loads(isolated_runtime_settings.read_text())["forex_min_rr"] == 2.1

        fetched = client.get("/api/forex/settings")
        assert fetched.status_code == 200
        assert fetched.json()["execution_policy"] == "manual_only"
        assert fetched.json()["secret_status"]["llm_provider_secrets"] == "Configured"
        assert "SENTINEL_SECRET" not in fetched.text

        reset = client.post("/api/forex/settings/reset")
        assert reset.status_code == 200
        assert reset.json()["settings"]["forex_default_pair"] == "EURUSD"
        assert not isolated_runtime_settings.exists()

    @pytest.mark.parametrize("payload", [
        {"auto_order": True},
        {"mt5_password": "secret"},
        {"api_key": "secret"},
        {"forex_default_risk_percent": 0},
    ])
    def test_settings_reject_unknown_secret_and_invalid_fields(
        self, client, isolated_runtime_settings, payload
    ):
        response = client.patch("/api/forex/settings", json=payload)
        assert response.status_code == 400
        assert not isolated_runtime_settings.exists()

    def test_partial_invalid_settings_update_preserves_previous_file(
        self, client, isolated_runtime_settings
    ):
        assert client.patch("/api/forex/settings", json={
            "forex_default_risk_percent": 1.3,
        }).status_code == 200
        before = isolated_runtime_settings.read_text(encoding="utf-8")
        response = client.patch("/api/forex/settings", json={
            "forex_default_risk_percent": 2.0,
            "mt5_poll_interval_seconds": 0.01,
        })
        assert response.status_code == 400
        assert isolated_runtime_settings.read_text(encoding="utf-8") == before

    def test_poll_interval_change_reports_restart_for_existing_runtime(
        self, client, isolated_runtime_settings
    ):
        with patch("web.forex_routes._forex_runtime", object()):
            response = client.patch("/api/forex/settings", json={
                "mt5_poll_interval_seconds": 2.5,
            })
        assert response.status_code == 200
        assert response.json()["restart_required"] is True

    def test_analysis_uses_settings_only_when_request_values_are_omitted(
        self, isolated_runtime_settings
    ):
        config_module.save_runtime_settings({
            "forex_default_pair": "GBPUSD",
            "forex_default_execution_timeframe": "M15",
            "forex_default_context_timeframes": ["H1", "H4"],
            "forex_default_risk_percent": 1.7,
            "forex_min_rr": 2.2,
            "forex_max_spread_pips": 2.4,
        })
        defaults = ForexAnalysisRequest()
        explicit = ForexAnalysisRequest(pair="EURUSD", timeframe="H1", risk_percent=0.8, min_rr=1.8)

        assert defaults.pair == "GBPUSD"
        assert defaults.execution_timeframe == "M15"
        assert defaults.context_timeframes == ("H1", "H4")
        assert defaults.risk_percent == 1.7
        assert defaults.min_rr == 2.2
        assert defaults.max_spread_pips == 2.4
        assert explicit.pair == "EURUSD"
        assert explicit.execution_timeframe == "H1"
        assert explicit.risk_percent == 0.8
        assert explicit.min_rr == 1.8


# ---------------------------------------------------------------------------
# 1. Journal & Trade Lifecycle Tests
# ---------------------------------------------------------------------------

class TestJournalRoutes:
    def test_list_trades_empty(self, client):
        res = client.get("/api/forex/journal/trades")
        assert res.status_code == 200
        data = res.json()
        assert data["trades"] == []
        assert data["count"] == 0

    def test_manual_open_and_get_trade(self, client):
        open_payload = {
            "pair": "EURUSD",
            "action": "LONG",
            "entry_price": 1.0850,
            "lots": 1.5,
            "stop_loss": 1.0820,
            "take_profit": 1.0910,
            "actor": "TraderBob",
        }
        res = client.post("/api/forex/journal/trades/manual-open", json=open_payload)
        assert res.status_code == 200
        data = res.json()
        assert "trade_id" in data
        assert data["status"] == "OPEN"
        trade_id = data["trade_id"]

        # Fetch trade
        get_res = client.get(f"/api/forex/journal/trades/{trade_id}")
        assert get_res.status_code == 200
        trade_info = get_res.json()
        assert trade_info["trade"]["trade_id"] == trade_id
        assert trade_info["trade"]["pair"] == "EURUSD"
        assert trade_info["trade"]["action"] == "LONG"
        assert trade_info["trade"]["open_price"] == 1.0850
        assert trade_info["trade"]["lots"] == 1.5

    def test_get_trade_not_found(self, client):
        res = client.get("/api/forex/journal/trades/nonexistent_id")
        assert res.status_code == 404

    def test_close_trade_with_pnl_and_r_multiple(self, client):
        # Open trade first
        open_res = client.post(
            "/api/forex/journal/trades/manual-open",
            json={
                "pair": "EURUSD",
                "action": "LONG",
                "entry_price": 1.0800,
                "lots": 1.0,
                "stop_loss": 1.0780,  # 20 pips SL
                "take_profit": 1.0840,  # 40 pips TP (2.0R)
            },
        )
        trade_id = open_res.json()["trade_id"]

        # Close trade at target
        close_payload = {
            "close_price": 1.0840,
            "exit_reason": "TAKE_PROFIT",
            "commission": 5.0,
            "swap": 0.0,
        }
        close_res = client.post(f"/api/forex/journal/trades/{trade_id}/close", json=close_payload)
        assert close_res.status_code == 200
        settled = close_res.json()["trade"]
        assert settled["status"] == "CLOSED"
        assert settled["exit_reason"] == "TAKE_PROFIT"
        assert pytest.approx(settled["pips_gained"], rel=1e-2) == 40.0
        assert pytest.approx(settled["r_multiple"], rel=1e-2) == 2.0
        assert settled["net_profit"] > 0

    def test_modify_stop_loss_and_take_profit(self, client):
        open_res = client.post(
            "/api/forex/journal/trades/manual-open",
            json={
                "pair": "GBPUSD",
                "action": "LONG",
                "entry_price": 1.2500,
                "lots": 1.0,
                "stop_loss": 1.2460,
                "take_profit": 1.2580,
            },
        )
        trade_id = open_res.json()["trade_id"]

        # Modify SL to breakeven
        sl_res = client.post(
            f"/api/forex/journal/trades/{trade_id}/modify-sl",
            json={"new_stop_loss": 1.2500, "reason": "Moved to breakeven after TP1 reached"},
        )
        assert sl_res.status_code == 200
        assert sl_res.json()["new_stop_loss"] == 1.2500

        # Modify TP
        tp_res = client.post(
            f"/api/forex/journal/trades/{trade_id}/modify-tp",
            json={"new_take_profit": 1.2650, "reason": "Extended runner target"},
        )
        assert tp_res.status_code == 200
        assert tp_res.json()["new_take_profit"] == 1.2650

    def test_partial_close(self, client):
        open_res = client.post(
            "/api/forex/journal/trades/manual-open",
            json={
                "pair": "USDJPY",
                "action": "LONG",
                "entry_price": 150.00,
                "lots": 2.0,
                "stop_loss": 149.50,
                "take_profit": 151.00,
            },
        )
        trade_id = open_res.json()["trade_id"]

        part_res = client.post(
            f"/api/forex/journal/trades/{trade_id}/partial-close",
            json={"lots_to_close": 1.0, "close_price": 150.50, "exit_reason": "TAKE_PROFIT"},
        )
        assert part_res.status_code == 200
        data = part_res.json()
        assert data["status"] == "PARTIALLY_CLOSED"
        assert data["result"]["remaining_lots"] == 1.0

    def test_update_trade_reflection(self, client):
        open_res = client.post(
            "/api/forex/journal/trades/manual-open",
            json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0800, "lots": 1.0},
        )
        trade_id = open_res.json()["trade_id"]

        ref_res = client.post(
            f"/api/forex/journal/trades/{trade_id}/reflection",
            json={
                "reflection_text": "Clean momentum breakout following London session open.",
                "category_tag": "MOMENTUM_BREAKOUT",
                "execution_quality": "EXCELLENT",
            },
        )
        assert ref_res.status_code == 200
        trade_data = ref_res.json()["trade"]
        assert trade_data["reflection"] == "Clean momentum breakout following London session open."
        assert "MOMENTUM_BREAKOUT" in trade_data["tags"]

    def test_journal_summary_and_timeline(self, client):
        # Open and close two trades
        t1 = client.post(
            "/api/forex/journal/trades/manual-open",
            json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0800, "lots": 1.0, "stop_loss": 1.0780, "take_profit": 1.0840},
        ).json()["trade_id"]
        client.post(f"/api/forex/journal/trades/{t1}/close", json={"close_price": 1.0840, "exit_reason": "TAKE_PROFIT"})

        summary_res = client.get("/api/forex/journal/summary")
        assert summary_res.status_code == 200
        summary = summary_res.json()["summary"]
        assert summary["total_trades"] == 1
        assert summary["winning_trades"] == 1
        assert summary["win_rate"] == 100.0

        # Timeline
        tl_res = client.get(f"/api/forex/journal/timeline?trade_id={t1}&render_markdown=true")
        assert tl_res.status_code == 200
        tl_data = tl_res.json()
        assert len(tl_data["events"]) >= 1
        assert "markdown" in tl_data

    def test_journal_performance(self, client):
        perf_res = client.get("/api/forex/journal/performance?initial_capital=100000")
        assert perf_res.status_code == 200
        assert "performance" in perf_res.json()
        assert "markdown_dashboard" in perf_res.json()


# ---------------------------------------------------------------------------
# 2. Proposal & Risk Sizing Tests
# ---------------------------------------------------------------------------

class TestProposalRoutes:
    def test_create_and_list_proposals(self, client):
        payload = {
            "pair": "EURUSD",
            "action": "LONG",
            "order_type": "MARKET",
            "setup_type": "BREAKOUT",
            "timeframe": "H1",
            "entry_price": 1.0850,
            "stop_loss": 1.0820,
            "take_profit": 1.0910,
            "suggested_risk_percent": 1.5,
            "confluence_factors": ["London Breakout", "EMA Stack"],
            "reasoning": "Bullish momentum continuation above resistance",
        }
        res = client.post("/api/forex/proposals", json=payload)
        assert res.status_code == 200
        prop_id = res.json()["proposal_id"]
        assert prop_id.startswith("prop_")

        # Query proposal
        get_res = client.get(f"/api/forex/proposals/{prop_id}")
        assert get_res.status_code == 200
        assert get_res.json()["proposal"]["pair"] == "EURUSD"
        assert get_res.json()["proposal"]["status"] == "PROPOSED"

        # List proposals
        list_res = client.get("/api/forex/proposals?pair=EURUSD")
        assert list_res.status_code == 200
        assert list_res.json()["count"] >= 1

    def test_update_proposal_status(self, client):
        prop_res = client.post(
            "/api/forex/proposals",
            json={
                "pair": "AUDUSD",
                "action": "SHORT",
                "entry_price": 0.6550,
                "stop_loss": 0.6580,
                "take_profit": 0.6490,
            },
        )
        prop_id = prop_res.json()["proposal_id"]

        status_res = client.post(
            f"/api/forex/proposals/{prop_id}/status",
            json={"status": "APPROVED"},
        )
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "APPROVED"

    def test_update_proposal_status_user_actions_and_transitions(self, client):
        prop_res = client.post(
            "/api/forex/proposals",
            json={
                "pair": "EURUSD",
                "action": "LONG",
                "entry_price": 1.0850,
                "stop_loss": 1.0810,
                "take_profit": 1.0930,
            },
        )
        prop_id = prop_res.json()["proposal_id"]

        # Approve proposal
        res_app = client.post(f"/api/forex/proposals/{prop_id}/status", json={"status": "APPROVED"})
        assert res_app.status_code == 200

        # User action: WAIT
        res_wait = client.post(f"/api/forex/proposals/{prop_id}/status", json={"status": "WAIT", "reason": "Holding for NY"})
        assert res_wait.status_code == 200
        assert res_wait.json()["status"] == "WAITING_USER"

        # User action: EXECUTED
        res_exec = client.post(f"/api/forex/proposals/{prop_id}/status", json={"status": "EXECUTED", "reason": "Executed in MT5 manually"})
        assert res_exec.status_code == 200
        assert res_exec.json()["status"] == "EXECUTED"

        # Illegal transition from terminal state EXECUTED to APPROVED returns 400
        res_illegal = client.post(f"/api/forex/proposals/{prop_id}/status", json={"status": "APPROVED"})
        assert res_illegal.status_code == 400

    def test_list_proposals_advanced_filters(self, client):
        """Phase 28: Validate multi-field proposal filtering (pair, date, status, action, setup, timeframe)."""
        # Create Proposal 1: EURUSD, LONG, BREAKOUT, H1
        p1 = client.post(
            "/api/forex/proposals",
            json={
                "pair": "EURUSD",
                "action": "LONG",
                "order_type": "MARKET",
                "setup_type": "BREAKOUT",
                "timeframe": "H1",
                "entry_price": 1.0850,
                "stop_loss": 1.0820,
                "take_profit": 1.0910,
            },
        ).json()["proposal_id"]

        # Create Proposal 2: USDJPY, SHORT, LIQUIDITY_SWEEP, M15
        p2 = client.post(
            "/api/forex/proposals",
            json={
                "pair": "USDJPY",
                "action": "SHORT",
                "order_type": "LIMIT",
                "setup_type": "LIQUIDITY_SWEEP",
                "timeframe": "M15",
                "entry_price": 150.50,
                "stop_loss": 150.90,
                "take_profit": 149.70,
            },
        ).json()["proposal_id"]

        # 1. Filter by pair
        res_pair = client.get("/api/forex/proposals?pair=USDJPY")
        assert res_pair.status_code == 200
        ids_pair = [p["proposal_id"] for p in res_pair.json()["proposals"]]
        assert p2 in ids_pair
        assert p1 not in ids_pair

        # 2. Filter by action
        res_act = client.get("/api/forex/proposals?action=SHORT")
        assert res_act.status_code == 200
        assert all(p["action"] == "SHORT" for p in res_act.json()["proposals"])

        # 3. Filter by setup
        res_setup = client.get("/api/forex/proposals?setup=LIQUIDITY_SWEEP")
        assert res_setup.status_code == 200
        assert all(p["setup_type"] == "LIQUIDITY_SWEEP" for p in res_setup.json()["proposals"])

        # 4. Filter by timeframe
        res_tf = client.get("/api/forex/proposals?timeframe=M15")
        assert res_tf.status_code == 200
        assert all(p["timeframe"] == "M15" for p in res_tf.json()["proposals"])

        # 5. Combined filter
        res_comb = client.get("/api/forex/proposals?pair=EURUSD&action=LONG&setup=BREAKOUT&timeframe=H1")
        assert res_comb.status_code == 200
        ids_comb = [p["proposal_id"] for p in res_comb.json()["proposals"]]
        assert p1 in ids_comb
        assert p2 not in ids_comb

    def test_get_proposal_detail_complete_contract(self, client):
        """Phase 28: Validate complete proposal detail payload including immutable original proposal, risk review, user decision, matched execution, final outcome, and lessons."""
        prop_res = client.post(
            "/api/forex/proposals",
            json={
                "pair": "GBPUSD",
                "action": "LONG",
                "order_type": "LIMIT",
                "setup_type": "TREND_PULLBACK",
                "timeframe": "H4",
                "entry_price": 1.2800,
                "stop_loss": 1.2750,
                "take_profit": 1.2900,
                "suggested_risk_percent": 1.0,
                "invalidation_condition": "H4 close below 1.2740",
                "reasoning": "Bullish trend continuation test of H4 EMA 50.",
            },
        )
        assert prop_res.status_code == 200
        prop_id = prop_res.json()["proposal_id"]

        # 1. Update status to APPROVED
        app_res = client.post(f"/api/forex/proposals/{prop_id}/status", json={"status": "APPROVED"})
        assert app_res.status_code == 200

        # 2. Record matched execution trade in journal
        open_res = client.post(
            "/api/forex/journal/trades/manual-open",
            json={
                "pair": "GBPUSD",
                "action": "LONG",
                "entry_price": 1.2800,
                "lots": 1.5,
                "stop_loss": 1.2750,
                "take_profit": 1.2900,
                "proposal_id": prop_id,
                "notes": "Matched execution against approved proposal",
            },
        )
        assert open_res.status_code == 200
        trade_id = open_res.json()["trade_id"]

        # Close trade to produce final outcome
        close_res = client.post(
            f"/api/forex/journal/trades/{trade_id}/close",
            json={
                "close_price": 1.2900,
                "exit_reason": "TAKE_PROFIT",
                "reflection": "TP reached cleanly according to thesis",
            },
        )
        assert close_res.status_code == 200

        # 3. Retrieve proposal detail via GET /api/forex/proposals/{proposal_id}
        detail_res = client.get(f"/api/forex/proposals/{prop_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()

        # Check all Phase 28 contract elements
        assert "proposal" in detail
        assert "original_proposal" in detail
        assert detail["original_proposal"]["invalidation_condition"] == "H4 close below 1.2740"
        assert "risk_review" in detail
        assert "user_decision" in detail
        assert detail["user_decision"]["status"] == "EXECUTED"
        assert "matched_execution" in detail
        assert detail["matched_execution"]["trade_id"] == trade_id
        assert detail["matched_execution"]["lots"] == 1.5
        assert "final_outcome" in detail
        assert detail["final_outcome"]["status"] == "CLOSED"
        assert detail["final_outcome"]["exit_reason"] == "TAKE_PROFIT"
        assert detail["final_outcome"]["pips_gained"] > 0
        assert "lessons" in detail

    def test_evaluate_risk_endpoint(self, client):
        eval_payload = {
            "proposal": {
                "pair": "EURUSD",
                "action": "LONG",
                "order_type": "MARKET",
                "setup_type": "BREAKOUT",
                "timeframe": "H1",
                "entry_price": 1.0850,
                "stop_loss": 1.0820,   # 30 pips SL
                "take_profit_1": 1.0910,  # 60 pips TP (2.0R)
                "suggested_risk_percent": 1.0,
            },
            "account_balance": 100000.0,
            "account_currency": "USD",
            "spread_pips": 1.2,
            "current_time": "2026-09-22T10:00:00+00:00",  # Mid-week London session (calendar clear)
        }
        res = client.post("/api/forex/proposals/evaluate-risk", json=eval_payload)
        assert res.status_code == 200
        data = res.json()
        assert "decision" in data
        assert data["is_approved"] is False
        assert "DATA_INSUFFICIENT" in str(data)
        assert data["action"] == "REJECT"
        assert data["evaluation_kind"] == "MANUAL_RISK_EVALUATION"
        assert data["broker_verified"] is False

    def test_position_sizing_endpoint(self, client):
        sizing_payload = {
            "proposal": {
                "pair": "EURUSD",
                "action": "LONG",
                "order_type": "MARKET",
                "setup_type": "BREAKOUT",
                "timeframe": "H1",
                "entry_price": 1.0850,
                "stop_loss": 1.0820,   # 30 pips SL
                "take_profit_1": 1.0910,
            },
            "method": "FIXED_RISK_PERCENT",
            "account_balance": 100000.0,
            "account_equity": 100000.0,
            "account_free_margin": 100000.0,
            "account_currency": "USD",
            "risk_percent": 1.0,  # $1,000 risk
            "leverage": 100.0,
        }
        res = client.post("/api/forex/proposals/size", json=sizing_payload)
        assert res.status_code == 200
        sizing = res.json()["sizing"]
        assert res.json()["sizing_kind"] == "MANUAL_ESTIMATE"
        assert res.json()["broker_verified"] is False
        assert res.json()["broker_constraints_source"] == "STANDARD_FX_ESTIMATE"
        assert any("Standard FX estimate constraints" in item for item in res.json()["assumptions"])
        assert sizing["is_executable"] is True
        assert sizing["lots"] > 0
        assert sizing["margin_required"] > 0
        assert pytest.approx(sizing["risk_amount"], abs=5.0) == 1000.0

    def test_position_sizing_requires_complete_manual_account_data(self, client):
        res = client.post(
            "/api/forex/proposals/size",
            json={
                "proposal": {
                    "pair": "EURUSD",
                    "action": "LONG",
                    "entry_price": 1.0850,
                    "stop_loss": 1.0820,
                },
                "account_balance": 100000.0,
            },
        )
        assert res.status_code == 422

    def test_position_sizing_preserves_known_zero_free_margin(self, client):
        res = client.post(
            "/api/forex/proposals/size",
            json={
                "proposal": {
                    "pair": "EURUSD",
                    "action": "LONG",
                    "entry_price": 1.0850,
                    "stop_loss": 1.0820,
                },
                "account_balance": 100000.0,
                "account_equity": 90000.0,
                "account_free_margin": 0.0,
                "account_currency": "USD",
                "leverage": 100.0,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["sizing_kind"] == "MANUAL_ESTIMATE"
        assert data["sizing"]["is_executable"] is False

    def test_position_sizing_rejects_negative_free_margin(self, client):
        res = client.post(
            "/api/forex/proposals/size",
            json={
                "proposal": {"pair": "EURUSD", "action": "NO_TRADE"},
                "account_balance": 100000.0,
                "account_equity": 100000.0,
                "account_free_margin": -1.0,
                "account_currency": "USD",
                "leverage": 100.0,
            },
        )
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# 3. MT5 Read-Only Adapter Tests
# ---------------------------------------------------------------------------

class TestMT5Routes:
    def test_mt5_status_disconnected(self, client):
        res = client.get("/api/forex/mt5/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "DISCONNECTED"
        assert data["is_connected"] is False

    def test_mt5_connect_and_disconnect(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_observer.connection.connect.return_value = True
        mock_observer.connection.get_status.return_value = MT5ConnectionStatus.CONNECTED
        mock_observer.connection.is_connected.return_value = True

        conn_res = client.post("/api/forex/mt5/connect", json={"login": 123456, "server": "Broker-Demo"})
        assert conn_res.status_code == 200
        assert conn_res.json()["connected"] is True

        disc_res = client.post("/api/forex/mt5/disconnect")
        assert disc_res.status_code == 200
        assert disc_res.json()["status"] == "DISCONNECTED"

    def test_mt5_account_info(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_account = MT5AccountInfo(
            login=987654,
            name="Institutional Trader",
            server="LiveServer",
            currency="USD",
            leverage=100,
            balance=150000.0,
            equity=152500.0,
            profit=2500.0,
            margin=3500.0,
            margin_free=149000.0,
            margin_level=4357.14,
        )
        mock_observer.get_account_info.return_value = mock_account

        res = client.get("/api/forex/mt5/account")
        assert res.status_code == 200
        acct = res.json()["account"]
        assert acct["login"] == 987654
        assert acct["equity"] == 152500.0

    def test_mt5_account_unavailable_returns_503(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_observer.get_account_info.side_effect = MT5DataError("MT5 terminal IPC connection failed")

        res = client.get("/api/forex/mt5/account")
        assert res.status_code == 503

    def test_mt5_symbol_info_and_tick(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_sym = MT5SymbolInfo(
            name="EURUSD",
            canonical_symbol="EURUSD",
            digits=5,
            point=0.00001,
            pip_size=0.0001,
            spread_points=12,
            spread_pips=1.2,
            bid=1.08500,
            ask=1.08512,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        mock_observer.get_symbol_info.return_value = mock_sym

        sym_res = client.get("/api/forex/mt5/symbol/EURUSD")
        assert sym_res.status_code == 200
        assert sym_res.json()["symbol_info"]["spread_pips"] == 1.2

        mock_tick = MT5Tick(
            symbol="EURUSD",
            time=datetime.now(timezone.utc),
            bid=1.08500,
            ask=1.08512,
            spread_pips=1.2,
        )
        mock_observer.get_current_tick.return_value = mock_tick
        tick_res = client.get("/api/forex/mt5/tick/EURUSD")
        assert tick_res.status_code == 200
        assert tick_res.json()["tick"]["bid"] == 1.08500

    def test_mt5_positions_orders_deals(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_pos = MT5Position(
            ticket=10101,
            time=datetime.now(timezone.utc),
            type=ForexAction.LONG,
            symbol="EURUSD",
            volume=1.0,
            price_open=1.0820,
            sl=1.0790,
            tp=1.0880,
            price_current=1.0850,
            profit=300.0,
        )
        mock_observer.get_open_positions.return_value = [mock_pos]
        mock_observer.get_pending_orders.return_value = []
        mock_observer.get_deals.return_value = []

        pos_res = client.get("/api/forex/mt5/positions")
        assert pos_res.status_code == 200
        assert len(pos_res.json()["positions"]) == 1
        assert pos_res.json()["positions"][0]["ticket"] == 10101

        ord_res = client.get("/api/forex/mt5/orders")
        assert ord_res.status_code == 200
        assert ord_res.json()["count"] == 0

        deal_res = client.get("/api/forex/mt5/deals")
        assert deal_res.status_code == 200
        assert deal_res.json()["count"] == 0

        # Verify query parameters passed to get_deals
        deal_filtered = client.get("/api/forex/mt5/deals?position=10101&count=5")
        assert deal_filtered.status_code == 200
        mock_observer.get_deals.assert_called_with(date_from=None, date_to=None, position=10101, count=5)

    def test_mt5_status_contract_canonical_and_dual_keys(self, client, isolated_forex_env):
        """Verify canonical MT5 status and backward compatible dual keys."""
        # 1. Disconnected
        res_disc = client.get("/api/forex/mt5/status")
        assert res_disc.status_code == 200
        data_disc = res_disc.json()
        assert data_disc["status"] == "DISCONNECTED"
        assert data_disc["is_connected"] is False
        assert data_disc["connected"] is False
        assert data_disc["masked_login"] == "Not Set"

        # 2. Connected
        _, _, mock_observer = isolated_forex_env
        mock_observer.connection.is_connected.return_value = True
        mock_observer.connection.get_status.return_value = MT5ConnectionStatus.CONNECTED
        mock_observer.connection.login = 654321
        mock_observer.connection.server = "Demo-Server"
        mock_observer.connection.terminal_path = "C:/Program Files/MetaTrader 5/terminal64.exe"

        res_conn = client.get("/api/forex/mt5/status")
        assert res_conn.status_code == 200
        data_conn = res_conn.json()
        assert data_conn["status"] == "CONNECTED"
        assert data_conn["is_connected"] is True
        assert data_conn["connected"] is True
        assert data_conn["login"] == "654***"
        assert data_conn["account_login"] == "654***"
        assert "654321" not in res_conn.text
        assert data_conn["masked_login"] == "654***"
        assert data_conn["server"] == "Demo-Server"

    def test_mt5_account_contract_canonical_and_flat(self, client, isolated_forex_env):
        """Verify canonical nested account response and flat compatibility."""
        _, _, mock_observer = isolated_forex_env
        mock_account = MT5AccountInfo(
            login=112233,
            name="FX Test",
            server="DemoServer",
            currency="USD",
            leverage=200,
            balance=50000.0,
            equity=51200.0,
            profit=1200.0,
            margin=1000.0,
            margin_free=50200.0,
            margin_level=5120.0,
        )
        mock_observer.get_account_info.return_value = mock_account

        res = client.get("/api/forex/mt5/account")
        assert res.status_code == 200
        body = res.json()

        # Canonical nested account object
        assert "account" in body
        assert body["account"]["balance"] == 50000.0
        assert body["account"]["equity"] == 51200.0
        assert body["account"]["profit"] == 1200.0
        assert body["account"]["margin"] == 1000.0
        assert body["account"]["margin_free"] == 50200.0
        assert body["account"]["margin_level"] == 5120.0

        # Flat root compatibility
        assert body["balance"] == 50000.0
        assert body["equity"] == 51200.0
        assert body["profit"] == 1200.0
        assert body["margin_free"] == 50200.0

    def test_mt5_positions_directions_and_symbols(self, client, isolated_forex_env):
        """Verify positions response format for LONG (EURUSD) and SHORT (USDJPY)."""
        _, _, mock_observer = isolated_forex_env
        pos_eur = MT5Position(
            ticket=20001,
            time=datetime.now(timezone.utc),
            type=ForexAction.LONG,
            symbol="EURUSD",
            volume=0.5,
            price_open=1.08500,
            sl=1.08200,
            tp=1.09100,
            price_current=1.08750,
            profit=125.0,
        )
        pos_jpy = MT5Position(
            ticket=20002,
            time=datetime.now(timezone.utc),
            type=ForexAction.SHORT,
            symbol="USDJPY",
            volume=1.0,
            price_open=152.500,
            sl=153.200,
            tp=151.000,
            price_current=152.100,
            profit=262.98,
        )
        mock_observer.get_open_positions.return_value = [pos_eur, pos_jpy]

        res = client.get("/api/forex/mt5/positions")
        assert res.status_code == 200
        positions = res.json()["positions"]
        assert len(positions) == 2

        # EURUSD LONG position
        p1 = positions[0]
        assert p1["ticket"] == 20001
        assert p1["symbol"] == "EURUSD"
        assert p1["type"] == "LONG"
        assert p1["price_open"] == 1.08500

        # USDJPY SHORT position
        p2 = positions[1]
        assert p2["ticket"] == 20002
        assert p2["symbol"] == "USDJPY"
        assert p2["type"] == "SHORT"
        assert p2["price_open"] == 152.500



# ---------------------------------------------------------------------------
# 4. Forex Analysis Run & SSE Streaming Tests
# ---------------------------------------------------------------------------

class TestForexAnalysisRuns:
    @pytest.mark.parametrize("missing", ["state", "proposal", "risk"])
    def test_incomplete_worker_result_never_completes(self, client, missing):
        run_id = f"fx_incomplete_{missing}"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []
        proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
        decision = ForexRiskDecision(
            pair="EURUSD", decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.NO_TRADE, approved_action=ForexAction.NO_TRADE,
            executive_rationale="Preserve capital",
        )
        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            graph = factory.return_value
            graph.stream.return_value = iter([])
            graph.get_state.return_value = {} if missing == "state" else {"final_trade_decision": "NO_TRADE"}
            graph.get_last_proposal.return_value = None if missing == "proposal" else proposal
            graph.get_last_risk_decision.return_value = None if missing == "risk" else decision
            graph.get_last_sizing_result.return_value = None
            graph.process_signal.return_value = "NO_TRADE"
            _run_forex_analysis(
                run_id,
                ForexAnalysisRequest(
                    pair="EURUSD",
                    account_source="manual",
                    account_balance=100000,
                    account_equity=100000,
                    account_free_margin=100000,
                    account_leverage=100,
                    account_currency="USD",
                ),
            )
            graph.save_reports.assert_not_called()
        assert _forex_runs[run_id]["status"] == "failed"
        assert _forex_runs[run_id]["signal"] is None
        assert run_id not in _forex_completed_reports
        assert [event["type"] for event in _forex_run_events[run_id]] == ["preparing_data", "error"]

    def test_worker_does_not_invent_or_borrow_proposal_identifier(self, client, isolated_forex_env):
        journal, _, _ = isolated_forex_env
        unrelated = journal.save_proposal(ForexTraderProposal(pair="EURUSD", action=ForexAction.LONG, reasoning="Unrelated proposal"))
        run_id = "fx_no_persisted_identifier"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []
        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            graph = factory.return_value
            graph.stream.return_value = iter([])
            graph.get_state.return_value = {"final_trade_decision": "NO_TRADE"}
            graph.get_last_proposal.return_value = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
            graph.get_last_risk_decision.return_value = ForexRiskDecision(
                pair="EURUSD", decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.NO_TRADE, approved_action=ForexAction.NO_TRADE,
                executive_rationale="Preserve capital",
            )
            graph.get_last_sizing_result.return_value = None
            graph.process_signal.return_value = "NO_TRADE"
            graph.save_reports.return_value = None
            _run_forex_analysis(
                run_id,
                ForexAnalysisRequest(
                    pair="EURUSD",
                    account_source="manual",
                    account_balance=100000,
                    account_equity=100000,
                    account_free_margin=100000,
                    account_leverage=100,
                    account_currency="USD",
                ),
            )
        assert _forex_runs[run_id]["status"] == "completed"
        assert _forex_completed_reports[run_id]["proposal_id"] is None
        assert _forex_runs[run_id]["proposal_id"] != unrelated
        assert journal.list_trades() == []

    def test_start_analysis_endpoint_queues_run_and_lists_it(self, client):
        req = {
            "pair": "EURUSD",
            "timeframe": "M15",
            "account_balance": 100000.0,
            "risk_percent": 1.0,
        }
        res = client.post("/api/forex/analyze", json=req)
        assert res.status_code == 200
        data = res.json()
        assert "run_id" in data
        assert data["status"] == "queued"
        assert data["pair"] == "EURUSD"
        assert data["timeframe"] == "M15"

        run_id = data["run_id"]
        assert data["asset_type"] == "forex"
        assert data["run_type"] == "forex"

        # Verify listing
        runs_res = client.get("/api/forex/runs")
        assert runs_res.status_code == 200
        run_items = runs_res.json()["runs"]
        matched = next(r for r in run_items if r["run_id"] == run_id)
        assert matched["asset_type"] == "forex"
        assert matched["run_type"] == "forex"

        # Verify get run detail with run and report keys
        get_res = client.get(f"/api/forex/runs/{run_id}")
        assert get_res.status_code == 200
        body = get_res.json()
        assert "run" in body
        assert "report" in body
        run_detail = body["run"]
        assert run_detail["run_id"] == run_id
        assert run_detail["pair"] == "EURUSD"
        assert run_detail["asset_type"] == "forex"

    def test_analysis_rejects_invalid_account_source(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "EURUSD", "account_source": "paper"},
        )
        assert res.status_code == 422

    def test_manual_analysis_requires_complete_account_data(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={
                "pair": "EURUSD",
                "account_source": "manual",
                "account_balance": 100000.0,
            },
        )
        assert res.status_code == 422

    def test_manual_analysis_accepts_explicit_zero_free_margin(self):
        request = ForexAnalysisRequest(
            pair="EURUSD",
            account_source="manual",
            account_balance=100000.0,
            account_equity=90000.0,
            account_free_margin=0.0,
            account_leverage=50.0,
            account_currency="USD",
        )
        assert request.account_free_margin == 0.0

    def test_canonical_routes_and_compatibility_aliases(self, client):
        """Verify canonical endpoints /runs/{id}, /events and compatibility aliases."""
        run_id = "fx_alias_test_789"
        _forex_runs[run_id] = {
            "run_id": run_id,
            "run_type": "forex",
            "asset_type": "forex",
            "pair": "GBPUSD",
            "timeframe": "H1",
            "date": None,
            "status": "completed",
            "provider": "",
            "quick_model": "",
            "deep_model": "",
            "account_balance": 50000.0,
            "risk_percent": 1.0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
            "signal": "SHORT",
            "proposal_id": "prop_test",
            "report_id": "rep_test",
            "report_path": None,
        }
        _forex_run_events[run_id] = [
            {"type": "preparing_data", "data": {"status": "ok"}, "ts": 1.0},
            {"type": "complete", "data": {"signal": "SHORT"}, "ts": 2.0},
        ]
        _forex_completed_reports[run_id] = {
            "run_id": run_id,
            "signal": "SHORT",
            "pair": "GBPUSD",
            "report": "Mock short report",
        }

        # Canonical SSE /runs/{id}/events
        events_res = client.get(f"/api/forex/runs/{run_id}/events")
        assert events_res.status_code == 200
        assert "event: preparing_data" in events_res.text
        assert "event: complete" in events_res.text

        # Compatibility alias /analyze/{id}/stream
        stream_res = client.get(f"/api/forex/analyze/{run_id}/stream")
        assert stream_res.status_code == 200
        assert "event: complete" in stream_res.text

        # Compatibility alias /analyze/{id}/report
        rep_res = client.get(f"/api/forex/analyze/{run_id}/report")
        assert rep_res.status_code == 200
        assert rep_res.json()["signal"] == "SHORT"

        # Compatibility alias /analyze/{id}/status
        stat_res = client.get(f"/api/forex/analyze/{run_id}/status")
        assert stat_res.status_code == 200
        assert stat_res.json()["status"] == "completed"

    def test_invalid_run_returns_404_on_all_endpoints(self, client):
        """Verify 404 behavior for non-existent runs across canonical and alias routes."""
        invalid_id = "fx_nonexistent_999"

        # Canonical get run
        res = client.get(f"/api/forex/runs/{invalid_id}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Run not found"

        # Canonical events
        res = client.get(f"/api/forex/runs/{invalid_id}/events")
        assert res.status_code == 404

        # Aliases
        assert client.get(f"/api/forex/analyze/{invalid_id}/stream").status_code == 404
        assert client.get(f"/api/forex/analyze/{invalid_id}/report").status_code == 404
        assert client.get(f"/api/forex/analyze/{invalid_id}/status").status_code == 404

    def test_start_analysis_phase26_parameters(self, client):
        req = {
            "pair": "EURUSD",
            "execution_timeframe": "H1",
            "context_timeframes": ["H4", "D1", "W1"],
            "date": "2026-03-04",
            "analysts": ["forex_technical", "forex_macro"],
            "account_balance": 150000.0,
            "risk_percent": 1.5,
            "research_depth": "comprehensive",
            "account_source": "manual",
            "account_equity": 149000.0,
            "account_free_margin": 125000.0,
            "account_leverage": 50.0,
            "account_currency": "USD",
            "min_rr": 2.0,
            "max_spread_pips": 1.8,
            "economic_blackout": False,
        }
        res = client.post("/api/forex/analyze", json=req)
        assert res.status_code == 200
        data = res.json()
        run_id = data["run_id"]
        assert data["execution_timeframe"] == "H1"
        assert data["context_timeframes"] == ["H4", "D1", "W1"]

        run_entry = _forex_runs[run_id]
        assert run_entry["research_depth"] == "comprehensive"
        assert run_entry["min_rr"] == 2.0
        assert run_entry["max_spread_pips"] == 1.8
        assert run_entry["economic_blackout"] is False
        assert run_entry["account_source"] == "manual"

    def test_run_forex_analysis_applies_phase26_risk_and_live_mt5_account(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_conn = mock_observer.connection
        mock_conn.is_connected.return_value = True
        mock_conn.get_status.return_value = MT5ConnectionStatus.CONNECTED
        mock_acc = MagicMock()
        mock_acc.balance = 87654.0
        mock_acc.equity = 87900.0
        mock_acc.margin = 150.0
        mock_acc.margin_free = 87750.0
        mock_acc.currency = "USD"
        mock_acc.leverage = 100
        mock_conn.get_account_info.return_value = mock_acc
        observed_at = datetime.now(timezone.utc)
        mock_observer.to_sizing_account_profile.return_value = ForexAccountProfile(
            balance=87654.0,
            equity=87900.0,
            used_margin=150.0,
            free_margin=87750.0,
            currency="USD",
            leverage=100.0,
            margin_call_level=85.0,
            stop_out_level=40.0,
        )
        mock_observer.get_current_tick.return_value = MT5Tick(
            time=observed_at,
            bid=1.0843,
            ask=1.0850,
            spread_pips=7.0,
            source="MT5",
            broker_symbol="EURUSD.raw",
        )
        mock_observer.to_broker_constraints.return_value = MT5SymbolInfo(
            name="EURUSD.raw",
            canonical_symbol="EURUSD",
            digits=5,
            point=0.00001,
            pip_size=0.0001,
            volume_min=0.03,
            volume_max=17.0,
            volume_step=0.03,
            contract_size=1000.0,
        ).to_broker_constraints()
        mock_observer.get_atr_pips.return_value = 20.0
        mock_observer.get_conversion_observations.return_value = ()
        day_start = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        mock_observer.get_daily_realized_pnl.return_value = (-100.0, day_start)
        mock_observer.to_open_positions.return_value = [
            OpenPosition(
                position_id="live-1",
                pair="GBPUSD",
                action=ForexAction.SHORT,
                lots=0.3,
                entry_price=1.25,
                stop_loss=1.26,
                risk_amount=300.0,
            )
        ]

        run_id = "fx_phase26_worker_test"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []

        req = ForexAnalysisRequest(
            pair="EURUSD",
            execution_timeframe="H1",
            context_timeframes=["H4", "D1"],
            account_source="mt5",
            account_balance=50000.0,
            risk_percent=1.2,
            min_rr=2.2,
            max_spread_pips=1.9,
            economic_blackout=True,
            research_depth="comprehensive",
            max_daily_loss_percent=2.0,
        )

        proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
        decision = ForexRiskDecision(
            pair="EURUSD", decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.NO_TRADE, approved_action=ForexAction.NO_TRADE,
            executive_rationale="Preserve capital",
        )

        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            graph = factory.return_value
            graph.stream.return_value = iter([])
            graph.get_state.return_value = {"final_trade_decision": "NO_TRADE"}
            graph.get_last_proposal.return_value = proposal
            graph.get_last_risk_decision.return_value = decision
            graph.get_last_sizing_result.return_value = None
            graph.process_signal.return_value = "NO_TRADE"
            graph.save_reports.return_value = None

            _run_forex_analysis(run_id, req)

            call_kwargs = factory.call_args[1]
            assert call_kwargs["sizing_account"].balance == 87654.0
            assert call_kwargs["sizing_account"].equity == 87900.0
            assert call_kwargs["sizing_account"].used_margin == 150.0
            assert call_kwargs["sizing_account"].free_margin == 87750.0
            context = call_kwargs["risk_context"]
            assert graph.stream.call_args.kwargs["trade_date"] == context.as_of_utc.isoformat()
            assert context.account.margin_call_level == 85.0
            assert context.market.spread_pips == 7.0
            assert context.market.atr_pips == 20.0
            assert context.broker.broker_symbol == "EURUSD.raw"
            assert context.broker.min_volume == 0.03
            assert context.broker.max_volume == 17.0
            assert context.broker.volume_step == 0.03
            assert context.portfolio.open_positions[0].position_id == "live-1"
            assert context.portfolio.realized_pnl_today == -100.0
            assert context.portfolio.trading_day_start_utc == day_start
            assert call_kwargs["risk_limits"].min_risk_reward_ratio == 2.2
            assert call_kwargs["risk_limits"].max_spread_pips == 1.9
            assert call_kwargs["risk_limits"].enforce_news_blackout is True
            assert call_kwargs["config"]["research_depth"] == "comprehensive"

            holder = {}
            evaluator = create_forex_risk_evaluator(
                risk_context=context,
                risk_limits=ForexRiskLimits(
                    enforce_market_open=False,
                    enforce_news_blackout=False,
                    max_spread_pips=2.5,
                ),
                sizing_result_holder=holder,
            )
            live_proposal = ForexTraderProposal(
                pair="EURUSD",
                action=ForexAction.LONG,
                entry_price=1.0850,
                stop_loss=1.0800,
                take_profit_1=1.0950,
                suggested_risk_percent=1.0,
                reasoning="Live-context propagation regression",
            )
            evaluated = evaluator(
                {
                    "company_of_interest": "EURUSD",
                    "trade_date": observed_at.date().isoformat(),
                    "forex_proposal": live_proposal.model_dump(),
                }
            )
            violations = evaluated["forex_risk_decision"]["risk_violations"]
            assert any("Current spread (7.0 pips)" in item for item in violations)
            assert any("Spread-to-ATR ratio" in item for item in violations)
            assert holder["latest"].recommended_lot_size % 0.03 == pytest.approx(0.0)

    def test_live_usdjpy_conversion_reaches_sizing(self, isolated_forex_env):
        _, _, observer = isolated_forex_env
        observer.connection.is_connected.return_value = True
        observed_at = datetime.now(timezone.utc)
        observer.to_sizing_account_profile.return_value = ForexAccountProfile(
            balance=20_000,
            equity=20_000,
            free_margin=20_000,
            currency="USD",
            leverage=100,
        )
        observer.get_current_tick.return_value = MT5Tick(
            time=observed_at,
            bid=149.99,
            ask=150.01,
            spread_pips=2.0,
            source="MT5",
            broker_symbol="USDJPY.raw",
        )
        observer.to_broker_constraints.return_value = MT5SymbolInfo(
            name="USDJPY.raw",
            canonical_symbol="USDJPY",
            digits=3,
            point=0.001,
            pip_size=0.01,
            volume_min=0.01,
            volume_max=50,
            volume_step=0.01,
        ).to_broker_constraints()
        observer.get_atr_pips.return_value = 60.0
        observer.get_conversion_observations.return_value = (
            ForexConversionRate(
                from_currency="USD",
                to_currency="JPY",
                status=AvailabilityStatus.AVAILABLE,
                rate=150.0,
                conversion_path=("USDJPY.raw",),
                source="MT5",
                observed_at=observed_at,
            ),
        )
        observer.to_open_positions.return_value = []

        run_id = "fx_phase3_usdjpy"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []
        req = ForexAnalysisRequest(
            pair="USDJPY",
            execution_timeframe="H1",
            account_source="mt5",
            economic_blackout=False,
        )
        proposal = ForexTraderProposal(pair="USDJPY", action=ForexAction.NO_TRADE, reasoning="No setup")
        decision = ForexRiskDecision(
            pair="USDJPY",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.NO_TRADE,
            approved_action=ForexAction.NO_TRADE,
            executive_rationale="Preserve capital",
        )
        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            graph = factory.return_value
            graph.stream.return_value = iter([])
            graph.get_state.return_value = {"final_trade_decision": "NO_TRADE"}
            graph.get_last_proposal.return_value = proposal
            graph.get_last_risk_decision.return_value = decision
            graph.get_last_sizing_result.return_value = None
            graph.process_signal.return_value = "NO_TRADE"
            graph.save_reports.return_value = None
            _run_forex_analysis(run_id, req)

            context = factory.call_args.kwargs["risk_context"]
            assert context.conversions[0].conversion_path == ("USDJPY.raw",)
            holder = {}
            evaluator = create_forex_risk_evaluator(
                risk_context=context,
                risk_limits=ForexRiskLimits(enforce_market_open=False, enforce_news_blackout=False),
                sizing_result_holder=holder,
            )
            executable = ForexTraderProposal(
                pair="USDJPY",
                action=ForexAction.LONG,
                entry_price=150.0,
                stop_loss=149.5,
                take_profit_1=151.0,
                suggested_risk_percent=1.0,
                reasoning="USDJPY conversion propagation",
            )
            evaluator(
                {
                    "company_of_interest": "USDJPY",
                    "trade_date": observed_at.date().isoformat(),
                    "forex_proposal": executable.model_dump(),
                }
            )
            assert holder["latest"].pip_value_per_lot == pytest.approx(6.67, abs=0.01)

    def test_explicit_disconnected_mt5_fails_without_manual_fallback(self, isolated_forex_env):
        _, _, observer = isolated_forex_env
        observer.connection.is_connected.return_value = False
        run_id = "fx_phase3_disconnected"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []

        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            _run_forex_analysis(
                run_id,
                ForexAnalysisRequest(pair="EURUSD", account_source="mt5"),
            )
            factory.assert_not_called()

        assert _forex_runs[run_id]["status"] == "failed"
        assert _forex_runs[run_id]["signal"] is None

    def test_omitted_account_source_defaults_to_mt5_without_balance_fallback(
        self, isolated_forex_env
    ):
        _, _, observer = isolated_forex_env
        observer.connection.is_connected.return_value = False
        request = ForexAnalysisRequest(pair="EURUSD")
        assert request.account_source == "mt5"
        assert request.account_balance is None

        run_id = "fx_phase5_default_mt5_disconnected"
        _forex_runs[run_id] = {"status": "queued", "signal": None}
        _forex_run_events[run_id] = []
        with patch("web.forex_routes.ForexTradingAgentsGraph") as factory:
            _run_forex_analysis(run_id, request)
            factory.assert_not_called()

        assert _forex_runs[run_id]["status"] == "failed"
        assert "not connected" in _forex_runs[run_id]["error"].lower()

    def test_run_forex_analysis_worker_and_sse_events(self, client, tmp_path):
        run_id = "fx_test_worker_123"
        req = ForexAnalysisRequest(
            pair="EURUSD",
            timeframe="M15",
            date="2026-03-04",
            analysts=["forex_technical", "forex_macro", "forex_news"],
            account_balance=100000.0,
            account_source="manual",
            account_equity=100000.0,
            account_free_margin=100000.0,
            account_leverage=100.0,
            account_currency="USD",
            risk_percent=1.0,
        )

        _forex_runs[run_id] = {
            "run_id": run_id,
            "pair": "EURUSD",
            "timeframe": "M15",
            "date": "2026-03-04",
            "status": "queued",
            "provider": "",
            "quick_model": "",
            "deep_model": "",
            "account_balance": 100000.0,
            "risk_percent": 1.0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "signal": None,
            "proposal_id": None,
            "report_id": None,
            "report_path": None,
        }
        _forex_run_events[run_id] = []

        mock_proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.MARKET,
            setup_type=SetupType.BREAKOUT,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit_1=1.0910,
            risk_reward_ratio=2.0,
            confidence_score=0.85,
            reasoning="Strong institutional trend alignment",
        )
        object.__setattr__(mock_proposal, "proposal_id", "prop_mock_123")
        mock_decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            approved_lot_size=1.5,
            stop_loss_pips=30.0,
            risk_amount_usd=450.0,
            risk_percent=0.45,
            risk_reward_ratio=2.0,
            executive_rationale="Approved trade setup within risk boundaries",
        )
        mock_sizing = PositionSizingResult(
            pair="EURUSD",
            action=ForexAction.LONG,
            sizing_method=PositionSizingMethod.FIXED_RISK_PERCENT,
            recommended_lot_size=1.5,
            raw_lot_size=1.5,
            units=150000.0,
            risk_amount=450.0,
            risk_percent=0.45,
            pip_value_per_lot=10.0,
            total_pip_value=15.0,
            stop_distance_pips=30.0,
            margin_required=1500.0,
            free_margin_remaining=98500.0,
            margin_level_percent=6666.67,
            leverage_used=1.5,
            is_executable=True,
        )

        stream_chunks = [
            {"forex_technical_report": "Bullish trend on H1 and H4."},
            {"forex_macro_report": "Fed neutral, ECB dovish."},
            {"forex_news_report": "No high-impact economic news in 2 hours."},
            {
                "investment_debate_state": {
                    "bull_history": "Bull argues strong momentum.",
                    "bear_history": "Bear counters near resistance.",
                    "judge_decision": "Consensus: Buy dips.",
                }
            },
            {
                "trader_investment_plan": "Proposing LONG at 1.0850",
                "forex_proposal": mock_proposal.model_dump(),
            },
            {
                "forex_risk_decision": mock_decision.model_dump(),
                "final_trade_decision": "Approved LONG execution",
            },
        ]

        report_dir = tmp_path / "mock_reports" / "EURUSD_20260304"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "complete_report.md"
        report_file.write_text("Mock complete report", encoding="utf-8")

        with patch("web.forex_routes.ForexTradingAgentsGraph") as mock_graph_cls:
            mock_graph = MagicMock()
            mock_graph.stream.return_value = iter(stream_chunks)
            mock_graph.get_state.return_value = {
                "forex_technical_report": "Bullish trend on H1 and H4.",
                "forex_macro_report": "Fed neutral, ECB dovish.",
                "forex_news_report": "No high-impact economic news in 2 hours.",
                "investment_debate_state": {"judge_decision": "Consensus: Buy dips."},
                "trader_investment_plan": "Proposing LONG at 1.0850",
                "final_trade_decision": "Approved LONG execution",
            }
            mock_graph.process_signal.return_value = "LONG"
            mock_graph.get_last_proposal.return_value = mock_proposal
            mock_graph.get_last_risk_decision.return_value = mock_decision
            mock_graph.get_last_sizing_result.return_value = mock_sizing
            mock_graph.save_reports.return_value = report_file

            mock_graph_cls.return_value = mock_graph

            _run_forex_analysis(run_id, req)

        # Check run state was updated to completed
        run_record = _forex_runs[run_id]
        assert run_record["status"] == "completed"
        assert run_record["signal"] == "LONG"
        assert run_record["proposal_id"] == mock_proposal.proposal_id
        assert run_record["finished_at"] is not None

        # Check completed report was stored
        assert run_id in _forex_completed_reports
        rep = _forex_completed_reports[run_id]
        assert rep["signal"] == "LONG"
        assert rep["proposal"]["action"] == "LONG"
        assert rep["risk_decision"]["decision"] == "APPROVE"
        assert rep["sizing"]["recommended_lot_size"] == 1.5
        assert rep["context"]["execution_timeframe"] == "M15"
        assert rep["context"]["context_timeframes"] == ["H1", "H4", "D1"]
        assert rep["context"]["news_risk"] is None
        assert rep["research"]["technical"] == "Bullish trend on H1 and H4."
        assert rep["research"]["macro"] == "Fed neutral, ECB dovish."
        assert rep["research"]["news"] == "No high-impact economic news in 2 hours."
        assert rep["research"]["manager_synthesis"] == "Consensus: Buy dips."
        assert "historical_lessons" in rep["memory"]
        assert "Forex Market Feed (OHLCV)" in rep["provenance"]["sources"]

        # Check SSE events emitted in order
        event_types = [evt["type"] for evt in _forex_run_events[run_id]]
        assert "preparing_data" in event_types
        assert "technical_analyst" in event_types
        assert "macro_analyst" in event_types
        assert "news_analyst" in event_types
        assert "bull_bear_debate" in event_types
        assert "research_manager" in event_types
        assert "trader" in event_types
        assert "risk_evaluator" in event_types
        assert "complete" in event_types
        assert event_types[-1] == "complete"

        # Check SSE streaming response terminates cleanly on complete
        stream_res = client.get(f"/api/forex/runs/{run_id}/events")
        assert stream_res.status_code == 200
        assert "event: complete" in stream_res.text
        assert "event: preparing_data" in stream_res.text

    def test_forex_decision_report_modular_cards(self, client):
        """Phase 27: Verify run endpoint returns all 7 modular intelligence cards & report payload."""
        run_id = "fx_rep_test_7cards"
        now_iso = datetime.now(timezone.utc).isoformat()
        _forex_runs[run_id] = {
            "run_id": run_id,
            "pair": "GBPUSD",
            "timeframe": "M15",
            "execution_timeframe": "M15",
            "context_timeframes": ["H1", "H4"],
            "date": "2026-03-04",
            "status": "completed",
            "provider": "openrouter",
            "quick_model": "deepseek-v3",
            "deep_model": "r1",
            "account_balance": 50000.0,
            "risk_percent": 1.0,
            "started_at": now_iso,
            "finished_at": now_iso,
            "error": None,
            "signal": "SHORT",
            "proposal_id": "prop_gbpusd_01",
            "report_id": "rep_gbpusd_01",
            "report_path": None,
        }
        _forex_completed_reports[run_id] = {
            "run_id": run_id,
            "pair": "GBPUSD",
            "timeframe": "M15",
            "execution_timeframe": "M15",
            "context_timeframes": ["H1", "H4"],
            "signal": "SHORT",
            "proposal": {
                "action": "SHORT",
                "entry_price": 1.2850,
                "entry_zone_low": 1.2845,
                "entry_zone_high": 1.2855,
                "stop_loss": 1.2880,
                "take_profit_1": 1.2790,
                "take_profit_2": 1.2740,
                "risk_reward_ratio": 2.0,
                "suggested_risk_percent": 1.0,
                "suggested_lot_size": 1.2,
                "valid_until": "2026-03-04 18:00:00 UTC",
                "invalidation_condition": "H1 close above 1.2885",
                "trade_rationale_summary": "London session distribution after liquidity sweep.",
            },
            "risk_decision": {
                "decision": "APPROVE",
                "max_risk_percent": 1.0,
                "approved_lot_size": 1.2,
                "risk_checks_passed": [
                    "Risk:Reward verified >= 1.5R",
                    "Stop distance within 35 pip threshold",
                    "Economic blackout window cleared",
                ],
                "risk_violations": [],
                "modifications_required": [],
                "executive_rationale": "High-probability setup with clean invalidation.",
            },
            "sizing": {
                "recommended_lot_size": 1.2,
                "margin_required": 1200.0,
                "units": 120000.0,
            },
            "context": {
                "execution_timeframe": "M15",
                "context_timeframes": ["H1", "H4"],
                "session": "London Open",
                "spread_pips": 1.2,
                "volatility_atr": 38.0,
                "news_risk": "CLEARED",
            },
            "research": {
                "technical": "Bearish engulfing on M15 retesting broken support.",
                "macro": "BoE dovish comments vs strong USD data.",
                "news": "No high-impact UK news today.",
                "bull_case": "Support holding at 1.2840.",
                "bear_case": "Break of daily pivot with volume.",
                "manager_synthesis": "Sell rallies below 1.2860.",
            },
            "memory": {
                "historical_lessons": ["Avoid shorting into daily S1 without confirmation."],
            },
            "provenance": {
                "sources": ["Forex Market Feed (OHLCV)", "Economic Calendar", "Central Bank Intelligence"],
                "generated_at": now_iso,
                "analysis_cutoff": "2026-03-04",
            },
            "report": "## Executive Decision\n\nApproved SHORT execution for GBPUSD.",
        }

        # 1. Fetch via /api/forex/runs/{run_id}
        res = client.get(f"/api/forex/runs/{run_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["run"]["run_id"] == run_id
        rep = data["report"]
        assert rep["pair"] == "GBPUSD"
        assert rep["signal"] == "SHORT"
        assert rep["proposal"]["entry_price"] == 1.2850
        assert rep["proposal"]["risk_reward_ratio"] == 2.0
        assert rep["risk_decision"]["decision"] == "APPROVE"
        assert len(rep["risk_decision"]["risk_checks_passed"]) == 3
        assert rep["context"]["session"] == "London Open"
        assert rep["research"]["technical"] == "Bearish engulfing on M15 retesting broken support."
        assert len(rep["memory"]["historical_lessons"]) == 1
        assert "Forex Market Feed (OHLCV)" in rep["provenance"]["sources"]

        # 2. Fetch via compatibility alias /api/forex/analyze/{run_id}/report
        alias_res = client.get(f"/api/forex/analyze/{run_id}/report")
        assert alias_res.status_code == 200
        alias_rep = alias_res.json()
        assert alias_rep["run_id"] == run_id
        assert alias_rep["context"]["execution_timeframe"] == "M15"

    def test_run_forex_analysis_worker_failure_emits_error(self, client):
        run_id = "fx_test_failure_456"
        req = ForexAnalysisRequest(
            pair="USDJPY",
            timeframe="H1",
            account_source="manual",
            account_balance=100000,
            account_equity=100000,
            account_free_margin=100000,
            account_leverage=100,
            account_currency="USD",
        )

        _forex_runs[run_id] = {
            "run_id": run_id,
            "pair": "USDJPY",
            "timeframe": "H1",
            "date": None,
            "status": "queued",
            "provider": "",
            "quick_model": "",
            "deep_model": "",
            "account_balance": 100000.0,
            "risk_percent": 1.0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "signal": None,
            "proposal_id": None,
            "report_id": None,
            "report_path": None,
        }
        _forex_run_events[run_id] = []

        with patch("web.forex_routes.ForexTradingAgentsGraph") as mock_graph_cls:
            mock_graph = MagicMock()
            mock_graph.stream.side_effect = RuntimeError("Broker connection timeout")
            mock_graph_cls.return_value = mock_graph

            _run_forex_analysis(run_id, req)

        assert _forex_runs[run_id]["status"] == "failed"
        assert _forex_runs[run_id]["error"] == "The Forex analysis provider failed. Please retry."
        event_types = [evt["type"] for evt in _forex_run_events[run_id]]
        assert "error" in event_types

    def test_unknown_run_cannot_stream_fake_progress(self, client):
        assert client.get("/api/forex/runs/unknown").status_code == 404
        assert client.get("/api/forex/runs/unknown/events").status_code == 404


# ---------------------------------------------------------------------------
# 5. Forex Backtesting Tests
# ---------------------------------------------------------------------------

class TestForexBacktestRoutes:
    @pytest.mark.parametrize("extra", [{}, {"demo_mode": False}, {"candles": [{"open": 1.0}]}])
    def test_backtest_requires_demo_opt_in(self, client, isolated_forex_env, extra):
        journal, _, _ = isolated_forex_env
        res = client.post("/api/forex/backtest/run", json={"pair": "EURUSD", **extra})
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "FOREX_BACKTEST_DEMO_ONLY"
        assert client.get("/api/forex/backtest/runs").json()["count"] == 0
        assert journal.list_trades() == []

    def test_run_backtest_with_synthetic_candles(self, client, isolated_forex_env):
        req = {
            "pair": "EURUSD",
            "timeframe": "M15",
            "initial_balance": 100000.0,
            "count": 60,
            "demo_mode": True,
            "spread_pips": 1.2,
            "slippage_pips": 0.3,
            "commission_per_lot_usd": 5.0,
        }
        res = client.post("/api/forex/backtest/run", json=req)
        assert res.status_code == 200
        data = res.json()
        assert "backtest_id" in data
        assert data["status"] == "completed"
        assert "result" in data
        result = data["result"]
        assert "total_trades" in result
        assert "win_rate_pct" in result
        assert "profit_factor" in result
        assert "equity_curve" in result
        assert "markdown_report" in data
        assert data["demo_mode"] is True
        assert data["data_source"] == "synthetic"
        assert data["strategy"] == "demo_trend_continuation"
        assert data["validated_strategy_performance"] is False
        assert data["markdown_report"].startswith("> DEMO ONLY:")
        assert "synthetic candles" in data["notice"]
        assert result["total_trades"] > 0
        journal, _, _ = isolated_forex_env
        assert journal.list_trades() == []
        assert journal.list_proposals() == []
        assert journal.get_events() == []

        bt_id = data["backtest_id"]

        # Query backtest run
        get_res = client.get(f"/api/forex/backtest/{bt_id}")
        assert get_res.status_code == 200
        assert get_res.json()["backtest_id"] == bt_id
        for key in ("demo_mode", "data_source", "strategy", "validated_strategy_performance", "notice", "markdown_report"):
            assert get_res.json()[key] == data[key]

        # List runs
        list_res = client.get("/api/forex/backtest/runs")
        assert list_res.status_code == 200
        assert list_res.json()["count"] >= 1
        assert "backtests" not in list_res.json()
        listed = next(run for run in list_res.json()["runs"] if run["backtest_id"] == bt_id)
        assert listed["mode"] == "DEMO"
        assert listed["data_source"] == data["data_source"]
        assert listed["validated_strategy_performance"] is False
        assert listed["notice"] == data["notice"]
        assert listed["total_trades"] == result["total_trades"]

    def test_run_backtest_with_custom_candles(self, client, isolated_forex_env):
        # 20 custom candles
        custom_candles = []
        base = 1.0800
        for i in range(20):
            custom_candles.append({
                "time": f"2026-09-01T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
                "open": base + (i * 0.0005),
                "high": base + (i * 0.0005) + 0.0010,
                "low": base + (i * 0.0005) - 0.0002,
                "close": base + (i * 0.0005) + 0.0008,
                "volume": 150.0,
            })
        req = {
            "pair": "EURUSD",
            "timeframe": "M15",
            "candles": custom_candles,
            "demo_mode": True,
        }
        res = client.post("/api/forex/backtest/run", json=req)
        assert res.status_code == 200
        assert res.json()["status"] == "completed"
        assert res.json()["demo_mode"] is True
        assert res.json()["data_source"] == "user_supplied"
        assert res.json()["validated_strategy_performance"] is False
        assert "unverified provenance" in res.json()["notice"]
        assert res.json()["result"]["total_trades"] > 0
        journal, _, _ = isolated_forex_env
        assert journal.list_trades() == []
        assert journal.list_proposals() == []


# ---------------------------------------------------------------------------
# 6. Quantitative Analytics Tests
# ---------------------------------------------------------------------------

class TestAnalyticsRoutes:
    def test_analytics_dashboard_endpoint(self, client):
        # Record a closed trade first so analytics has data
        t_id = client.post(
            "/api/forex/journal/trades/manual-open",
            json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0800, "lots": 1.0, "stop_loss": 1.0780, "take_profit": 1.0840},
        ).json()["trade_id"]
        client.post(f"/api/forex/journal/trades/{t_id}/close", json={"close_price": 1.0840, "exit_reason": "TAKE_PROFIT"})

        res = client.get("/api/forex/analytics/dashboard?pair=EURUSD&initial_capital=100000")
        assert res.status_code == 200
        data = res.json()
        assert "dashboard" in data
        assert "markdown" in data
        assert data["dashboard"]["total_trades"] == 1

    def test_deep_metrics_endpoint(self, client):
        res = client.get("/api/forex/analytics/metrics?initial_capital=100000")
        assert res.status_code == 200
        metrics = res.json()["metrics"]
        assert "calmar_ratio" in metrics
        assert "sqn_rating" in metrics
        assert "underwater_episodes" in metrics

    def test_monte_carlo_endpoint(self, client):
        res = client.post(
            "/api/forex/analytics/monte-carlo",
            json={"trials": 200, "initial_capital": 100000.0, "seed": 42},
        )
        assert res.status_code == 200
        mc = res.json()["monte_carlo"]
        assert "drawdown_percentiles" in mc
        assert "ruin_probabilities" in mc

    def test_risk_calibration_endpoint(self, client):
        res = client.get("/api/forex/analytics/calibration?pair=EURUSD")
        assert res.status_code == 200
        calib = res.json()["calibration"]
        assert "optimal_sl_pips" in calib
        assert "optimal_tp_pips" in calib

    def test_ablation_study_endpoint(self, client):
        res = client.post("/api/forex/analytics/ablation", json={"pair": "EURUSD"})
        assert res.status_code == 200
        study = res.json()["ablation_study"]
        assert "variants" in study
        assert "component_importance_ranking" in study


# ---------------------------------------------------------------------------
# 7. Post-Trade Learning Tests
# ---------------------------------------------------------------------------

class TestLearningRoutes:
    def test_list_and_retrieve_lessons(self, client):
        # Query empty lessons
        res = client.get("/api/forex/learning/lessons")
        assert res.status_code == 200
        assert res.json()["count"] == 0

        # Retrieve prompt lessons
        ret_res = client.get("/api/forex/learning/retrieve?pair=EURUSD&limit=5")
        assert ret_res.status_code == 200
        assert "markdown_prompt" in ret_res.json()

    def test_reflect_on_closed_trade(self, client):
        # Open and close trade
        t_id = client.post(
            "/api/forex/journal/trades/manual-open",
            json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0800, "lots": 1.0, "stop_loss": 1.0780, "take_profit": 1.0840},
        ).json()["trade_id"]
        client.post(f"/api/forex/journal/trades/{t_id}/close", json={"close_price": 1.0840, "exit_reason": "TAKE_PROFIT"})

        # Trigger reflection
        ref_res = client.post(f"/api/forex/learning/reflect/{t_id}")
        assert ref_res.status_code == 200
        data = ref_res.json()
        assert data["status"] == "COMPLETED"
        assert "reflection" in data
        assert "rating" in data["reflection"]


# ---------------------------------------------------------------------------
# 8. Forex Analyst Selection Tests (Phase 1)
# ---------------------------------------------------------------------------

class TestForexAnalystSelection:
    def test_technical_only_accepted(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": ["forex_technical"]})
        assert res.status_code == 200
        data = res.json()
        assert data["analysts"] == ["forex_technical"]

    def test_macro_only_accepted(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": ["forex_macro"]})
        assert res.status_code == 200
        data = res.json()
        assert data["analysts"] == ["forex_macro"]

    def test_news_only_accepted(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": ["forex_news"]})
        assert res.status_code == 200
        data = res.json()
        assert data["analysts"] == ["forex_news"]

    def test_technical_and_macro_accepted(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": ["forex_technical", "forex_macro"]})
        assert res.status_code == 200
        data = res.json()
        assert data["analysts"] == ["forex_technical", "forex_macro"]

    def test_all_three_analysts_accepted(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "EURUSD", "analysts": ["forex_technical", "forex_macro", "forex_news"]},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["analysts"] == ["forex_technical", "forex_macro", "forex_news"]

    def test_empty_analysts_rejected(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": []})
        assert res.status_code == 422

    def test_invalid_analyst_rejected(self, client):
        res = client.post("/api/forex/analyze", json={"pair": "EURUSD", "analysts": ["unsupported_analyst"]})
        assert res.status_code == 422

    def test_duplicate_analysts_rejected(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "EURUSD", "analysts": ["forex_technical", "forex_technical"]},
        )
        assert res.status_code == 422

    def test_risk_engine_remains_mandatory_in_graph(self):
        from unittest.mock import MagicMock

        from tradingagents.graph.forex_graph import ForexTradingAgentsGraph

        mock_llm = MagicMock()
        # Test technical only
        graph_tech = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
        )
        assert "Forex Risk Evaluator" in graph_tech.workflow.nodes
        assert "Forex Trader" in graph_tech.workflow.nodes
        assert "Research Manager" in graph_tech.workflow.nodes
        assert "Forex Technical Analyst" in graph_tech.workflow.nodes
        assert "Forex Macro Analyst" not in graph_tech.workflow.nodes
        assert "Forex News Analyst" not in graph_tech.workflow.nodes

        # Test macro only
        graph_macro = ForexTradingAgentsGraph(
            selected_analysts=("forex_macro",),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
        )
        assert "Forex Risk Evaluator" in graph_macro.workflow.nodes
        assert "Forex Macro Analyst" in graph_macro.workflow.nodes
        assert "Forex Technical Analyst" not in graph_macro.workflow.nodes

    def test_backend_selection_reaches_graph_worker(self, monkeypatch, client):
        from web import forex_routes

        captured_kwargs = {}

        class MockGraph:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
            def stream(self, *args, **kwargs):
                return iter([])
            def get_state(self):
                return {}
            def process_signal(self, state):
                return "NO_TRADE"
            def get_last_proposal(self):
                return None
            def get_last_risk_decision(self):
                return None
            def get_last_sizing_result(self):
                return None

        monkeypatch.setattr(forex_routes, "ForexTradingAgentsGraph", MockGraph)
        res = client.post(
            "/api/forex/analyze",
            json={
                "pair": "EURUSD",
                "analysts": ["forex_technical", "forex_news"],
                "account_source": "manual",
                "account_balance": 100000.0,
                "account_equity": 100000.0,
                "account_free_margin": 100000.0,
                "account_leverage": 100.0,
                "account_currency": "USD",
            },
        )
        assert res.status_code == 200
        # Allow worker thread to invoke Graph
        import time

        time.sleep(0.15)
        assert captured_kwargs.get("selected_analysts") == ("forex_technical", "forex_news")


# ---------------------------------------------------------------------------
# 9. Forex Timeframe First-Class Tests (Phase 2)
# ---------------------------------------------------------------------------

class TestForexTimeframeFirstClass:
    @pytest.mark.parametrize("tf", ["M5", "M15", "H1", "H4"])
    def test_api_accepts_execution_timeframe_and_applies_defaults(self, client, tf):
        from tradingagents.forex.domain import get_default_context_timeframes

        expected_ctx = [c.value for c in get_default_context_timeframes(tf)]
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "EURUSD", "execution_timeframe": tf},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["execution_timeframe"] == tf
        assert data["timeframe"] == tf
        assert data["context_timeframes"] == expected_ctx

    def test_api_accepts_custom_context_timeframes(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={
                "pair": "EURUSD",
                "execution_timeframe": "M15",
                "context_timeframes": ["H1", "D1"],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["execution_timeframe"] == "M15"
        assert data["context_timeframes"] == ["H1", "D1"]

    def test_backward_compatibility_timeframe_key(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "GBPUSD", "timeframe": "M5"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["execution_timeframe"] == "M5"
        assert data["timeframe"] == "M5"
        assert "M15" in data["context_timeframes"]

    def test_invalid_timeframe_rejected(self, client):
        res = client.post(
            "/api/forex/analyze",
            json={"pair": "EURUSD", "execution_timeframe": "INVALID_TF"},
        )
        assert res.status_code == 422

    @pytest.mark.parametrize("tf", ["M5", "M15", "H1", "H4"])
    def test_graph_state_and_proposal_preserves_execution_timeframe(self, tf):
        from unittest.mock import MagicMock

        from tradingagents.database.journal import ForexTradeJournal
        from tradingagents.forex.domain import get_default_context_timeframes
        from tradingagents.graph.forex_graph import (
            ForexTradingAgentsGraph,
            create_forex_portfolio_manager,
            create_forex_risk_evaluator,
        )

        mock_llm = MagicMock()
        with ForexTradeJournal(":memory:") as journal:
            graph = ForexTradingAgentsGraph(
                selected_analysts=("forex_technical",),
                quick_thinking_llm=mock_llm,
                deep_thinking_llm=mock_llm,
                journal=journal,
            )
            state = graph.create_run_state("EURUSD", execution_timeframe=tf)
            assert state["forex_execution_timeframe"] == tf
            assert state["timeframe"] == tf
            expected_ctx = [c.value for c in get_default_context_timeframes(tf)]
            assert state["forex_context_timeframes"] == expected_ctx
            assert f"({tf})" in state["instrument_context"]

            # Verify evaluator and portfolio manager preserve timeframe into proposal and journal
            evaluator = create_forex_risk_evaluator(
                risk_engine=graph.risk_engine,
                sizing_engine=graph.sizing_engine,
                risk_limits=graph.risk_limits,
                sizing_account=graph.sizing_account,
                sizing_constraints=graph.sizing_constraints,
                sizing_method=graph.sizing_method,
            )
            raw_proposal = {
                "pair": "EURUSD",
                "action": "LONG",
                "order_type": "MARKET",
                "setup_type": "TREND_CONTINUATION",
                "timeframe": "H1",  # simulate misaligned model proposal
                "entry_price": 1.0850,
                "stop_loss": 1.0800,
                "take_profit_1": 1.0950,
                "suggested_risk_percent": 1.0,
            }
            state["forex_proposal"] = raw_proposal
            eval_res = evaluator(state)
            assert eval_res["forex_proposal"]["timeframe"] == tf

            state.update(eval_res)
            manager = create_forex_portfolio_manager(journal=journal)
            mgr_res = manager(state)
            proposal_id = mgr_res["forex_proposal_id"]
            assert proposal_id is not None
            saved = journal.get_proposal(proposal_id)
            assert saved.timeframe == tf


# ---------------------------------------------------------------------------
# 10. Authentication Enforcement Tests (Phase 5)
# ---------------------------------------------------------------------------

class TestForexAuthentication:
    """Verify that Forex mutating routes enforce authentication and never swallow 401."""

    def test_unauthenticated_request_blocked_when_api_key_configured(self):
        import web.server as server

        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", "prod-secret-key-xyz"):
            # Session cookie alone must NOT suffice when explicit API key is configured
            fresh_client.cookies.set("tradingagents_session", server._SESSION_TOKEN)

            # Analyze
            res_analyze = fresh_client.post("/api/forex/analyze", json={"pair": "EURUSD"})
            assert res_analyze.status_code == 401

            # MT5 connect
            res_connect = fresh_client.post("/api/forex/mt5/connect", json={"login": 12345, "server": "Test"})
            assert res_connect.status_code == 401

            # Journal manual open
            res_open = fresh_client.post(
                "/api/forex/journal/trades/manual-open",
                json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0850, "lots": 0.1},
            )
            assert res_open.status_code == 401

            # Proposal status
            res_status = fresh_client.post("/api/forex/proposals/prop_test/status", json={"status": "APPROVED"})
            assert res_status.status_code == 401

    def test_incorrect_api_key_returns_401(self):
        import web.server as server

        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", "prod-secret-key-xyz"):
            res1 = fresh_client.post(
                "/api/forex/analyze",
                json={"pair": "EURUSD"},
                headers={"X-API-Key": "wrong-key"},
            )
            assert res1.status_code == 401

            res2 = fresh_client.post(
                "/api/forex/mt5/connect",
                json={"login": 12345, "server": "Test"},
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert res2.status_code == 401

    def test_correct_api_key_accepted(self):
        import web.server as server

        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", "prod-secret-key-xyz"):
            # Accepted via X-API-Key
            res1 = fresh_client.post(
                "/api/forex/journal/trades/manual-open",
                json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0850, "lots": 0.1},
                headers={"X-API-Key": "prod-secret-key-xyz"},
            )
            assert res1.status_code == 200

            # Accepted via Bearer token
            res2 = fresh_client.post(
                "/api/forex/journal/trades/manual-open",
                json={"pair": "EURUSD", "action": "SHORT", "entry_price": 1.0850, "lots": 0.1},
                headers={"Authorization": "Bearer prod-secret-key-xyz"},
            )
            assert res2.status_code == 200

    def test_missing_session_returns_401_when_no_api_key_configured(self):
        import web.server as server

        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", ""):
            # Client without cookie and without session header gets 401
            res = fresh_client.post(
                "/api/forex/journal/trades/manual-open",
                json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0850, "lots": 0.1},
            )
            assert res.status_code == 401

    def test_valid_session_cookie_accepted_when_no_api_key_configured(self):
        import web.server as server

        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", ""):
            fresh_client.get("/")
            res = fresh_client.post(
                "/api/forex/journal/trades/manual-open",
                json={"pair": "EURUSD", "action": "LONG", "entry_price": 1.0850, "lots": 0.1},
            )
            assert res.status_code == 200

    def test_verify_forex_auth_never_swallows_http_exception(self):
        from fastapi import HTTPException

        from web.forex_routes import verify_forex_auth

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.cookies = {}

        # verify_forex_auth must raise HTTPException, never return True when unauthorized
        with pytest.raises(HTTPException) as exc_info:
            verify_forex_auth(mock_request)
        assert exc_info.value.status_code == 401


class TestCredentialSecurity:
    """Validate that API keys and broker credentials are never leaked or persisted insecurely."""

    def test_mt5_connect_request_does_not_leak_password_in_repr(self):
        from web.forex_routes import MT5ConnectRequest

        req = MT5ConnectRequest(login=12345, password="super_secret_broker_password_987", server="DemoServer")
        req_repr = repr(req)
        req_str = str(req)

        assert "super_secret_broker_password_987" not in req_repr
        assert "super_secret_broker_password_987" not in req_str

    def test_mt5_connection_repr_masks_password(self):
        from tradingagents.mt5.connection import MT5Connection

        conn = MT5Connection(
            login=999888,
            password="sensitive_broker_pass_abc",
            server="LiveBroker",
            mt5_api=MagicMock(),
        )
        conn_repr = repr(conn)

        assert "sensitive_broker_pass_abc" not in conn_repr
        assert "***" in conn_repr

    def test_frontend_does_not_persist_secrets_in_web_storage(self):
        from pathlib import Path

        app_js_path = Path(__file__).resolve().parent.parent / "web" / "static" / "app.js"
        content = app_js_path.read_text(encoding="utf-8")

        # Must NOT write API keys or broker secrets to web storage
        assert "localStorage.setItem('tradingagents_api_key'" not in content
        assert "localStorage.setItem(\"tradingagents_api_key\"" not in content
        assert "sessionStorage.setItem('tradingagents_api_key'" not in content
        assert "sessionStorage.setItem(\"tradingagents_api_key\"" not in content

        # Must actively purge legacy secrets
        assert "localStorage.removeItem('tradingagents_api_key')" in content


class TestDashboardOverview:
    """Validate Phase 25 consolidated institutional dashboard overview."""

    def test_mask_account_login(self):
        from web.forex_routes import mask_account_login

        assert mask_account_login(None) == "Not Set"
        assert mask_account_login("") == "Not Set"
        assert mask_account_login(123) == "***"
        assert mask_account_login("123") == "***"
        assert mask_account_login("12345678") == "123*****"
        assert mask_account_login(987654321) == "987******"

    def test_dashboard_overview_disconnected(self, client):
        res = client.get("/api/forex/dashboard/overview")
        assert res.status_code == 200
        data = res.json()

        # MT5 subsystem
        assert "mt5" in data
        assert data["mt5"]["connection_status"] == "DISCONNECTED"
        assert data["mt5"]["is_connected"] is False
        assert data["mt5"]["account"] is None
        assert data["mt5"]["open_positions"] == []
        assert data["mt5"]["pending_orders"] == []

        # Trading subsystem
        assert "trading" in data
        assert data["trading"]["open_positions_count"] == 0
        assert data["trading"]["pending_orders_count"] == 0
        assert "today_result" in data["trading"]
        assert data["trading"]["today_result"]["trade_count"] == 0

        # Research subsystem
        assert "research" in data
        assert isinstance(data["research"]["recent_analyses"], list)
        assert isinstance(data["research"]["upcoming_events"], list)
        assert isinstance(data["research"]["recent_lessons"], list)

        # Performance subsystem & Sample guard
        assert "performance" in data
        perf = data["performance"]
        assert perf["trade_count"] == 0
        assert perf["is_sample_size_adequate"] is False
        assert "Sample size warning" in perf["sample_warning"]
        assert "30" in perf["sample_warning"]

    @pytest.mark.parametrize("pair", ["EURUSD", "USDJPY", "GBPUSD"])
    def test_dashboard_events_follow_requested_pair(self, client, pair):
        with patch(
            "tradingagents.forex.calendar.get_calendar_events_for_pair",
            return_value=[],
        ) as calendar:
            res = client.get(f"/api/forex/dashboard/overview?pair={pair}")
        assert res.status_code == 200
        assert res.json()["pair"] == pair
        assert calendar.call_args.kwargs["symbol"] == pair

    def test_dashboard_overview_connected_with_positions(self, client, isolated_forex_env):
        from tradingagents.agents.schemas_forex import ForexAction

        in_memory_journal, _, mock_observer = isolated_forex_env

        # Setup mock MT5 observer as connected with account and position
        mock_conn = mock_observer.connection
        mock_conn.is_connected.return_value = True
        mock_conn.get_status.return_value = MT5ConnectionStatus.CONNECTED
        mock_conn.server = "Demo-Server-01"
        mock_conn.login = 88776655

        mock_acc = MagicMock()
        mock_acc.balance = 25000.0
        mock_acc.equity = 25450.0
        mock_acc.margin = 350.0
        mock_acc.margin_free = 25100.0
        mock_acc.profit = 450.0
        mock_acc.currency = "USD"
        mock_acc.leverage = 200
        mock_conn.get_account_info.return_value = mock_acc

        mock_pos = MagicMock()
        mock_pos.model_dump.return_value = {
            "ticket": 1001,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.5,
            "price_open": 1.0850,
            "profit": 450.0,
        }
        mock_conn.get_positions.return_value = [mock_pos]
        mock_conn.get_orders.return_value = []

        # Add a closed trade today into journal
        now_iso = datetime.now(timezone.utc).isoformat()
        in_memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.0800,
            stop_loss=1.0780,
            lots=0.5,
            trade_id="tr_today_1",
        )
        in_memory_journal.record_trade_close(
            trade_id="tr_today_1",
            close_price=1.0850,
            close_time_utc=now_iso,
        )

        res = client.get("/api/forex/dashboard/overview")
        assert res.status_code == 200
        data = res.json()

        assert data["mt5"]["is_connected"] is True
        assert data["mt5"]["masked_login"] == "887*****"
        assert data["mt5"]["server"] == "Demo-Server-01"
        assert data["mt5"]["account"]["balance"] == 25000.0
        assert data["mt5"]["account"]["equity"] == 25450.0
        assert len(data["mt5"]["open_positions"]) == 1
        assert data["trading"]["open_positions_count"] == 1
        assert data["trading"]["today_result"]["trade_count"] == 1
        assert data["trading"]["today_result"]["net_profit"] == 250.0
        assert data["trading"]["today_result"]["total_r"] == 2.5

    def test_mt5_symbol_info_with_tick_enrichment(self, client, isolated_forex_env):
        """Verify get_mt5_symbol_info returns symbol specifications and enriches with live tick."""
        _, _, mock_observer = isolated_forex_env
        mock_sym = MT5SymbolInfo(
            name="EURUSD",
            canonical_symbol="EURUSD",
            path="Forex/Majors/EURUSD",
            digits=5,
            point=0.00001,
            spread_points=12,
            spread_pips=1.2,
            bid=1.08500,
            ask=1.08512,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            contract_size=100000.0,
        )
        mock_tick = MT5Tick(
            symbol="EURUSD",
            time=datetime.now(timezone.utc),
            bid=1.08500,
            ask=1.08512,
            spread_points=12,
            spread_pips=1.2,
        )
        mock_observer.get_symbol_info.return_value = mock_sym
        mock_observer.get_current_tick.return_value = mock_tick

        res = client.get("/api/forex/mt5/symbol/EURUSD")
        assert res.status_code == 200
        body = res.json()
        assert "symbol_info" in body
        assert body["symbol_info"]["name"] == "EURUSD"
        assert body["symbol_info"]["digits"] == 5
        assert body["symbol_info"]["spread_pips"] == 1.2
        assert "tick" in body
        assert body["tick"]["bid"] == 1.08500
        assert body["tick"]["ask"] == 1.08512

    def test_mt5_observer_page_connect_disconnect_flow(self, client, isolated_forex_env):
        """Verify MT5 connect and disconnect observer endpoints and ensure no password leaks."""
        _, _, mock_observer = isolated_forex_env
        mock_conn = mock_observer.connection
        mock_conn.connect.return_value = True
        mock_conn.get_status.return_value = MT5ConnectionStatus.CONNECTED
        mock_conn.login = 12345678
        mock_conn.server = "MetaQuotes-Demo"

        # 1. Connect request (without password or with masked password)
        res_connect = client.post(
            "/api/forex/mt5/connect",
            json={"login": 12345678, "server": "MetaQuotes-Demo", "path": "terminal64.exe"},
        )
        assert res_connect.status_code == 200
        conn_data = res_connect.json()
        assert conn_data["connected"] is True
        assert conn_data["status"] == "CONNECTED"
        assert "password" not in conn_data

        # 2. Check status returns masked login
        mock_conn.is_connected.return_value = True
        res_status = client.get("/api/forex/mt5/status")
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["is_connected"] is True
        assert status_data["masked_login"] == "123*****"
        assert "password" not in status_data

        # 3. Disconnect request
        res_disconnect = client.post("/api/forex/mt5/disconnect")
        assert res_disconnect.status_code == 200
        disc_data = res_disconnect.json()
        assert disc_data["status"] == "DISCONNECTED"
        assert disc_data["is_connected"] is False
        mock_conn.disconnect.assert_called_once()
