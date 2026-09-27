"""Unit and integration tests for first-class Forex CLI workflow (Phase 5).

Validates:
- Forex pair detection and routing to ForexTradingAgentsGraph.
- Non-interactive CLI command: 'tradingagents run EURUSD'.
- Custom Forex analyst subsets (--analysts technical,macro).
- Custom risk and timeframe settings (--timeframe M15 --balance 50000 --risk-pct 1.5).
- Error handling on invalid analysts or broken configurations.
"""

from __future__ import annotations

import datetime
from pathlib import Path
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
        mock_graph.run.return_value = ({"final_trade_decision": "Approved LONG"}, "LONG")
        mock_graph.save_reports.return_value = tmp_path / "forex_report.md"
        return mock_graph

    def test_run_forex_default_pipeline(self, mock_forex_graph):
        with patch("tradingagents.forex.ForexTradingAgentsGraph", return_value=mock_forex_graph) as mock_cls:
            runner = CliRunner()
            today_str = datetime.date.today().isoformat()
            result = runner.invoke(m.app, ["run", "EURUSD", "--date", today_str])
            assert result.exit_code == 0, result.output
            assert "Decision: LONG" in result.output
            assert "Report:" in result.output

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["selected_analysts"] == ["forex_technical", "forex_macro", "forex_news"]
            assert call_kwargs["sizing_account"].balance == 100000.0
            assert call_kwargs["risk_limits"].max_risk_percent == 1.0

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

    def test_run_forex_rejects_invalid_analysts(self):
        runner = CliRunner()
        result = runner.invoke(m.app, ["run", "EURUSD", "--analysts", "fundamentals"])
        assert result.exit_code == 1
        assert "Forex analysts must contain one or more of" in result.output
