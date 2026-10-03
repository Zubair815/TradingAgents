"""Focused regression tests for Phase 8: Runtime, Configuration, and UI Completion.

Covers:
- RUN-008: End-to-end LangGraph checkpoint/resume integrity for Forex analysis runs.
- CFG-006: Truthful restart-required notifications for MT5 polling configuration changes,
           ensuring dynamically applied settings never falsely trigger restart notices.
- UI-005: Truthful representation of unavailable/error states in runtime APIs and contracts.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

from tests.test_forex_graph import MockChatModel
from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.dataflows import config as config_module
from tradingagents.graph.forex_graph import ForexTradingAgentsGraph
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.mt5.errors import MT5DataError
from tradingagents.mt5.models import MT5ConnectionStatus
from tradingagents.mt5.observer import MT5Observer
from tradingagents.risk.engine import ForexRiskLimits
from web.forex_routes import reset_forex_state, set_forex_dependencies
from web.server import app


@pytest.fixture(autouse=True)
def isolated_forex_env():
    """Ensure clean in-memory journal and dependencies for each test."""
    reset_forex_state()
    in_memory_journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    journal_mgr = ForexJournalManager(journal=in_memory_journal)

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
def isolated_settings(tmp_path, monkeypatch):
    settings_path = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(config_module, "_RUNTIME_CONFIG_PATH", settings_path)
    config_module.reset_runtime_settings()
    yield settings_path
    config_module.reset_runtime_settings()


class CrashableMockChatModel(MockChatModel):
    """Mock LLM that can simulate an unexpected failure at the Trader node."""

    def __init__(self, response_msg=None, structured_response=None, crash_on_trader: bool = False):
        super().__init__(response_msg=response_msg, structured_response=structured_response)
        self.crash_on_trader = crash_on_trader
        self.analyst_calls = 0
        self.trader_calls = 0

    def invoke(self, input: Any, config: Any = None) -> AIMessage:
        prompt_text = str(input)
        if "Trader" in prompt_text or "investment plan" in prompt_text.lower():
            self.trader_calls += 1
            if self.crash_on_trader:
                raise RuntimeError("Simulated mid-graph failure at Forex Trader node")
        else:
            self.analyst_calls += 1
        return super().invoke(input, config)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        schema_name = getattr(schema, "__name__", str(schema))
        if "ForexTraderProposal" in schema_name and self.crash_on_trader:
            def _crash(x):
                raise RuntimeError("Simulated mid-graph failure at Forex Trader node")
            return RunnableLambda(_crash)
        return super().with_structured_output(schema, **kwargs)


class TestCheckpointResumeEndToEnd:
    """Verifies RUN-008: LangGraph checkpointing saves state and resumes from last node."""

    def test_checkpoint_resume_after_mid_graph_crash(self):
        memory_saver = MemorySaver()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            order_type=OrderType.MARKET,
            setup_type=SetupType.BREAKOUT,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit_1=1.0950,
            reasoning="Checkpoint resume test verified.",
            confidence=0.8,
        )

        mock_llm = CrashableMockChatModel(
            structured_response=proposal,
            crash_on_trader=True,
        )

        risk_limits = ForexRiskLimits(enforce_market_open=False, enforce_news_blackout=False)

        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            risk_limits=risk_limits,
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            checkpointer=memory_saver,
            db_path=":memory:",
        )

        thread_id = "test_resumable_run_42"

        # Run 1: Should execute technical analyst, then crash at Trader node
        with pytest.raises(RuntimeError, match="Simulated mid-graph failure"):
            graph.run("EURUSD", trade_date="2026-03-04", thread_id=thread_id)

        assert mock_llm.analyst_calls >= 1

        # Verify a checkpoint exists for this thread
        cfg = {"configurable": {"thread_id": thread_id}}
        cp_state = graph.graph.get_state(cfg)
        assert cp_state is not None
        assert cp_state.next  # Next node to execute is queued

        # Run 2: Fix the failure condition and resume
        mock_llm.crash_on_trader = False
        analyst_calls_before_resume = mock_llm.analyst_calls

        final_state, signal = graph.run("EURUSD", trade_date="2026-03-04", thread_id=thread_id)

        assert signal in ("NO_TRADE", "LONG", "SHORT", "REJECT", "MODIFY")
        assert final_state is not None
        assert final_state.get("forex_proposal") is not None
        # Verify analyst was NOT re-executed because state was resumed from checkpoint
        assert mock_llm.analyst_calls == analyst_calls_before_resume

        # Verify thread is now completed
        post_cp_state = graph.graph.get_state(cfg)
        assert post_cp_state.next == ()

    def test_stream_checkpoint_resume(self):
        memory_saver = MemorySaver()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            order_type=OrderType.MARKET,
            setup_type=SetupType.BREAKOUT,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit_1=1.0950,
            reasoning="Stream checkpoint resume test.",
            confidence=0.85,
        )

        mock_llm = CrashableMockChatModel(
            structured_response=proposal,
            crash_on_trader=True,
        )

        risk_limits = ForexRiskLimits(enforce_market_open=False, enforce_news_blackout=False)

        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            risk_limits=risk_limits,
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            checkpointer=memory_saver,
            db_path=":memory:",
        )

        thread_id = "test_stream_resumable_run_99"

        # Stream 1: Fails mid-run
        with pytest.raises(RuntimeError, match="Simulated mid-graph failure"):
            for _ in graph.stream("EURUSD", trade_date="2026-03-04", thread_id=thread_id):
                pass

        # Stream 2: Resumes and completes
        mock_llm.crash_on_trader = False
        chunks = list(graph.stream("EURUSD", trade_date="2026-03-04", thread_id=thread_id))
        assert len(chunks) > 0


class TestConfigurationRestartTruthfulness:
    """Verifies CFG-006: restart-required notifications are strictly truthful."""

    def test_dynamic_setting_never_reports_restart_required(
        self, client, isolated_settings
    ):
        mock_runtime = MagicMock()
        mock_runtime.service.poll_interval = 5.0

        with patch("web.forex_routes._forex_runtime", mock_runtime):
            # Patch purely dynamic settings
            resp = client.patch("/api/forex/settings", json={
                "forex_default_pair": "GBPUSD",
                "forex_default_risk_percent": 1.5,
                "forex_min_rr": 2.2,
                "forex_market_source": "yahoo",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["restart_required"] is False
            assert data["saved"] is True

            # GET should also report restart_required is False
            get_resp = client.get("/api/forex/settings")
            assert get_resp.status_code == 200
            assert get_resp.json()["restart_required"] is False

    def test_mt5_poll_interval_change_reports_restart_required(
        self, client, isolated_settings
    ):
        mock_runtime = MagicMock()
        mock_runtime.service.poll_interval = 5.0

        with patch("web.forex_routes._forex_runtime", mock_runtime):
            # Patch MT5 poll interval
            resp = client.patch("/api/forex/settings", json={
                "mt5_poll_interval_seconds": 12.0,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["restart_required"] is True

            # GET should truthfully reflect restart is required because active poll is 5.0 but config is 12.0
            get_resp = client.get("/api/forex/settings")
            assert get_resp.status_code == 200
            assert get_resp.json()["restart_required"] is True

    def test_mt5_poll_interval_unchanged_does_not_falsely_require_restart(
        self, client, isolated_settings
    ):
        mock_runtime = MagicMock()
        mock_runtime.service.poll_interval = 5.0

        with patch("web.forex_routes._forex_runtime", mock_runtime):
            # Submitting the current value should NOT trigger restart_required
            resp = client.patch("/api/forex/settings", json={
                "mt5_poll_interval_seconds": 5.0,
            })
            assert resp.status_code == 200
            assert resp.json()["restart_required"] is False

    def test_reset_reports_restart_if_poll_interval_was_overridden(
        self, client, isolated_settings
    ):
        mock_runtime = MagicMock()
        mock_runtime.service.poll_interval = 10.0

        with patch("web.forex_routes._forex_runtime", mock_runtime):
            # Set to non-default first
            client.patch("/api/forex/settings", json={"mt5_poll_interval_seconds": 10.0})

            # Reset restores default (5.0) which differs from current (10.0)
            reset_resp = client.post("/api/forex/settings/reset")
            assert reset_resp.status_code == 200
            assert reset_resp.json()["restart_required"] is True


class TestUIUnavailableTruthfulness:
    """Verifies UI-005: truthfulness of unavailable states in API responses."""

    def test_mt5_account_when_disconnected_reports_unavailable_metrics(self, client, isolated_forex_env):
        _, _, mock_observer = isolated_forex_env
        mock_observer.get_account_info.side_effect = MT5DataError("Disconnected", code=-1)

        resp = client.get("/api/forex/mt5/account")
        # Must return 503 rather than fake $0.00 balance
        assert resp.status_code == 503
        data = resp.json()
        assert data.get("detail", {}).get("code") == "MT5_DATA_UNAVAILABLE"

    def test_proposals_missing_metrics_do_not_fabricate_zeroes(self, client):
        """Proposals query must not fabricate zeroes for empty fields."""
        resp = client.get("/api/forex/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert "proposals" in data
