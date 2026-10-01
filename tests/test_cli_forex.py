"""Unit and integration tests for first-class Forex CLI workflow (Phase 5).

Validates:
- Forex pair detection and routing to ForexTradingAgentsGraph.
- Non-interactive CLI command: 'tradingagents run EURUSD'.
- Custom Forex analyst subsets (--analysts technical,macro).
- Explicit manual account mode and complete sizing assumptions.
- Custom risk and timeframe settings reach the graph.
- Error handling on invalid analysts or broken configurations.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import cli.main as m
from cli.models import AnalystType, AssetType
from cli.utils import (
    detect_asset_type,
    filter_analysts_for_asset_type,
)


class TestCliForexDetection:
    @pytest.mark.parametrize("ticker", ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "EURUSD=X"])
    def test_detects_forex_symbols(self, ticker: str):
        assert detect_asset_type(ticker) == AssetType.FOREX

    def test_filter_analysts_for_forex(self):
        analysts = [
            AnalystType.MARKET,
            AnalystType.SOCIAL,
            AnalystType.NEWS,
            AnalystType.FUNDAMENTALS,
        ]
        filtered = filter_analysts_for_asset_type(analysts, AssetType.FOREX)
        assert AnalystType.FUNDAMENTALS not in filtered
        assert AnalystType.MARKET in filtered
        assert AnalystType.NEWS in filtered


class TestCliForexRunCommand:
    @pytest.fixture
    def mock_forex_graph(self, tmp_path: Path):
        mock_graph = MagicMock()
        mock_graph.run.return_value = (
            {
                "final_trade_decision": "Approved LONG",
                "forex_execution_timeframe": "H1",
                "forex_proposal": {
                    "action": "LONG",
                    "entry_price": 1.1,
                    "stop_loss": 1.09,
                    "take_profit": 1.12,
                },
                "forex_risk_decision": {
                    "decision": "APPROVE",
                    "approved_lot_size": 0.2,
                    "violations": [],
                },
            },
            "LONG",
        )
        mock_graph.save_reports.return_value = tmp_path / "forex_report.md"
        return mock_graph

    @staticmethod
    def manual_account_args() -> list[str]:
        return [
            "--account-source", "manual",
            "--balance", "100000",
            "--equity", "99500",
            "--free-margin", "95000",
            "--leverage", "100",
            "--account-currency", "USD",
        ]

    def test_run_forex_default_pipeline(self, mock_forex_graph):
        with patch("tradingagents.forex.ForexTradingAgentsGraph", return_value=mock_forex_graph) as mock_cls:
            runner = CliRunner()
            today_str = datetime.date.today().isoformat()
            result = runner.invoke(
                m.app,
                ["run", "EURUSD", "--date", today_str, *self.manual_account_args()],
            )
            assert result.exit_code == 0, result.output
            assert "Decision: LONG" in result.output
            assert "Report:" in result.output

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["selected_analysts"] == ["forex_technical", "forex_macro", "forex_news"]
            assert call_kwargs["sizing_account"].balance == 100000.0
            assert call_kwargs["sizing_account"].equity == 99500.0
            assert call_kwargs["sizing_account"].free_margin == 95000.0
            assert call_kwargs["risk_context"] is None
            assert call_kwargs["risk_limits"].max_risk_percent == 1.0
            assert mock_forex_graph.run.call_args.kwargs["execution_timeframe"] == "H1"
            assert "manual estimate / not broker verified" in result.output
            assert "Execution policy: MANUAL ONLY" in result.output

    def test_run_forex_custom_analysts_and_risk(self, mock_forex_graph):
        with patch("tradingagents.forex.ForexTradingAgentsGraph", return_value=mock_forex_graph) as mock_cls:
            runner = CliRunner()
            today_str = datetime.date.today().isoformat()
            result = runner.invoke(
                m.app,
                [
                    "run",
                    "USDJPY",
                    "--date", today_str,
                    "--analysts", "technical,macro",
                    "--timeframe", "M15",
                    "--balance", "75000",
                    "--equity", "74000",
                    "--free-margin", "70000",
                    "--leverage", "50",
                    "--account-currency", "EUR",
                    "--account-source", "manual",
                    "--risk-pct", "1.5",
                ],
            )
            assert result.exit_code == 0, result.output
            assert "Decision: LONG" in result.output

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["selected_analysts"] == ["forex_technical", "forex_macro"]
            assert call_kwargs["sizing_account"].balance == 75000.0
            assert call_kwargs["risk_limits"].max_risk_percent == 1.5
            assert mock_forex_graph.run.call_args.kwargs["execution_timeframe"] == "M15"

    def test_run_forex_requires_explicit_account_source(self):
        runner = CliRunner()
        result = runner.invoke(m.app, ["run", "EURUSD"])
        assert result.exit_code == 1
        assert "account-source must be explicitly set" in result.output

    def test_run_forex_manual_mode_requires_complete_account(self):
        runner = CliRunner()
        result = runner.invoke(
            m.app,
            ["run", "EURUSD", "--account-source", "manual", "--balance", "10000"],
        )
        assert result.exit_code == 1
        assert "manual Forex mode requires" in result.output
        assert "free-margin" in result.output

    def test_run_forex_preserves_zero_free_margin(self, mock_forex_graph):
        with patch("tradingagents.forex.ForexTradingAgentsGraph", return_value=mock_forex_graph) as mock_cls:
            runner = CliRunner()
            args = self.manual_account_args()
            args[args.index("95000")] = "0"
            result = runner.invoke(m.app, ["run", "EURUSD", *args])
            assert result.exit_code == 0, result.output
            assert mock_cls.call_args.kwargs["sizing_account"].free_margin == 0.0

    def test_run_forex_mt5_uses_shared_authoritative_context(self, mock_forex_graph):
        observer = MagicMock()
        risk_context = MagicMock()
        risk_context.as_of_utc = datetime.datetime(
            2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc
        )
        application_context = SimpleNamespace(
            account=MagicMock(),
            risk_context=risk_context,
            broker_verified=True,
            assumptions=(),
        )
        with (
            patch("tradingagents.forex.ForexTradingAgentsGraph", return_value=mock_forex_graph) as mock_cls,
            patch("tradingagents.mt5.observer.MT5Observer", return_value=observer),
            patch(
                "tradingagents.forex.application.build_mt5_application_context",
                return_value=application_context,
            ) as build_context,
        ):
            runner = CliRunner()
            result = runner.invoke(
                m.app,
                [
                    "run", "EURJPY",
                    "--account-source", "mt5",
                    "--timeframe", "M15",
                    "--context-timeframes", "H1,H4",
                ],
            )

        assert result.exit_code == 0, result.output
        observer.connection.connect.assert_called_once_with()
        observer.connection.disconnect.assert_called_once_with()
        build_context.assert_called_once_with(
            observer,
            pair="EURJPY",
            execution_timeframe="M15",
        )
        assert mock_cls.call_args.kwargs["risk_context"] is risk_context
        assert mock_forex_graph.run.call_args.kwargs == {
            "trade_date": "2026-10-01T12:00:00+00:00",
            "execution_timeframe": "M15",
            "context_timeframes": ["H1", "H4"],
        }
        assert "broker verified" in result.output
        assert "broker_verified=true" in result.output

    def test_run_forex_rejects_invalid_analysts(self):
        runner = CliRunner()
        result = runner.invoke(
            m.app,
            ["run", "EURUSD", "--analysts", "fundamentals", *self.manual_account_args()],
        )
        assert result.exit_code == 1
        assert "Forex analysts must contain one or more of" in result.output
