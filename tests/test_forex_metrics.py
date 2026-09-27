"""Unit & Integration Tests for Forex Metrics, MFE/MAE, Outcome Engine & Execution Analytics (Phase 16)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.metrics.execution import (
    ExecutionQualityAnalyzer,
    calculate_execution_quality_score,
    calculate_execution_slippage,
    calculate_slippage_cost,
    calculate_spread_cost,
)
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.metrics.mfe_mae import (
    calculate_mfe_mae,
)
from tradingagents.metrics.models import (
    ExecutionQuality,
    SlippageType,
    TradeMfeMae,
    TradeOutcomeCategory,
    TradeOutcomeResult,
)
from tradingagents.metrics.outcome import TradeOutcomeEngine

# ===========================================================================
# 1. Model & Enum Tests
# ===========================================================================


def test_models_and_enums_instantiation():
    """Verify serialization and validation of Phase 16 metric models."""
    outcome_cat = TradeOutcomeCategory.from_str("premature_exit")
    assert outcome_cat == TradeOutcomeCategory.PREMATURE_EXIT

    outcome_res = TradeOutcomeResult(
        trade_id="trd_123",
        category=TradeOutcomeCategory.PERFECT_EXIT,
        efficiency_score=95.5,
        title="Test Perfect Exit",
        description="Great trade",
        tags=["optimal"],
        recommendations=["Keep doing this"],
    )
    assert outcome_res.category == TradeOutcomeCategory.PERFECT_EXIT
    assert outcome_res.efficiency_score == 95.5

    eq = ExecutionQuality(
        trade_id="trd_123",
        pair="EURUSD",
        action=ForexAction.LONG,
        requested_price=1.0800,
        fill_price=1.0801,
        slippage_pips=1.0,
        slippage_type=SlippageType.ADVERSE,
        slippage_cost_usd=10.0,
    )
    assert eq.slippage_type == SlippageType.ADVERSE
    assert eq.quality_score == 100.0


# ===========================================================================
# 2. MFE / MAE Calculation Tests (Long, Short, JPY)
# ===========================================================================


def test_mfe_mae_long_trade():
    """Verify MFE/MAE calculation for a LONG trade on EURUSD."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0820, low=1.0780, close=1.0810, volume=100.0),
        ForexBar(timestamp=t1, open=1.0810, high=1.0900, low=1.0805, close=1.0870, volume=150.0),
        ForexBar(timestamp=t2, open=1.0870, high=1.0890, low=1.0840, close=1.0880, volume=120.0),
    ]

    # Long: Entry 1.0800, SL 1.0750 (50 pips risk), Exit 1.0880 (+80 pips profit)
    # Peak High: 1.0900 (MFE = 100 pips = 2.0R)
    # Deepest Low: 1.0780 (MAE = 20 pips = 0.4R)
    res = calculate_mfe_mae(
        open_price=1.0800,
        stop_loss=1.0750,
        action=ForexAction.LONG,
        pair="EURUSD",
        candles=candles,
        close_price=1.0880,
        open_time_utc=t0,
        close_time_utc=t2,
        trade_id="trd_long_1",
    )

    assert res.trade_id == "trd_long_1"
    assert res.mfe_price == 1.0900
    assert res.mae_price == 1.0780
    assert res.mfe_pips == 100.0
    assert res.mae_pips == 20.0
    assert res.mfe_r == 2.00
    assert res.mae_r == 0.40
    assert res.realized_pips == 80.0
    assert res.realized_r == 1.60
    assert res.runup_efficiency_pct == 80.0  # 80 pips realized / 100 pips peak runup
    assert res.drawdown_efficiency_pct == 60.0  # (50 - 20) / 50 * 100
    # Exit efficiency: (1.0880 - 1.0780) / (1.0900 - 1.0780) = 0.0100 / 0.0120 = 83.3%
    assert 83.0 <= res.exit_efficiency_pct <= 83.5
    assert res.mfe_time_utc == t1.isoformat()
    assert res.mae_time_utc == t0.isoformat()


def test_mfe_mae_short_trade():
    """Verify directional symmetry of MFE/MAE calculation for a SHORT trade."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0820, low=1.0780, close=1.0790, volume=100.0),
        ForexBar(timestamp=t1, open=1.0790, high=1.0795, low=1.0700, close=1.0730, volume=150.0),
        ForexBar(timestamp=t2, open=1.0730, high=1.0750, low=1.0710, close=1.0720, volume=120.0),
    ]

    # Short: Entry 1.0800, SL 1.0850 (50 pips risk), Exit 1.0720 (+80 pips profit)
    # Peak Low (favorable for short): 1.0700 (MFE = 100 pips = 2.0R)
    # Deepest High (adverse for short): 1.0820 (MAE = 20 pips = 0.4R)
    res = calculate_mfe_mae(
        open_price=1.0800,
        stop_loss=1.0850,
        action=ForexAction.SHORT,
        pair="EURUSD",
        candles=candles,
        close_price=1.0720,
        open_time_utc=t0,
        close_time_utc=t2,
        trade_id="trd_short_1",
    )

    assert res.trade_id == "trd_short_1"
    assert res.mfe_price == 1.0700
    assert res.mae_price == 1.0820
    assert res.mfe_pips == 100.0
    assert res.mae_pips == 20.0
    assert res.mfe_r == 2.00
    assert res.mae_r == 0.40
    assert res.realized_pips == 80.0
    assert res.realized_r == 1.60
    assert res.runup_efficiency_pct == 80.0
    assert res.drawdown_efficiency_pct == 60.0
    assert 83.0 <= res.exit_efficiency_pct <= 83.5


def test_mfe_mae_jpy_pair_three_digits():
    """Verify pip calculation using 0.01 convention for JPY pairs."""
    candles = [
        {"high": 151.25, "low": 149.80, "timestamp": "2026-03-10T12:00:00Z"},
    ]
    # Long USDJPY: Entry 150.00, SL 149.50 (50 pips risk)
    # High 151.25 (MFE = 125 pips = 2.5R), Low 149.80 (MAE = 20 pips = 0.4R)
    # Close 151.00 (+100 pips = +2.0R)
    res = calculate_mfe_mae(
        open_price=150.00,
        stop_loss=149.50,
        action=ForexAction.LONG,
        pair="USDJPY",
        candles=candles,
        close_price=151.00,
    )
    assert res.mfe_pips == 125.0
    assert res.mae_pips == 20.0
    assert res.mfe_r == 2.50
    assert res.mae_r == 0.40
    assert res.realized_pips == 100.0
    assert res.realized_r == 2.00
    assert res.runup_efficiency_pct == 80.0


def test_mfe_mae_timestamp_window_filtering():
    """Verify that candles outside the trade's open/close window are excluded."""
    t_before = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
    t_open = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    t_during = datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
    t_after = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)

    candles = [
        ForexBar(timestamp=t_before, open=1.0700, high=1.0990, low=1.0650, close=1.0750, volume=10.0),
        ForexBar(timestamp=t_during, open=1.0800, high=1.0850, low=1.0790, close=1.0830, volume=10.0),
        ForexBar(timestamp=t_after, open=1.0830, high=1.1100, low=1.0500, close=1.0900, volume=10.0),
    ]

    res = calculate_mfe_mae(
        open_price=1.0800,
        stop_loss=1.0750,
        action=ForexAction.LONG,
        pair="EURUSD",
        candles=candles,
        close_price=1.0830,
        open_time_utc=t_open,
        close_time_utc=t_close,
    )
    # The extreme candle before (1.0990) and after (1.1100) must NOT bleed in
    assert res.mfe_price == 1.0850
    assert res.mae_price == 1.0790
    assert res.mfe_pips == 50.0
    assert res.mae_pips == 10.0


def test_mfe_mae_empty_candles_fallback():
    """Verify graceful handling when no candles are provided."""
    res = calculate_mfe_mae(
        open_price=1.0800,
        stop_loss=1.0750,
        action=ForexAction.LONG,
        pair="EURUSD",
        candles=[],
        close_price=1.0850,
    )
    assert res.candle_count == 0
    assert res.mfe_pips == 50.0
    assert res.realized_pips == 50.0


# ===========================================================================
# 3. Trade Outcome Classification Tests
# ===========================================================================


def test_outcome_engine_perfect_exit():
    """Verify classification of an optimal exit capturing >= 80% runup."""
    mfe_mae = TradeMfeMae(
        trade_id="t_perfect",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0890,
        stop_loss=1.0750,
        mfe_price=1.0900,
        mae_price=1.0790,
        mfe_pips=100.0,
        mae_pips=10.0,
        mfe_r=2.0,
        mae_r=0.2,
        realized_pips=90.0,
        realized_r=1.8,
        runup_efficiency_pct=90.0,
        drawdown_efficiency_pct=80.0,
        exit_efficiency_pct=90.9,
    )
    outcome = TradeOutcomeEngine.classify_outcome(mfe_mae)
    assert outcome.category == TradeOutcomeCategory.PERFECT_EXIT
    assert outcome.efficiency_score >= 90.0
    assert "perfect_exit" in outcome.tags


def test_outcome_engine_premature_exit():
    """Verify classification of a premature exit leaving money on the table."""
    mfe_mae = TradeMfeMae(
        trade_id="t_premature",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0825,
        stop_loss=1.0750,
        mfe_price=1.0950,
        mae_price=1.0790,
        mfe_pips=150.0,
        mae_pips=10.0,
        mfe_r=3.0,
        mae_r=0.2,
        realized_pips=25.0,
        realized_r=0.5,
        runup_efficiency_pct=16.7,  # < 45%
        drawdown_efficiency_pct=80.0,
        exit_efficiency_pct=21.9,
    )
    outcome = TradeOutcomeEngine.classify_outcome(mfe_mae)
    assert outcome.category == TradeOutcomeCategory.PREMATURE_EXIT
    assert "left_money_on_table" in outcome.tags


def test_outcome_engine_greedy_exit():
    """Verify classification of an unharvested profit reversal."""
    mfe_mae = TradeMfeMae(
        trade_id="t_greedy",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0802,
        stop_loss=1.0750,
        mfe_price=1.0900,
        mae_price=1.0760,
        mfe_pips=100.0,
        mae_pips=40.0,
        mfe_r=2.0,  # >= 1.5R favorable
        mae_r=0.8,
        realized_pips=2.0,
        realized_r=0.04,  # Closed at scratch
        runup_efficiency_pct=2.0,
        drawdown_efficiency_pct=20.0,
        exit_efficiency_pct=30.0,
    )
    outcome = TradeOutcomeEngine.classify_outcome(mfe_mae)
    assert outcome.category == TradeOutcomeCategory.GREEDY_EXIT
    assert "unharvested_profit" in outcome.tags


def test_outcome_engine_runaway_loss():
    """Verify classification of a stop breach runaway loss."""
    mfe_mae = TradeMfeMae(
        trade_id="t_runaway",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0735,
        stop_loss=1.0750,
        mfe_price=1.0810,
        mae_price=1.0735,
        mfe_pips=10.0,
        mae_pips=65.0,
        mfe_r=0.2,
        mae_r=1.30,  # > 1.05R stop breach
        realized_pips=-65.0,
        realized_r=-1.30,
        runup_efficiency_pct=0.0,
        drawdown_efficiency_pct=0.0,
        exit_efficiency_pct=0.0,
    )
    outcome = TradeOutcomeEngine.classify_outcome(mfe_mae)
    assert outcome.category == TradeOutcomeCategory.RUNAWAY_LOSS
    assert "stop_breach" in outcome.tags


def test_outcome_engine_standard_win_and_loss():
    """Verify standard win, standard loss, and breakeven classifications."""
    # Standard win
    win_mfe = TradeMfeMae(
        trade_id="t_win",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0860,
        stop_loss=1.0750,
        mfe_price=1.0880,
        mae_price=1.0785,
        mfe_pips=80.0,
        mae_pips=15.0,
        mfe_r=1.6,
        mae_r=0.3,
        realized_pips=60.0,
        realized_r=1.2,
        runup_efficiency_pct=75.0,
        drawdown_efficiency_pct=70.0,
        exit_efficiency_pct=78.9,
    )
    assert TradeOutcomeEngine.classify_outcome(win_mfe).category == TradeOutcomeCategory.STANDARD_WIN

    # Standard loss
    loss_mfe = TradeMfeMae(
        trade_id="t_loss",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0750,
        stop_loss=1.0750,
        mfe_price=1.0815,
        mae_price=1.0750,
        mfe_pips=15.0,
        mae_pips=50.0,
        mfe_r=0.3,
        mae_r=1.0,
        realized_pips=-50.0,
        realized_r=-1.0,
        runup_efficiency_pct=0.0,
        drawdown_efficiency_pct=0.0,
        exit_efficiency_pct=0.0,
    )
    assert TradeOutcomeEngine.classify_outcome(loss_mfe).category == TradeOutcomeCategory.STANDARD_LOSS

    # Breakeven
    be_mfe = TradeMfeMae(
        trade_id="t_be",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0802,
        stop_loss=1.0750,
        mfe_price=1.0830,
        mae_price=1.0785,
        mfe_pips=30.0,
        mae_pips=15.0,
        mfe_r=0.6,
        mae_r=0.3,
        realized_pips=2.0,
        realized_r=0.04,
        runup_efficiency_pct=6.7,
        drawdown_efficiency_pct=70.0,
        exit_efficiency_pct=37.8,
    )
    assert TradeOutcomeEngine.classify_outcome(be_mfe).category == TradeOutcomeCategory.BREAKEVEN


# ===========================================================================
# 4. Execution Quality & Slippage Analytics Tests
# ===========================================================================


def test_execution_slippage_calculation():
    """Verify directional slippage calculation for Buy and Sell orders."""
    # BUY: requested 1.08000, filled 1.08015 -> adverse slippage (+1.5 pips)
    slip, stype = calculate_execution_slippage(1.08000, 1.08015, ForexAction.LONG, "EURUSD")
    assert slip == 1.5
    assert stype == SlippageType.ADVERSE

    # BUY: requested 1.08000, filled 1.07980 -> price improvement (-2.0 pips)
    slip, stype = calculate_execution_slippage(1.08000, 1.07980, ForexAction.LONG, "EURUSD")
    assert slip == -2.0
    assert stype == SlippageType.IMPROVEMENT

    # SELL: requested 1.08000, filled 1.07985 -> adverse slippage (+1.5 pips)
    slip, stype = calculate_execution_slippage(1.08000, 1.07985, ForexAction.SHORT, "EURUSD")
    assert slip == 1.5
    assert stype == SlippageType.ADVERSE

    # SELL: requested 1.08000, filled 1.08020 -> price improvement (-2.0 pips)
    slip, stype = calculate_execution_slippage(1.08000, 1.08020, ForexAction.SHORT, "EURUSD")
    assert slip == -2.0
    assert stype == SlippageType.IMPROVEMENT

    # EXACT fill
    slip, stype = calculate_execution_slippage(1.08000, 1.08000, ForexAction.LONG, "EURUSD")
    assert slip == 0.0
    assert stype == SlippageType.EXACT


def test_execution_cost_and_scoring():
    """Verify slippage cost, spread cost, and execution quality scoring."""
    # 1.5 pips on 1.0 lot EURUSD = $15.00
    cost = calculate_slippage_cost(1.5, 1.0, "EURUSD", "USD")
    assert cost == 15.0

    # 1.0 pip spread on 0.5 lots EURUSD = $5.00
    spread_cost = calculate_spread_cost(1.0, 0.5, "EURUSD", "USD")
    assert spread_cost == 5.0

    # Zero slippage, tight spread -> score 100
    score_clean = calculate_execution_quality_score(slippage_pips=0.0, spread_pips=1.0, execution_delay_ms=80.0)
    assert score_clean == 100.0

    # Adverse slippage 2.0 pips, wide spread 2.5 pips, delay 800ms -> heavy penalty
    score_poor = calculate_execution_quality_score(slippage_pips=2.0, spread_pips=2.5, execution_delay_ms=800.0)
    assert score_poor < 60.0


def test_execution_analyzer_benchmarking():
    """Verify broker deal benchmarking across pairs and sessions."""
    analyzer = ExecutionQualityAnalyzer(account_currency="USD")

    eq1 = analyzer.analyze_execution(
        trade_id="t1",
        pair="EURUSD",
        action=ForexAction.LONG,
        requested_price=1.08000,
        fill_price=1.08005,  # 0.5 pips adverse
        volume=1.0,
        spread_at_open_pips=1.0,
        session="LONDON",
    )
    eq2 = analyzer.analyze_execution(
        trade_id="t2",
        pair="EURUSD",
        action=ForexAction.SHORT,
        requested_price=1.08000,
        fill_price=1.08010,  # 1.0 pips improvement
        volume=1.0,
        spread_at_open_pips=1.2,
        session="NEW_YORK",
    )

    benchmark = analyzer.benchmark_broker_execution([eq1, eq2])
    assert benchmark["total_executions"] == 2
    assert benchmark["adverse_fill_pct"] == 50.0
    assert benchmark["price_improvement_pct"] == 50.0
    assert "EURUSD" in benchmark["by_pair"]


# ===========================================================================
# 5. ForexMetricsManager Integration Tests
# ===========================================================================


def test_metrics_manager_integration(tmp_path: Path):
    """End-to-end integration test of ForexMetricsManager with ForexTradeJournal."""
    db_file = tmp_path / "test_metrics.db"
    journal = ForexTradeJournal(db_path=db_file)
    manager = ForexMetricsManager(journal=journal)

    # 1. Open and close a trade in journal
    rec = journal.record_trade_open(
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        take_profit=1.0950,
        lots=1.0,
    )
    journal.record_trade_close(
        trade_id=rec.trade_id,
        close_price=1.0880,
        exit_reason=TradeExitReason.TAKE_PROFIT,
    )

    # 2. Provide candles
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0890, low=1.0785, close=1.0880, volume=50.0),
    ]

    # 3. Analyze trade
    mfe_mae, outcome = manager.analyze_trade(rec.trade_id, candles)
    assert mfe_mae.mfe_pips == 90.0
    assert mfe_mae.mae_pips == 15.0
    assert outcome.category in (TradeOutcomeCategory.PERFECT_EXIT, TradeOutcomeCategory.STANDARD_WIN)

    # 4. Record execution deal
    exec_rec = manager.analyze_execution(
        trade_id=rec.trade_id,
        pair="EURUSD",
        action=ForexAction.LONG,
        requested_price=1.0800,
        fill_price=1.0801,
        volume=1.0,
        spread_at_open_pips=1.2,
    )

    # 5. Compute summary & render dashboard
    trades = journal.list_trades()
    summary = manager.compute_summary(
        trades=trades,
        candles_by_trade={rec.trade_id: candles},
        executions=[exec_rec],
    )
    assert summary.total_trades_analyzed == 1
    assert summary.total_executions_analyzed == 1
    assert summary.avg_mfe_pips == 90.0

    markdown = manager.render_markdown_dashboard(summary)
    assert "# Forex MFE/MAE & Execution Quality Dashboard" in markdown
    assert "Runup Capture Efficiency" in markdown
    assert "Execution Quality Score" in markdown
