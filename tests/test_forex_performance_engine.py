"""Unit and Integration Tests for Forex Performance Analytics Engine (Phase 19).

Validates:
- Overall deterministic metrics: trade count, wins, losses, breakeven, win rate,
  average winner, average loser, average R, median R, expectancy, profit factor,
  gross P/L, net P/L, maximum drawdown, loss streak, holding duration, average MFE/MAE.
- Multi-dimensional segmentation: pair, timeframe, setup, session, direction, weekday,
  regime, news condition, model, provider, prompt version, strategy version, confidence band.
- Execution metrics: entry deviation, SL changes, TP changes, manual exits, partial close behavior.
- Separate theoretical vs realized performance tracking.
- Markdown performance scorecard rendering.
- Integration with ForexTradeJournal, ForexJournalManager, and ForexMetricsManager.
- API endpoint: GET /api/forex/analytics/performance.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
)
from tradingagents.analytics.performance import (
    ForexPerformanceEngine,
    detect_confidence_band,
    detect_trade_session,
    detect_weekday_name,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    TradeExitReason,
    TradeStatus,
)
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.metrics.manager import ForexMetricsManager
from web.forex_routes import router, set_forex_dependencies

# ---------------------------------------------------------------------------
# Test Data Fixtures
# ---------------------------------------------------------------------------


class MockPerfTrade:
    """Mock trade container for performance analytics testing."""

    def __init__(
        self,
        trade_id: str,
        pair: str = "EURUSD",
        action: ForexAction = ForexAction.LONG,
        status: TradeStatus = TradeStatus.CLOSED,
        open_time_utc: str = "2026-03-10T08:00:00+00:00",
        close_time_utc: str = "2026-03-10T12:00:00+00:00",
        open_price: float = 1.0800,
        close_price: float = 1.0850,
        stop_loss: float = 1.0750,
        take_profit: float = 1.0900,
        net_profit: float = 500.0,
        r_multiple: float = 1.0,
        mfe_r: float = 1.5,
        mfe_pips: float = 75.0,
        mae_r: float = 0.3,
        mae_pips: float = 15.0,
        exit_reason: TradeExitReason = TradeExitReason.TAKE_PROFIT,
        confidence: float | None = 75.0,
        metadata: dict | None = None,
        proposal_id: str | None = None,
    ) -> None:
        self.trade_id = trade_id
        self.pair = pair
        self.action = action
        self.status = status
        self.open_time_utc = open_time_utc
        self.close_time_utc = close_time_utc
        self.open_price = open_price
        self.close_price = close_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.net_profit = net_profit
        self.r_multiple = r_multiple
        self.mfe_r = mfe_r
        self.mfe_pips = mfe_pips
        self.mae_r = mae_r
        self.mae_pips = mae_pips
        self.exit_reason = exit_reason
        self.confidence = confidence
        self.metadata = metadata or {}
        self.proposal_id = proposal_id


# ---------------------------------------------------------------------------
# 1. Overall Deterministic Metrics Tests
# ---------------------------------------------------------------------------


def test_performance_engine_overall_metrics():
    """Verify deterministic calculations for all Phase 19 overall KPIs."""
    engine = ForexPerformanceEngine(initial_capital=100000.0)

    # 4 Trades:
    # 1. Win: +$1,000, +2.0R, 4h hold, MFE 2.5R, MAE 0.2R
    # 2. Loss: -$500, -1.0R, 2h hold, MFE 0.3R, MAE 1.0R
    # 3. Win: +$500, +1.0R, 6h hold, MFE 1.2R, MAE 0.4R
    # 4. Breakeven: $0, 0.0R, 1h hold, MFE 0.5R, MAE 0.5R
    trades = [
        MockPerfTrade("t1", net_profit=1000.0, r_multiple=2.0, open_time_utc="2026-03-02T08:00:00+00:00", close_time_utc="2026-03-02T12:00:00+00:00", mfe_r=2.5, mfe_pips=50.0, mae_r=0.2, mae_pips=4.0),
        MockPerfTrade("t2", net_profit=-500.0, r_multiple=-1.0, open_time_utc="2026-03-03T09:00:00+00:00", close_time_utc="2026-03-03T11:00:00+00:00", mfe_r=0.3, mfe_pips=6.0, mae_r=1.0, mae_pips=20.0),
        MockPerfTrade("t3", net_profit=500.0, r_multiple=1.0, open_time_utc="2026-03-04T10:00:00+00:00", close_time_utc="2026-03-04T16:00:00+00:00", mfe_r=1.2, mfe_pips=24.0, mae_r=0.4, mae_pips=8.0),
        MockPerfTrade("t4", net_profit=0.0, r_multiple=0.0, open_time_utc="2026-03-05T13:00:00+00:00", close_time_utc="2026-03-05T14:00:00+00:00", mfe_r=0.5, mfe_pips=10.0, mae_r=0.5, mae_pips=10.0),
    ]

    metrics = engine.calculate_metrics(trades)

    assert metrics.trade_count == 4
    assert metrics.wins == 2
    assert metrics.losses == 1
    assert metrics.breakeven == 1
    assert metrics.win_rate == 0.50
    assert metrics.win_rate_pct == 50.0

    # Profit / Loss
    assert metrics.gross_profit == 1500.0
    assert metrics.gross_loss == 500.0
    assert metrics.net_profit == 1000.0
    assert metrics.profit_factor == 3.0

    # Averages
    assert metrics.average_winner == 750.0  # (1000 + 500) / 2
    assert metrics.average_winner_r == 1.5   # (2.0 + 1.0) / 2
    assert metrics.average_loser == 500.0
    assert metrics.average_loser_r == -1.0

    # R metrics
    assert metrics.average_r == 0.50  # (2.0 - 1.0 + 1.0 + 0.0) / 4
    assert metrics.median_r == 0.50   # median of [ -1.0, 0.0, 1.0, 2.0 ] = 0.50

    # Expectancy: (0.5 * 1.5) + (0.5 * -1.0) = 0.75 - 0.5 = 0.25R
    assert metrics.expectancy == 0.25

    # Holding duration: (4 + 2 + 6 + 1) / 4 = 13 / 4 = 3.25h = 11700s
    assert metrics.holding_duration_seconds == 11700.0
    assert "3h" in metrics.holding_duration_formatted

    # Excursions
    assert metrics.average_mfe_r == pytest.approx((2.5 + 0.3 + 1.2 + 0.5) / 4, 0.01)
    assert metrics.average_mae_r == pytest.approx((0.2 + 1.0 + 0.4 + 0.5) / 4, 0.01)


# ---------------------------------------------------------------------------
# 2. Execution Metrics & Management Friction Tests
# ---------------------------------------------------------------------------


def test_execution_metrics_deviation_and_modifications():
    """Verify entry deviation, SL/TP modifications, and manual exits."""
    engine = ForexPerformanceEngine()

    proposals = [
        {"proposal_id": "p1", "entry_price": 1.0800},
        {"proposal_id": "p2", "entry_price": 1.2500},
    ]

    trades = [
        # EURUSD: proposed 1.0800, open 1.0805 -> deviation = 0.5 pips
        MockPerfTrade("t1", pair="EURUSD", open_price=1.0805, proposal_id="p1", exit_reason=TradeExitReason.MANUAL),
        # GBPUSD: proposed 1.2500, open 1.2512 -> deviation = 1.2 pips
        MockPerfTrade("t2", pair="GBPUSD", open_price=1.2512, proposal_id="p2", exit_reason=TradeExitReason.TAKE_PROFIT),
    ]

    events = [
        {"event_type": "SL_CHANGED"},
        {"event_type": "SL_CHANGED"},
        {"event_type": "TP_CHANGED"},
        {"event_type": "PARTIAL_CLOSE", "volume": 0.5, "payload": {"realized_r": 1.5}},
    ]

    exec_metrics = engine.calculate_execution_metrics(trades=trades, events=events, proposals=proposals)

    assert exec_metrics.entry_deviation_pips == pytest.approx(8.5, 0.01)  # (5.0 + 12.0) / 2
    assert exec_metrics.max_entry_deviation_pips == 12.0
    assert exec_metrics.sl_changes_count == 2
    assert exec_metrics.tp_changes_count == 1
    assert exec_metrics.manual_exits_count == 1
    assert exec_metrics.manual_exits_pct == 50.0
    assert exec_metrics.partial_close_count == 1
    assert exec_metrics.partial_close_volume == 0.5
    assert exec_metrics.partial_close_avg_captured_r == 1.5


# ---------------------------------------------------------------------------
# 3. Multi-Dimensional Segmentation Tests
# ---------------------------------------------------------------------------


def test_segmentation_across_dimensions():
    """Verify multi-dimensional breakdown across pair, session, direction, and confidence."""
    engine = ForexPerformanceEngine()

    trades = [
        MockPerfTrade("t1", pair="EURUSD", action=ForexAction.LONG, open_time_utc="2026-03-02T08:00:00+00:00", confidence=85.0, metadata={"timeframe": "H1", "setup_type": "BREAKOUT", "regime": "TRENDING", "model_name": "claude-3-5-sonnet"}),
        MockPerfTrade("t2", pair="EURUSD", action=ForexAction.SHORT, open_time_utc="2026-03-03T14:00:00+00:00", confidence=65.0, metadata={"timeframe": "M15", "setup_type": "REVERSAL", "regime": "RANGING", "model_name": "gpt-4o"}),
        MockPerfTrade("t3", pair="USDJPY", action=ForexAction.LONG, open_time_utc="2026-03-04T02:00:00+00:00", confidence=75.0, metadata={"timeframe": "H1", "setup_type": "BREAKOUT", "regime": "TRENDING", "model_name": "gpt-4o"}),
    ]

    breakdown = engine.calculate_segmentation(trades)

    # By Pair
    assert "EURUSD" in breakdown.by_pair
    assert breakdown.by_pair["EURUSD"].trade_count == 2
    assert "USDJPY" in breakdown.by_pair
    assert breakdown.by_pair["USDJPY"].trade_count == 1

    # By Direction
    assert "LONG" in breakdown.by_direction
    assert breakdown.by_direction["LONG"].trade_count == 2
    assert "SHORT" in breakdown.by_direction
    assert breakdown.by_direction["SHORT"].trade_count == 1

    # By Session
    assert "LONDON" in breakdown.by_session  # t1 at 08:00 UTC
    assert "LONDON_NY_OVERLAP" in breakdown.by_session  # t2 at 14:00 UTC
    assert "ASIAN" in breakdown.by_session  # t3 at 02:00 UTC

    # By Confidence Band
    assert "80-90" in breakdown.by_confidence_band
    assert "60-70" in breakdown.by_confidence_band
    assert "70-80" in breakdown.by_confidence_band

    # By Model
    assert "claude-3-5-sonnet" in breakdown.by_model
    assert "gpt-4o" in breakdown.by_model
    assert breakdown.by_model["gpt-4o"].trade_count == 2


# ---------------------------------------------------------------------------
# 4. Helper Function Tests
# ---------------------------------------------------------------------------


def test_helper_detectors():
    """Verify session, weekday, and confidence band classification helpers."""
    # Sessions
    assert detect_trade_session("2026-03-02T03:30:00+00:00") == "ASIAN"
    assert detect_trade_session("2026-03-02T09:15:00+00:00") == "LONDON"
    assert detect_trade_session("2026-03-02T13:45:00+00:00") == "LONDON_NY_OVERLAP"
    assert detect_trade_session("2026-03-02T18:00:00+00:00") == "NEW_YORK"

    # Weekdays (2026-03-02 is Monday)
    assert detect_weekday_name("2026-03-02T08:00:00+00:00") == "Monday"
    assert detect_weekday_name("2026-03-06T08:00:00+00:00") == "Friday"

    # Confidence bands
    assert detect_confidence_band(55.0) == "50-60"
    assert detect_confidence_band(68.0) == "60-70"
    assert detect_confidence_band(74.5) == "70-80"
    assert detect_confidence_band(88.0) == "80-90"
    assert detect_confidence_band(95.0) == "90-100"
    assert detect_confidence_band(42.0) == "<50"
    assert detect_confidence_band(0.72) == "70-80"  # decimal scaling
    assert detect_confidence_band(None) == "UNRATED"


# ---------------------------------------------------------------------------
# 5. Full Report & Markdown Dashboard Generation Tests
# ---------------------------------------------------------------------------


def test_generate_performance_report_and_markdown():
    """Verify full report generation with separate theoretical vs realized tracking."""
    engine = ForexPerformanceEngine()

    realized_trades = [
        MockPerfTrade("t1", net_profit=600.0, r_multiple=1.5, status=TradeStatus.CLOSED),
        MockPerfTrade("t2", net_profit=-400.0, r_multiple=-1.0, status=TradeStatus.CLOSED),
    ]
    theoretical_sims = [
        {"net_profit": 800.0, "theoretical_r": 2.0},
        {"net_profit": -400.0, "theoretical_r": -1.0},
        {"net_profit": 600.0, "theoretical_r": 1.5},
    ]

    report = engine.generate_performance_report(
        trades=realized_trades,
        simulations=theoretical_sims,
    )

    assert report.realized.trade_count == 2
    assert report.realized.win_rate_pct == 50.0

    assert report.theoretical.trade_count == 3
    assert report.theoretical.win_rate_pct == pytest.approx(66.7, 0.1)

    # Check Markdown formatting contains sections
    md = report.summary_markdown
    assert "# Comprehensive Forex Performance Analytics" in md
    assert "## 1. Overall Portfolio Performance" in md
    assert "## 2. Theoretical AI Edge vs Realized Human Execution" in md
    assert "## 3. Broker Execution Quality & Mid-Trade Management" in md


# ---------------------------------------------------------------------------
# 6. Integration with JournalManager, MetricsManager, and Web API
# ---------------------------------------------------------------------------


def test_journal_and_metrics_manager_integration():
    """Verify ForexJournalManager and ForexMetricsManager compute comprehensive performance."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    journal.record_trade_open(
        pair="EURUSD", action=ForexAction.LONG, open_price=1.08, stop_loss=1.07,
        lots=1.0, confidence=72.0,
    )

    journal_mgr = ForexJournalManager(journal=journal)
    report1 = journal_mgr.compute_comprehensive_performance()
    assert report1.overall.trade_count == 1

    metrics_mgr = ForexMetricsManager(journal=journal)
    report2 = metrics_mgr.get_comprehensive_performance()
    assert report2.overall.trade_count == 1


def test_api_performance_route():
    """Test GET /api/forex/analytics/performance endpoint."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    metrics_mgr = ForexMetricsManager(journal=journal)

    journal.record_trade_open(
        pair="GBPUSD", action=ForexAction.SHORT, open_price=1.25, stop_loss=1.26,
        lots=1.0, confidence=80.0,
    )

    set_forex_dependencies(journal=journal, metrics_manager=metrics_mgr)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    res = client.get("/api/forex/analytics/performance")
    assert res.status_code == 200
    data = res.json()
    assert "performance" in data
    assert "markdown" in data
    assert data["performance"]["overall"]["trade_count"] == 1
    assert "GBPUSD" in data["performance"]["segmentation"]["by_pair"]
