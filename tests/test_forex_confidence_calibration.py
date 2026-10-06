"""Unit and Integration Tests for Confidence Calibration Engine (Phase 18).

Validates:
- Model confidence score persistence on ForexTraderProposal, ProposalRecord, and TradeJournalRecord.
- Regex and JSON parsing/normalization of model confidence (decimal 0.85 -> 85.0%).
- Discrete confidence buckets: 50-60, 60-70, 70-80, 80-90, 90-100.
- For each bucket: trade count, win rate, average R, expectancy.
- Never displaying model confidence as true probability unless calibrated.
- Small sample warnings when bucket sample sizes fall below threshold.
- Expected Calibration Error (ECE) and Brier score evaluation.
- Markdown calibration scorecard generation in ForexMetricsManager.
- SQLite migration and persistence in ForexTradeJournal.
- API endpoints: GET /api/forex/metrics/confidence-calibration, POST /api/forex/metrics/calibrate-confidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.agents.trader.forex_trader import parse_forex_proposal_from_text
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalRecord,
)
from tradingagents.metrics.confidence_calibration import (
    ConfidenceCalibrationEngine,
)
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.metrics.models import MetricsSummary
from web.forex_routes import router, set_forex_dependencies

# ---------------------------------------------------------------------------
# Test Data Fixtures & Mocks
# ---------------------------------------------------------------------------


class MockTradeItem:
    """Mock trade container for calibration testing."""

    def __init__(
        self,
        confidence: float | None,
        r_multiple: float,
        net_profit: float = 100.0,
    ) -> None:
        self.confidence = confidence
        self.r_multiple = r_multiple
        self.net_profit = net_profit
        self.status = "CLOSED"
        self.close_time_utc = "2026-03-10T12:00:00+00:00"


# ---------------------------------------------------------------------------
# 1. Model & Schema Persistence Tests
# ---------------------------------------------------------------------------


def test_proposal_schema_confidence_field():
    """Verify confidence field on ForexTraderProposal."""
    proposal = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.TREND_CONTINUATION,
        timeframe="H1",
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit_1=1.0950,
        confidence=82.5,
        reasoning="Bullish market structure",
    )
    assert proposal.confidence == 82.5

    # ProposalRecord round-trip
    record = ProposalRecord.from_forex_trader_proposal(proposal)
    assert record.confidence == 82.5

    reconstructed = record.to_forex_trader_proposal()
    assert reconstructed.confidence == 82.5


def test_parse_forex_proposal_confidence_normalization():
    """Verify decimal 0.75 is normalized to 75.0% in trader proposal parser."""
    text_json = """
    ```json
    {
        "pair": "GBPUSD",
        "action": "LONG",
        "entry_price": 1.2500,
        "stop_loss": 1.2450,
        "take_profit_1": 1.2600,
        "confidence": 0.85,
        "reasoning": "Strong trend continuation"
    }
    ```
    """
    prop = parse_forex_proposal_from_text(text_json, pair="GBPUSD")
    assert prop.action == ForexAction.LONG
    assert prop.confidence == 85.0

    # Markdown regex text parsing
    text_md = """
    Action: SHORT
    Entry: 1.2800
    SL: 1.2850
    TP1: 1.2700
    Model Confidence: 68.5%
    Reasoning: Resistance rejection
    """
    prop2 = parse_forex_proposal_from_text(text_md, pair="GBPUSD")
    assert prop2.action == ForexAction.SHORT
    assert prop2.confidence == 68.5


# ---------------------------------------------------------------------------
# 2. Database SQLite Persistence & Migration Tests
# ---------------------------------------------------------------------------


def test_sqlite_journal_confidence_persistence():
    """Verify confidence is persisted and read back from SQLite proposals and trades."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)

    proposal = ForexTraderProposal(
        pair="USDJPY",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=150.00,
        stop_loss=149.50,
        take_profit_1=151.00,
        confidence=77.0,
        reasoning="Support test",
    )

    prop_id = journal.save_proposal(proposal)
    loaded_prop = journal.get_proposal(prop_id)
    assert loaded_prop is not None
    assert loaded_prop.confidence == 77.0

    # Execute proposal into trade and verify confidence inheritance
    trade = journal.record_trade_open(
        pair="USDJPY",
        action=ForexAction.LONG,
        open_price=150.00,
        stop_loss=149.50,
        lots=1.0,
        proposal_id=prop_id,
    )
    assert trade.confidence == 77.0

    # Read back trade
    loaded_trade = journal.get_trade(trade.trade_id)
    assert loaded_trade is not None
    assert loaded_trade.confidence == 77.0


# ---------------------------------------------------------------------------
# 3. Confidence Bucketing & Metrics Computation Tests
# ---------------------------------------------------------------------------


def test_confidence_calibration_engine_buckets():
    """Verify standard buckets 50-60, 60-70, 70-80, 80-90, 90-100 are created."""
    engine = ConfidenceCalibrationEngine(min_samples=5)
    report = engine.compute_calibration([])

    expected_buckets = ["50-60", "60-70", "70-80", "80-90", "90-100"]
    for b in expected_buckets:
        assert b in report.buckets
        assert report.buckets[b].trade_count == 0
        assert report.buckets[b].win_rate == 0.0
        assert report.buckets[b].is_calibrated is False
        assert report.buckets[b].calibrated_win_probability is None


def test_bucket_metrics_trade_count_win_rate_average_r_expectancy():
    """Verify exact calculation of trade count, win rate, average R, and expectancy per bucket."""
    engine = ConfidenceCalibrationEngine(min_samples=3)

    # 4 trades in 70-80 bucket:
    # Trade 1: Win, +2.0R
    # Trade 2: Win, +1.5R
    # Trade 3: Loss, -1.0R
    # Trade 4: Breakeven, 0.0R
    trades_70_80 = [
        MockTradeItem(confidence=72.0, r_multiple=2.0),
        MockTradeItem(confidence=75.0, r_multiple=1.5),
        MockTradeItem(confidence=78.0, r_multiple=-1.0),
        MockTradeItem(confidence=79.9, r_multiple=0.0),
    ]

    report = engine.compute_calibration(trades_70_80)
    bucket_70 = report.buckets["70-80"]

    assert bucket_70.trade_count == 4
    assert bucket_70.win_count == 2
    assert bucket_70.loss_count == 1
    assert bucket_70.breakeven_count == 1

    # Win rate: 2 / 4 = 0.50 (50.0%)
    assert bucket_70.win_rate == 0.50
    assert bucket_70.win_rate_pct == 50.0

    # Average R: (2.0 + 1.5 - 1.0 + 0.0) / 4 = 2.5 / 4 = 0.625 -> round 0.62
    assert bucket_70.average_r == 0.62

    # Average Win R: (2.0 + 1.5) / 2 = 1.75
    assert bucket_70.average_win_r == 1.75
    # Average Loss R: -1.0
    assert bucket_70.average_loss_r == -1.0

    # Expectancy uses the explicit 25% loss rate; breakevens are not losses.
    assert bucket_70.expectancy == 0.62

    # Min samples was 3, and trade_count is 4 -> bucket IS calibrated
    assert bucket_70.is_calibrated is True
    assert bucket_70.calibrated_win_probability == 0.50
    assert bucket_70.small_sample_warning is None


# ---------------------------------------------------------------------------
# 4. Strict Small-Sample Warning & Non-Display of Probability Rule Tests
# ---------------------------------------------------------------------------


def test_never_display_uncalibrated_confidence_as_true_probability():
    """Verify that if sample size is below threshold, calibrated_win_probability is None."""
    engine = ConfidenceCalibrationEngine(min_samples=10)

    # 4 trades in 80-90 bucket (below threshold of 10)
    trades_80 = [
        MockTradeItem(confidence=85.0, r_multiple=2.0),
        MockTradeItem(confidence=82.0, r_multiple=1.5),
        MockTradeItem(confidence=88.0, r_multiple=-1.0),
        MockTradeItem(confidence=84.0, r_multiple=1.0),
    ]

    report = engine.compute_calibration(trades_80)
    bucket_80 = report.buckets["80-90"]

    assert bucket_80.trade_count == 4
    assert bucket_80.is_calibrated is False
    # CRITICAL RULE: Never display model confidence as true probability unless calibrated
    assert bucket_80.calibrated_win_probability is None
    assert bucket_80.small_sample_warning is not None
    assert "Small sample warning" in bucket_80.small_sample_warning
    assert "minimum 10 required" in bucket_80.small_sample_warning


def test_calibrate_score_display_string():
    """Verify calibrate_score formats safe display strings preventing misinterpretation."""
    engine = ConfidenceCalibrationEngine(min_samples=5)

    # Uncalibrated scenario
    result_uncalib = engine.calibrate_score(raw_confidence=85.0)
    assert result_uncalib.is_calibrated is False
    assert result_uncalib.calibrated_win_probability is None
    assert "UNCALIBRATED" in result_uncalib.display_string
    assert "Do NOT treat as true win probability" in result_uncalib.display_string

    # Calibrated scenario with 5 trades
    calib_trades = [MockTradeItem(confidence=85.0, r_multiple=1.5) for _ in range(5)]
    report_calib = engine.compute_calibration(calib_trades)
    result_calib = engine.calibrate_score(raw_confidence=85.0, report=report_calib)

    assert result_calib.is_calibrated is True
    assert result_calib.calibrated_win_probability == 1.0
    assert "Calibrated Win Probability: 100.0%" in result_calib.display_string
    assert "Bucket '80-90'" in result_calib.display_string


# ---------------------------------------------------------------------------
# 5. Expected Calibration Error (ECE) and Brier Score Tests
# ---------------------------------------------------------------------------


def test_ece_and_brier_score_computation():
    """Verify ECE and Brier score metrics when model confidence diverges from outcomes."""
    engine = ConfidenceCalibrationEngine(min_samples=2)

    # Overconfident model: 90% confidence on 2 losing trades
    trades = [
        MockTradeItem(confidence=90.0, r_multiple=-1.0),
        MockTradeItem(confidence=90.0, r_multiple=-1.0),
    ]

    report = engine.compute_calibration(trades)
    # Brier score: (0.90 - 0.0)^2 = 0.81
    assert report.brier_score == pytest.approx(0.81, abs=0.01)
    # ECE: midpoint of 90-100 is 95% (0.95), empirical win rate is 0.0 -> ECE = |0.95 - 0.0| = 0.95
    assert report.expected_calibration_error == pytest.approx(0.95, abs=0.01)


# ---------------------------------------------------------------------------
# 6. ForexMetricsManager Integration & Markdown Scorecard Tests
# ---------------------------------------------------------------------------


def test_metrics_manager_confidence_calibration():
    """Verify ForexMetricsManager produces calibration reports and Section 5 markdown."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)

    # Insert sample trades across buckets
    first = journal.record_trade_open(
        pair="EURUSD", action=ForexAction.LONG, open_price=1.08, stop_loss=1.07,
        lots=1.0, confidence=65.0,
    )
    second = journal.record_trade_open(
        pair="EURUSD", action=ForexAction.SHORT, open_price=1.09, stop_loss=1.10,
        lots=1.0, confidence=75.0,
    )

    journal.record_trade_close(first.trade_id, close_price=1.09)
    journal.record_trade_close(second.trade_id, close_price=1.08)
    manager = ForexMetricsManager(journal=journal)
    calib_report = manager.get_confidence_calibration(min_samples=1)

    assert calib_report.total_trades_analyzed == 2
    assert calib_report.trades_with_confidence == 2
    assert "60-70" in calib_report.buckets
    assert "70-80" in calib_report.buckets

    # Check dashboard markdown includes Section 5
    summary = MetricsSummary(
        total_trades_analyzed=2,
        avg_realized_r=0.5,
        avg_mfe_r=1.2,
        avg_mae_r=0.4,
        avg_runup_efficiency_pct=60.0,
        avg_drawdown_efficiency_pct=40.0,
        avg_exit_efficiency_pct=70.0,
        total_executions_analyzed=2,
        avg_execution_quality_score=90.0,
    )
    markdown = manager.render_markdown_dashboard(summary=summary, confidence_report=calib_report)

    assert "## 5. Model Confidence Calibration & Probabilistic Reliability" in markdown
    assert "Model Confidence Calibration Scorecard" in markdown
    assert "50-60%" in markdown
    assert "60-70%" in markdown


# ---------------------------------------------------------------------------
# 7. FastAPI Route Endpoints Tests
# ---------------------------------------------------------------------------


def test_api_confidence_calibration_routes():
    """Test GET /api/forex/metrics/confidence-calibration and POST /api/forex/metrics/calibrate-confidence."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    manager = ForexMetricsManager(journal=journal)

    # Populate 3 trades in 60-70 bucket
    for _ in range(3):
        opened = journal.record_trade_open(
            pair="GBPUSD", action=ForexAction.LONG, open_price=1.25, stop_loss=1.24,
            lots=1.0, confidence=64.0,
        )
        journal.record_trade_close(opened.trade_id, close_price=1.26)

    set_forex_dependencies(journal=journal, metrics_manager=manager)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    from web.server import _SESSION_TOKEN, DASHBOARD_API_KEY
    client = TestClient(app, headers={"X-API-Key": DASHBOARD_API_KEY or _SESSION_TOKEN})

    # GET /api/forex/metrics/confidence-calibration
    res_get = client.get("/api/forex/metrics/confidence-calibration?min_samples=2")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert "report" in data_get
    assert "markdown" in data_get
    buckets = data_get["report"]["buckets"]
    assert "60-70" in buckets
    assert buckets["60-70"]["trade_count"] == 3
    assert buckets["60-70"]["is_calibrated"] is True

    # POST /api/forex/metrics/calibrate-confidence
    res_post = client.post(
        "/api/forex/metrics/calibrate-confidence",
        json={"confidence": 64.0, "pair": "GBPUSD", "min_samples": 2},
    )
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert "calibrated_result" in data_post
    result = data_post["calibrated_result"]
    assert result["raw_model_confidence"] == 64.0
    assert result["bucket_label"] == "60-70"
    assert "display_string" in result


def test_confidence_calibration_excludes_open_trades():
    engine = ConfidenceCalibrationEngine(min_samples=1)
    closed = MockTradeItem(confidence=75.0, r_multiple=1.0)
    open_trade = MockTradeItem(confidence=75.0, r_multiple=0.0, net_profit=0.0)
    open_trade.status = "OPEN"
    open_trade.close_time_utc = None

    report = engine.compute_calibration([closed, open_trade])

    assert report.total_trades_analyzed == 1
    assert report.buckets["70-80"].trade_count == 1
    assert report.buckets["70-80"].breakeven_count == 0

def test_confidence_calibration_ui_uses_empirical_labels_and_small_sample_state():
    source = Path("web/static/app.js").read_text(encoding="utf-8")
    html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert "Observed win rate" in source
    assert "Calibration gap" in source
    assert "SMALL SAMPLE" in source
    assert "raw model scores" in html
    assert "not a forecast probability" in html
