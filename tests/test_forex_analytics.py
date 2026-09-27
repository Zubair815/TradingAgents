"""Unit & Integration Tests for Institutional Forex Analytics (Phase 19).

Validates:
- Deep risk metrics: Calmar ratio, Ulcer Index, SQN, Recovery factor, Gain-to-Pain, underwater episodes.
- Monte Carlo simulation: bootstrap resampling, ruin probabilities, drawdown percentiles, confidence intervals.
- Empirical Stop & Target calibration: MAE survival curves, MFE crest expectancy, ATR multipliers.
- Multi-agent and signal ablation engine: marginal deltas, importance scoring, key insights.
- Executive analytics dashboard & institutional markdown scorecard rendering.
- Integration with ForexTradeJournal SQLite persistence and ForexBacktestResult.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.analytics import (
    DrawdownPercentiles,
    ForexAblationEngine,
    ForexAnalyticsDashboard,
    ForexAnalyticsManager,
    MonteCarloConfig,
    MonteCarloResult,
    MonteCarloSimulator,
    RiskStopCalibrator,
    SQNRating,
    TerminalWealthPercentiles,
    calculate_deep_metrics,
)
from tradingagents.backtest.forex_engine import (
    run_forex_backtest,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex import (
    ForexAnalyticsManager as FxManagerFromForex,
    MonteCarloSimulator as McFromForex,
    RiskStopCalibrator as CalibFromForex,
)

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class MockTrade:
    """Mock trade container for analytics testing."""

    def __init__(
        self,
        trade_id: str,
        net_profit: float,
        r_multiple: float,
        pips_gained: float,
        mae_pips: float = 10.0,
        mfe_pips: float = 25.0,
        mfe_r: float = 2.0,
        mae_r: float = 0.8,
        pair: str = "EURUSD",
        lots: float = 0.1,
        status: TradeStatus = TradeStatus.CLOSED,
    ):
        self.trade_id = trade_id
        self.net_profit = net_profit
        self.r_multiple = r_multiple
        self.pips_gained = pips_gained
        self.mae_pips = mae_pips
        self.mfe_pips = mfe_pips
        self.mfe_r = mfe_r
        self.mae_r = mae_r
        self.pair = pair
        self.lots = lots
        self.status = status


def make_sample_trades() -> list[MockTrade]:
    """Generate a realistic distribution of closed trades."""
    return [
        MockTrade("t1", net_profit=150.0, r_multiple=1.8, pips_gained=30.0, mae_pips=8.0, mfe_pips=35.0, mfe_r=2.1),
        MockTrade("t2", net_profit=-80.0, r_multiple=-1.0, pips_gained=-16.0, mae_pips=17.0, mfe_pips=5.0, mfe_r=0.3),
        MockTrade("t3", net_profit=220.0, r_multiple=2.5, pips_gained=44.0, mae_pips=6.0, mfe_pips=48.0, mfe_r=2.7),
        MockTrade("t4", net_profit=110.0, r_multiple=1.4, pips_gained=22.0, mae_pips=11.0, mfe_pips=28.0, mfe_r=1.75),
        MockTrade("t5", net_profit=-85.0, r_multiple=-1.0, pips_gained=-17.0, mae_pips=18.0, mfe_pips=4.0, mfe_r=0.25),
        MockTrade("t6", net_profit=190.0, r_multiple=2.2, pips_gained=38.0, mae_pips=9.0, mfe_pips=42.0, mfe_r=2.4),
        MockTrade("t7", net_profit=-75.0, r_multiple=-0.9, pips_gained=-15.0, mae_pips=16.0, mfe_pips=6.0, mfe_r=0.35),
        MockTrade("t8", net_profit=130.0, r_multiple=1.6, pips_gained=26.0, mae_pips=7.0, mfe_pips=32.0, mfe_r=1.9),
    ]


# ---------------------------------------------------------------------------
# 1. Models & Classification Tests
# ---------------------------------------------------------------------------


def test_models_instantiation_and_forex_root_exports():
    """Verify models instantiate properly and top-level exports in tradingagents.forex work."""
    assert FxManagerFromForex is ForexAnalyticsManager
    assert McFromForex is MonteCarloSimulator
    assert CalibFromForex is RiskStopCalibrator

    cfg = MonteCarloConfig(trials=500, random_seed=123)
    assert cfg.trials == 500
    assert cfg.random_seed == 123

    res = MonteCarloResult(
        trials=500,
        initial_capital=100000.0,
        ruin_probabilities={"20%": 0.05},
        drawdown_percentiles=DrawdownPercentiles(p50=6.5, worst_case=14.2),
        terminal_wealth_percentiles=TerminalWealthPercentiles(p50=105000.0),
        profit_confidence_interval=(2000.0, 8000.0),
        sharpe_confidence_interval=(1.2, 2.1),
        median_terminal_equity=105000.0,
        profitable_trials_pct=92.5,
    )
    assert res.trials == 500
    assert res.profitable_trials_pct == 92.5


def test_sqn_rating_classification():
    """Verify Van Tharp SQN rating classification boundaries."""
    assert SQNRating.from_sqn(1.2) == SQNRating.POOR
    assert SQNRating.from_sqn(1.8) == SQNRating.AVERAGE
    assert SQNRating.from_sqn(2.3) == SQNRating.GOOD
    assert SQNRating.from_sqn(2.8) == SQNRating.EXCELLENT
    assert SQNRating.from_sqn(3.5) == SQNRating.SUPERB
    assert SQNRating.from_sqn(5.5) == SQNRating.HOLY_GRAIL


# ---------------------------------------------------------------------------
# 2. Deep Metrics & Underwater Analysis
# ---------------------------------------------------------------------------


def test_calculate_deep_metrics():
    """Verify Calmar, Ulcer Index, SQN, Gain-to-Pain, and drawdown episode calculations."""
    # Equity path: 100k -> 102k -> 99k (peak 102k, dd 3k = 2.94%) -> 101k -> 105k (recovered) -> 103k
    curve = [100000.0, 102000.0, 99000.0, 101000.0, 105000.0, 103000.0]
    trades = make_sample_trades()

    metrics = calculate_deep_metrics(equity_curve=curve, trades=trades, initial_capital=100000.0)

    # 1. Calmar Ratio
    assert metrics.calmar_ratio > 0.0
    # 2. Ulcer Index
    assert metrics.ulcer_index > 0.0
    assert metrics.ulcer_performance_index > 0.0
    # 3. Recovery Factor
    assert metrics.recovery_factor > 0.0
    # 4. SQN
    assert metrics.system_quality_number > 0.0
    assert isinstance(metrics.sqn_rating, SQNRating)
    # 5. Gain-to-Pain
    assert metrics.gain_to_pain_ratio > 0.0

    # 6. Underwater Drawdown Episodes
    assert len(metrics.underwater_episodes) == 2
    ep1 = metrics.underwater_episodes[0]
    assert ep1.peak_index == 1
    assert ep1.trough_index == 2
    assert ep1.recovery_index == 4
    assert ep1.peak_equity == 102000.0
    assert ep1.trough_equity == 99000.0
    assert ep1.drawdown_cash == 3000.0
    assert ep1.is_recovered is True
    assert ep1.duration_bars == 3  # index 4 - index 1

    ep2 = metrics.underwater_episodes[1]
    assert ep2.is_recovered is False  # Terminal drawdown at 103k under peak 105k


def test_calculate_deep_metrics_empty():
    """Verify empty or single-point curve returns safe zeroed metrics."""
    empty_res = calculate_deep_metrics(equity_curve=[])
    assert empty_res.calmar_ratio == 0.0
    assert empty_res.ulcer_index == 0.0
    assert len(empty_res.underwater_episodes) == 0

    flat_res = calculate_deep_metrics(equity_curve=[100000.0, 100000.0])
    assert flat_res.ulcer_index == 0.0
    assert flat_res.longest_drawdown_bars == 0


# ---------------------------------------------------------------------------
# 3. Monte Carlo Bootstrap Simulation
# ---------------------------------------------------------------------------


def test_monte_carlo_resampling():
    """Verify bootstrap resampling simulation and percentile ordering."""
    trades = make_sample_trades()
    cfg = MonteCarloConfig(trials=500, random_seed=42)
    simulator = MonteCarloSimulator(cfg)

    result = simulator.run(trades, initial_capital=100000.0)

    assert result.trials == 500
    assert result.initial_capital == 100000.0

    # Ruin probabilities for each threshold
    for th in ["10%", "20%", "30%", "50%"]:
        assert th in result.ruin_probabilities
        assert 0.0 <= result.ruin_probabilities[th] <= 1.0

    # Drawdown percentiles must be monotonically increasing
    dd = result.drawdown_percentiles
    assert 0.0 <= dd.p5 <= dd.p25 <= dd.p50 <= dd.p75 <= dd.p95 <= dd.p99 <= dd.worst_case

    # Terminal wealth percentiles monotonically increasing
    tw = result.terminal_wealth_percentiles
    assert tw.p5 <= tw.p25 <= tw.p50 <= tw.p75 <= tw.p95

    # Confidence intervals
    assert result.profit_confidence_interval[0] <= result.profit_confidence_interval[1]
    assert result.sharpe_confidence_interval[0] <= result.sharpe_confidence_interval[1]

    # Positive expectancy sample should produce mostly profitable trials
    assert result.profitable_trials_pct >= 70.0


def test_monte_carlo_empty_trades():
    """Empty trade list gracefully returns zeroed Monte Carlo result."""
    simulator = MonteCarloSimulator()
    result = simulator.run([], initial_capital=50000.0)
    assert result.trials == 1000
    assert result.median_terminal_equity == 50000.0
    assert result.profitable_trials_pct == 0.0


# ---------------------------------------------------------------------------
# 4. Risk & Stop Calibration Engine
# ---------------------------------------------------------------------------


def test_risk_stop_calibrator():
    """Verify MAE survival curve and MFE target crest calibration."""
    trades = make_sample_trades()
    calibrator = RiskStopCalibrator(default_atr_pips=20.0)

    calibration = calibrator.calibrate(trades, pair="EURUSD", atr_pips=20.0)

    assert calibration.pair == "EURUSD"
    assert calibration.optimal_sl_pips >= 5.0
    assert calibration.optimal_tp_r >= 1.0
    assert calibration.optimal_tp_pips >= calibration.optimal_sl_pips

    # Check survival rates map
    assert len(calibration.mae_survival_rates) > 0
    for _sl, rate in calibration.mae_survival_rates.items():
        assert 0.0 <= rate <= 1.0

    # Check MFE crest probabilities map
    assert len(calibration.mfe_crest_probabilities) > 0
    for _tp, prob in calibration.mfe_crest_probabilities.items():
        assert 0.0 <= prob <= 1.0

    # Check recommended ATR multiples
    assert calibration.recommended_atr_sl_multiple > 0.0
    assert calibration.recommended_atr_tp_multiple > 0.0

    # Recommendations text
    assert len(calibration.recommendations) >= 2
    assert any("stop loss" in r.lower() for r in calibration.recommendations)
    assert any("target" in r.lower() for r in calibration.recommendations)


def test_risk_stop_calibrator_empty():
    """Empty trades list returns fallback calibration guidelines."""
    calibrator = RiskStopCalibrator()
    calib = calibrator.calibrate([], pair="GBPJPY")
    assert calib.pair == "GBPJPY"
    assert calib.optimal_sl_pips == 20.0
    assert calib.optimal_tp_r == 2.0
    assert len(calib.recommendations) > 0


# ---------------------------------------------------------------------------
# 5. Multi-Agent & Signal Ablation Study
# ---------------------------------------------------------------------------


def test_ablation_engine():
    """Verify comparative ablation study and marginal importance ranking."""
    baseline_trades = make_sample_trades()
    engine = ForexAblationEngine(initial_capital=100000.0)

    result = engine.run_default_synthetic_ablation(baseline_trades)

    assert result.baseline.variant_name == "Full System (All Analysts + Risk Engine)"
    assert result.baseline.total_trades == len(baseline_trades)
    assert result.baseline.total_net_profit > 0.0

    # Standard variants evaluated
    assert len(result.variants) == 4
    variant_names = [v.variant_name for v in result.variants]
    assert "Ablation: No Macro Analyst" in variant_names
    assert "Ablation: No News Blackout Filter" in variant_names
    assert "Ablation: No Deterministic Risk Engine" in variant_names
    assert "Ablation: Static Sizing (Fixed 0.1 Lot)" in variant_names

    # Deltas computed
    for v in result.variants:
        assert isinstance(v.delta_net_profit, float)
        assert isinstance(v.delta_sharpe, float)
        assert isinstance(v.delta_max_drawdown_pct, float)

    # Component importance ranking
    assert len(result.component_importance_ranking) == 4
    for comp, score in result.component_importance_ranking:
        assert isinstance(comp, str)
        assert score >= 0.0

    # Key findings synthesized
    assert len(result.key_findings) > 0


def test_ablation_empty():
    """Empty trade list handles ablation cleanly."""
    engine = ForexAblationEngine()
    res = engine.run_default_synthetic_ablation([])
    assert res.baseline.total_trades == 0
    assert len(res.variants) == 0


# ---------------------------------------------------------------------------
# 6. Analytics Dashboard & Markdown Report Generation
# ---------------------------------------------------------------------------


def test_analytics_dashboard_markdown_rendering():
    """Verify complete executive analytics dashboard formatting."""
    trades = make_sample_trades()
    curve = [100000.0, 100500.0, 100200.0, 101000.0, 101500.0]

    builder = ForexAnalyticsDashboard(monte_carlo_trials=300)
    dashboard = builder.generate_dashboard(
        trades=trades,
        equity_curve=curve,
        initial_capital=100000.0,
        period_start="2026-03-01",
        period_end="2026-03-15",
        pair="EURUSD",
    )

    assert dashboard.total_trades == len(trades)
    assert dashboard.final_equity == 101500.0
    assert dashboard.total_net_profit == 1500.0
    assert dashboard.monte_carlo is not None
    assert dashboard.calibration is not None
    assert dashboard.ablation is not None

    md = dashboard.render_markdown_dashboard()
    assert "# Institutional Forex Quantitative Analytics & Performance Dashboard" in md
    assert "1. Executive Performance Summary" in md
    assert "2. Advanced Risk & Downside Metrics" in md
    assert "System Quality Number (SQN)" in md
    assert "Calmar Ratio" in md
    assert "Ulcer Index" in md
    assert "3. Monte Carlo Simulation & Risk of Ruin" in md
    assert "4. Empirical Stop Loss & Profit Target Calibration" in md
    assert "5. Multi-Agent & Signal Ablation Study" in md

    # Check serialization to dict
    d = dashboard.to_dict()
    assert isinstance(d, dict)
    assert "deep_metrics" in d
    assert "monte_carlo" in d
    assert "calibration" in d


# ---------------------------------------------------------------------------
# 7. ForexAnalyticsManager Integration with Journal & Backtest
# ---------------------------------------------------------------------------


def test_analytics_manager_with_journal():
    """Verify ForexAnalyticsManager transparently analyzes trades from ForexTradeJournal."""
    journal = ForexTradeJournal(db_path=":memory:")

    # Record trade 1
    t1 = journal.record_trade_open(
        pair="EURUSD", action=ForexAction.LONG, open_price=1.0800, stop_loss=1.0760, lots=0.1
    )
    journal.record_trade_close(
        trade_id=t1.trade_id, close_price=1.0850, exit_reason=TradeExitReason.TAKE_PROFIT
    )

    # Record trade 2
    t2 = journal.record_trade_open(
        pair="EURUSD", action=ForexAction.SHORT, open_price=1.0850, stop_loss=1.0890, lots=0.1
    )
    journal.record_trade_close(
        trade_id=t2.trade_id, close_price=1.0810, exit_reason=TradeExitReason.TAKE_PROFIT
    )

    manager = ForexAnalyticsManager(journal=journal, initial_capital=100000.0)
    dashboard = manager.generate_dashboard(pair="EURUSD")

    assert dashboard.total_trades == 2
    assert dashboard.win_rate_pct == 100.0
    assert dashboard.total_net_profit > 0.0

    mc = manager.run_monte_carlo(trials=200)
    assert mc.trials == 200

    calib = manager.calibrate_stops(pair="EURUSD")
    assert calib.pair == "EURUSD"

    ablation = manager.run_ablation()
    assert len(ablation.variants) > 0

    journal.close()


def test_analytics_manager_with_backtest_result():
    """Verify ForexAnalyticsManager analyzes a ForexBacktestResult (Phase 18)."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = ForexBar(timestamp=now, open=1.0800, high=1.0810, low=1.0790, close=1.0800)
    c1 = ForexBar(timestamp=now + timedelta(minutes=15), open=1.0800, high=1.0860, low=1.0795, close=1.0850)

    proposal = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.TREND_CONTINUATION,
        timeframe="M15",
        entry_price=1.0800,
        stop_loss=1.0760,
        take_profit_1=1.0850,
        suggested_lot_size=0.1,
        reasoning="H1 EMA trend alignment.",
    )

    bt_result = run_forex_backtest(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={now: [proposal]},
    )

    manager = ForexAnalyticsManager()
    dashboard = manager.analyze_backtest_result(bt_result)

    assert dashboard.total_trades == 1
    assert dashboard.win_rate_pct == 100.0
    assert dashboard.final_equity > dashboard.initial_capital
    assert dashboard.deep_metrics.recovery_factor > 0.0
    assert dashboard.monte_carlo is not None
