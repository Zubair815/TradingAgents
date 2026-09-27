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

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
)
from tradingagents.database.journal import ForexTradeJournal
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
from web.forex_routes import (
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
    return TestClient(app)


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
            "risk_percent": 1.0,  # $1,000 risk
            "leverage": 100.0,
        }
        res = client.post("/api/forex/proposals/size", json=sizing_payload)
        assert res.status_code == 200
        sizing = res.json()["sizing"]
        assert sizing["is_executable"] is True
        assert sizing["lots"] > 0
        assert sizing["margin_required"] > 0
        assert pytest.approx(sizing["risk_amount"], abs=5.0) == 1000.0


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


# ---------------------------------------------------------------------------
# 4. Forex Analysis Run & SSE Streaming Tests
# ---------------------------------------------------------------------------

class TestForexAnalysisRuns:
    @pytest.mark.parametrize("pair", ["EURUSD", "USDJPY"])
    def test_analysis_unavailable_without_fabricated_results(self, client, isolated_forex_env, pair):
        journal, _, _ = isolated_forex_env
        res = client.post("/api/forex/analyze", json={"pair": pair, "timeframe": "M15"})
        assert res.status_code == 503
        detail = res.json()["detail"]
        assert detail["code"] == "FOREX_ANALYSIS_UNAVAILABLE"
        assert detail["status"] == "unavailable"
        assert detail["signal"] is None
        assert "run_id" not in res.json()
        assert client.get("/api/forex/runs").json() == {"runs": [], "count": 0}
        assert journal.list_proposals() == []
        assert journal.list_trades() == []
        assert journal.get_events() == []

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
        listed = next(run for run in list_res.json()["backtests"] if run["backtest_id"] == bt_id)
        for key in ("demo_mode", "data_source", "strategy", "validated_strategy_performance", "notice"):
            assert listed[key] == data[key]

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
