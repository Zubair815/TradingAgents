"""Unit and integration tests for Point-in-Time (PIT) Safe Forex Backtester (Phase 18).

Validates:
- Point-in-time safeguarding (zero lookahead into future bars).
- Intrabar Stop Loss and Take Profit trigger evaluation.
- Conservative collision resolution when both SL and TP are touched in the same bar.
- Spread drag, directional slippage, and broker commission friction modeling.
- Intrabar MFE/MAE excursion tracking across candle life.
- Margin check rejection and max open trades limit enforcement.
- Institutional scorecard metrics (Sharpe, Sortino, Profit Factor, Expectancy, Drawdown).
- Markdown performance card generation.
- Integration with ForexTradeJournal SQLite persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.backtest.forex_engine import (
    BacktestTrade,
    ForexBacktestConfig,
    ForexBacktestEngine,
    run_forex_backtest,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex import (
    ForexBacktestConfig as FxConfigFromForex,
    ForexBacktestEngine as FxEngineFromForex,
    run_forex_backtest as runForexFromForex,
)

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def make_candle(
    dt: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 100.0,
    spread: float = 1.0,
) -> ForexBar:
    """Generate a synthetic ForexBar candle."""
    return ForexBar(
        timestamp=dt,
        open=round(o, 5),
        high=round(h, 5),
        low=round(l, 5),
        close=round(c, 5),
        volume=v,
        spread_pips=spread,
    )


def make_proposal(
    pair: str = "EURUSD",
    action: ForexAction = ForexAction.LONG,
    entry: float = 1.0800,
    sl: float = 1.0760,
    tp: float = 1.0880,
    lots: float = 0.1,
    setup: SetupType = SetupType.TREND_CONTINUATION,
) -> ForexTraderProposal:
    """Generate a synthetic ForexTraderProposal."""
    return ForexTraderProposal(
        pair=pair,
        action=action,
        order_type=OrderType.MARKET,
        setup_type=setup,
        timeframe="M15",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        suggested_lot_size=lots,
        suggested_risk_percent=1.0,
        confluence_factors=["H1 Trend Alignment", "Bullish FVG Retest"],
        reasoning="Institutional setup with EMA and FVG confluence.",
    )


# ---------------------------------------------------------------------------
# 1. Configuration & Models
# ---------------------------------------------------------------------------


def test_forex_backtest_config_defaults_and_custom():
    """Verify default parameters and custom configuration overrides."""
    cfg = ForexBacktestConfig()
    assert cfg.initial_balance == 100000.0
    assert cfg.account_currency == "USD"
    assert cfg.leverage == 100.0
    assert cfg.default_spread_pips == 1.2
    assert cfg.default_slippage_pips == 0.3
    assert cfg.commission_per_lot_usd == 5.0
    assert cfg.conservative_stops is True
    assert cfg.max_open_trades == 5

    custom = ForexBacktestConfig(
        initial_balance=50000.0,
        leverage=50.0,
        conservative_stops=False,
        default_spread_pips=1.5,
    )
    assert custom.initial_balance == 50000.0
    assert custom.leverage == 50.0
    assert custom.conservative_stops is False
    assert custom.default_spread_pips == 1.5


def test_forex_symbols_exported_from_forex_root():
    """Verify Phase 18 classes are properly re-exported in tradingagents.forex."""
    assert FxConfigFromForex is ForexBacktestConfig
    assert FxEngineFromForex is ForexBacktestEngine
    assert runForexFromForex is run_forex_backtest


def test_backtest_trade_model_properties():
    """Verify BacktestTrade helper properties and state tracking."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    trade = BacktestTrade(
        trade_id="bt_test123",
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_time=now,
        entry_price=1.0800,
        stop_loss=1.0760,
        take_profit=1.0880,
        lots=0.1,
    )
    assert trade.is_closed is False
    assert trade.is_winner is False

    trade.status = TradeStatus.CLOSED
    trade.net_profit = 80.0
    assert trade.is_closed is True
    assert trade.is_winner is True


# ---------------------------------------------------------------------------
# 2. Point-in-Time (PIT) Safety Verification
# ---------------------------------------------------------------------------


def test_point_in_time_safety():
    """Verify strategy_callback receives strictly candles <= current timestamp."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        make_candle(base_time + timedelta(minutes=15 * i), 1.0800 + i * 0.0005, 1.0810 + i * 0.0005, 1.0790 + i * 0.0005, 1.0805 + i * 0.0005)
        for i in range(10)
    ]

    seen_timestamps: list[tuple[datetime, int, datetime]] = []

    def callback(current_time: datetime, pit_candles: list[ForexBar]):
        # Strategy MUST only see historical candles up to current_time
        assert pit_candles[-1].timestamp == current_time
        assert all(c.timestamp <= current_time for c in pit_candles)
        seen_timestamps.append((current_time, len(pit_candles), pit_candles[-1].timestamp))
        return []

    engine = ForexBacktestEngine()
    engine.run_candles(pair="EURUSD", candles=candles, strategy_callback=callback)

    assert len(seen_timestamps) == 10
    for idx, (curr_t, window_len, last_t) in enumerate(seen_timestamps):
        assert window_len == idx + 1
        assert curr_t == candles[idx].timestamp
        assert last_t == candles[idx].timestamp


# ---------------------------------------------------------------------------
# 3. Intrabar Stop Loss and Take Profit Triggers
# ---------------------------------------------------------------------------


def test_intrabar_long_take_profit():
    """Verify a LONG position closes with profit when candle high reaches TP."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0820, 1.0790, 1.0810)
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0810, 1.0830, 1.0805, 1.0825)
    # Candle 2 hits TP 1.0860
    c2 = make_candle(base_time + timedelta(minutes=30), 1.0825, 1.0870, 1.0820, 1.0865)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0760,
        tp=1.0860,
        lots=0.1,
    )

    config = ForexBacktestConfig(default_spread_pips=1.0, default_slippage_pips=0.2)
    engine = ForexBacktestEngine(config=config)

    # Submit proposal on candle 0
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1, c2],
        proposals_schedule={base_time: [proposal]},
    )

    assert result.total_trades == 1
    assert result.winning_trades == 1
    trade = result.trades[0]
    assert trade.status == TradeStatus.CLOSED
    assert trade.exit_reason == TradeExitReason.TAKE_PROFIT
    assert trade.exit_price == 1.0860
    assert trade.is_winner is True
    assert trade.pips_gained > 0
    assert trade.r_multiple > 0
    assert trade.net_profit > 0


def test_intrabar_long_stop_loss():
    """Verify a LONG position closes with loss and slippage when candle low reaches SL."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0795)
    # Candle 1 plummets and hits SL 1.0760
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0795, 1.0800, 1.0750, 1.0755)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0760,
        tp=1.0880,
        lots=0.1,
    )

    config = ForexBacktestConfig(default_spread_pips=1.0, default_slippage_pips=0.2)
    engine = ForexBacktestEngine(config=config)

    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )

    assert result.total_trades == 1
    assert result.losing_trades == 1
    trade = result.trades[0]
    assert trade.status == TradeStatus.CLOSED
    assert trade.exit_reason == TradeExitReason.STOP_LOSS
    # SL is 1.0760, with adverse slippage of 0.2 pips (0.00002), exit is <= 1.0760
    assert trade.exit_price <= 1.0760
    assert trade.is_winner is False
    assert trade.pips_gained < 0
    assert trade.net_profit < 0


def test_intrabar_short_take_profit():
    """Verify a SHORT position closes with profit when candle low reaches TP."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0795)
    # Candle 1 falls and hits TP 1.0740
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0795, 1.0800, 1.0735, 1.0740)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.SHORT,
        entry=1.0800,
        sl=1.0840,
        tp=1.0740,
        lots=0.1,
    )

    engine = ForexBacktestEngine()
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )

    assert result.total_trades == 1
    assert result.winning_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == TradeExitReason.TAKE_PROFIT
    assert trade.exit_price == 1.0740
    assert trade.is_winner is True


def test_intrabar_short_stop_loss():
    """Verify a SHORT position closes when candle high breaches SL."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0805)
    # Candle 1 surges and breaches SL 1.0840
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0805, 1.0855, 1.0800, 1.0850)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.SHORT,
        entry=1.0800,
        sl=1.0840,
        tp=1.0720,
        lots=0.1,
    )

    engine = ForexBacktestEngine()
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )

    assert result.total_trades == 1
    assert result.losing_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == TradeExitReason.STOP_LOSS
    assert trade.exit_price >= 1.0840
    assert trade.is_winner is False


# ---------------------------------------------------------------------------
# 4. Conservative Stops Collision Resolution
# ---------------------------------------------------------------------------


def test_conservative_stops_collision_resolution():
    """When both SL and TP are within a high-volatility bar's range:
    - If conservative_stops=True, assume SL was triggered first.
    - If conservative_stops=False, assume TP was triggered first.
    """
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0800)
    # Flash spike candle spanning from 1.0740 to 1.0870
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0800, 1.0870, 1.0740, 1.0810)

    # LONG with SL 1.0750 and TP 1.0860
    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0750,
        tp=1.0860,
        lots=0.1,
    )

    # 1. Conservative True -> Stop Loss triggered
    engine_cons = ForexBacktestEngine(config=ForexBacktestConfig(conservative_stops=True))
    res_cons = engine_cons.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )
    assert res_cons.trades[0].exit_reason == TradeExitReason.STOP_LOSS
    assert res_cons.trades[0].is_winner is False

    # 2. Conservative False -> Take Profit triggered
    engine_opt = ForexBacktestEngine(config=ForexBacktestConfig(conservative_stops=False))
    res_opt = engine_opt.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )
    assert res_opt.trades[0].exit_reason == TradeExitReason.TAKE_PROFIT
    assert res_opt.trades[0].is_winner is True


# ---------------------------------------------------------------------------
# 5. Friction Drag & Execution Modeling
# ---------------------------------------------------------------------------


def test_spread_and_slippage_friction_accounting():
    """Verify exact directional fill price adjustment and friction tracking."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    # Open price 1.0800, 1 pip = 0.0001
    candle = make_candle(base_time, 1.0800, 1.0820, 1.0780, 1.0805)

    config = ForexBacktestConfig(
        default_spread_pips=2.0,  # 0.0002
        default_slippage_pips=0.5,  # 0.00005
        commission_per_lot_usd=6.0,
    )
    engine = ForexBacktestEngine(config=config)

    # Long fill test: open + spread/2 (0.0001) + slippage (0.00005) = 1.08015
    long_prop = make_proposal(
        pair="EURUSD", action=ForexAction.LONG, entry=1.0800, sl=1.0750, tp=1.0850, lots=1.0
    )
    long_trade = engine.execute_proposal(long_prop, candle)
    assert long_trade is not None
    assert round(long_trade.entry_price, 5) == 1.08015
    assert long_trade.commission == 6.0  # $6.00 for 1.0 lot

    # Short fill test: open - spread/2 (0.0001) - slippage (0.00005) = 1.07985
    short_prop = make_proposal(
        pair="EURUSD", action=ForexAction.SHORT, entry=1.0800, sl=1.0850, tp=1.0750, lots=0.5
    )
    short_trade = engine.execute_proposal(short_prop, candle)
    assert short_trade is not None
    assert round(short_trade.entry_price, 5) == 1.07985
    assert short_trade.commission == 3.0  # $3.00 for 0.5 lot

    # Cumulative friction tracking
    assert engine.total_commission_drag == 9.0
    assert engine.total_spread_drag > 0.0
    assert engine.total_slippage_drag > 0.0


# ---------------------------------------------------------------------------
# 6. Dynamic Intrabar MFE/MAE Excursion Tracking
# ---------------------------------------------------------------------------


def test_dynamic_excursion_tracking():
    """Verify maximum favorable excursion (MFE) and maximum adverse excursion (MAE) update bar-by-bar."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0800)
    # Candle 1: High 1.0830 (favorable), Low 1.0785 (adverse)
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0800, 1.0830, 1.0785, 1.0820)
    # Candle 2: High 1.0850 (new higher high), Low 1.0810
    c2 = make_candle(base_time + timedelta(minutes=30), 1.0820, 1.0850, 1.0810, 1.0845)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0750,  # 50 pips initial risk
        tp=1.0900,
        lots=0.1,
    )

    engine = ForexBacktestEngine(config=ForexBacktestConfig(default_spread_pips=0.0, default_slippage_pips=0.0))
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1, c2],
        proposals_schedule={base_time: [proposal]},
    )

    trade = result.trades[0]
    # Trade was force-closed at end of c2 at 1.0845
    assert trade.status == TradeStatus.CLOSED
    # Peak price reached was 1.0850
    assert trade.mfe_price == 1.0850
    # Worst price reached was 1.0785
    assert trade.mae_price == 1.0785
    assert trade.mfe_pips == pytest.approx(50.0, abs=0.5)
    assert trade.mae_pips == pytest.approx(15.0, abs=0.5)
    assert trade.mfe_r > 0.0
    assert trade.mae_r > 0.0


# ---------------------------------------------------------------------------
# 7. Margin Check & Max Open Trades Constraint
# ---------------------------------------------------------------------------


def test_margin_and_max_trades_rejection():
    """Verify proposals are rejected when free margin is insufficient or max open trades reached."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    candle = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0800)

    # 1. Margin rejection on tiny balance
    tiny_cfg = ForexBacktestConfig(initial_balance=100.0, leverage=10.0)
    engine_tiny = ForexBacktestEngine(config=tiny_cfg)
    huge_prop = make_proposal(pair="EURUSD", action=ForexAction.LONG, lots=1.0)
    # 1 lot EURUSD at 1:10 leverage requires ~$10,800 margin, balance is only $100
    rejected_trade = engine_tiny.execute_proposal(huge_prop, candle)
    assert rejected_trade is None
    assert len(engine_tiny.open_trades) == 0

    # 2. Max open trades limit
    cfg_max = ForexBacktestConfig(initial_balance=100000.0, max_open_trades=1)
    engine_max = ForexBacktestEngine(config=cfg_max)
    prop1 = make_proposal(pair="EURUSD", action=ForexAction.LONG, lots=0.1)
    prop2 = make_proposal(pair="EURUSD", action=ForexAction.SHORT, lots=0.1)

    t1 = engine_max.execute_proposal(prop1, candle)
    assert t1 is not None
    assert len(engine_max.open_trades) == 1

    t2 = engine_max.execute_proposal(prop2, candle)
    assert t2 is None  # Exceeds max_open_trades=1
    assert len(engine_max.open_trades) == 1

    # 3. NO_TRADE proposal ignored
    no_trade_prop = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.NO_TRADE,
        order_type=OrderType.MARKET,
        timeframe="M15",
        reasoning="Market regime is choppy, no high-probability setup.",
    )
    assert engine_max.execute_proposal(no_trade_prop, candle) is None


# ---------------------------------------------------------------------------
# 8. Scorecard Metrics & Markdown Card
# ---------------------------------------------------------------------------


def test_scorecard_metrics_and_markdown_report():
    """Validate Sharpe, Sortino, Profit Factor, Expectancy, and report rendering."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    # 3 trades: 2 winners, 1 loser
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0800)
    # Win 1
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0800, 1.0860, 1.0795, 1.0850)
    # Trade 2 open
    c2 = make_candle(base_time + timedelta(minutes=30), 1.0850, 1.0860, 1.0840, 1.0850)
    # Loss 1
    c3 = make_candle(base_time + timedelta(minutes=45), 1.0850, 1.0855, 1.0800, 1.0805)
    # Trade 3 open
    c4 = make_candle(base_time + timedelta(minutes=60), 1.0805, 1.0815, 1.0795, 1.0805)
    # Win 2
    c5 = make_candle(base_time + timedelta(minutes=75), 1.0805, 1.0870, 1.0800, 1.0865)

    p1 = make_proposal(pair="EURUSD", action=ForexAction.LONG, entry=1.0800, sl=1.0760, tp=1.0855, lots=0.1)
    p2 = make_proposal(pair="EURUSD", action=ForexAction.LONG, entry=1.0850, sl=1.0810, tp=1.0900, lots=0.1)
    p3 = make_proposal(pair="EURUSD", action=ForexAction.LONG, entry=1.0805, sl=1.0770, tp=1.0865, lots=0.1)

    schedule = {
        base_time: [p1],
        base_time + timedelta(minutes=30): [p2],
        base_time + timedelta(minutes=60): [p3],
    }

    result = run_forex_backtest(
        pair="EURUSD",
        candles=[c0, c1, c2, c3, c4, c5],
        proposals_schedule=schedule,
    )

    assert result.total_trades == 3
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert result.win_rate_pct == pytest.approx(66.7, abs=0.5)
    assert result.loss_rate_pct == pytest.approx(33.3, abs=0.5)

    assert result.gross_profit > 0.0
    assert result.gross_loss > 0.0
    assert result.profit_factor > 1.0
    assert result.expectancy_r > 0.0
    assert result.expectancy_cash > 0.0
    assert result.total_pips > 0.0

    # Equity points recorded
    assert len(result.equity_curve) == 6
    assert result.max_drawdown_cash >= 0.0
    assert result.max_drawdown_pct >= 0.0

    # Markdown card rendering
    md = result.render_markdown_report()
    assert "# Institutional Forex Backtest Performance Report" in md
    assert "Executive Portfolio Performance" in md
    assert "Profit Factor" in md
    assert "Sharpe Ratio" in md
    assert "Execution Friction & Broker Drag" in md


# ---------------------------------------------------------------------------
# 9. ForexTradeJournal SQLite Persistence Integration
# ---------------------------------------------------------------------------


def test_forex_trade_journal_integration():
    """Verify backtester transparently logs trade opens and closes into ForexTradeJournal."""
    journal = ForexTradeJournal(db_path=":memory:")

    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0800)
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0800, 1.0860, 1.0795, 1.0855)

    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0760,
        tp=1.0850,
        lots=0.1,
    )

    engine = ForexBacktestEngine(journal=journal)
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )

    assert result.total_trades == 1
    logged_trades = journal.list_trades()
    assert len(logged_trades) == 1
    rec = logged_trades[0]
    assert rec.pair == "EURUSD"
    assert rec.status == TradeStatus.CLOSED
    assert rec.exit_reason == TradeExitReason.TAKE_PROFIT
    assert rec.close_price == 1.0850
    assert rec.lots == 0.1
    journal.close()


# ---------------------------------------------------------------------------
# 10. Edge Cases: Empty Candles & Final Force Close
# ---------------------------------------------------------------------------


def test_empty_candles_list():
    """Empty candle input gracefully returns clean 0-trade result."""
    result = run_forex_backtest(pair="EURUSD", candles=[])
    assert result.total_trades == 0
    assert result.winning_trades == 0
    assert result.final_balance == result.initial_balance
    assert result.profit_factor == 0.0
    assert result.sharpe_ratio == 0.0


def test_force_close_unresolved_trades_on_last_candle():
    """Positions still open when the simulation finishes must be force-closed at final bar close."""
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    c0 = make_candle(base_time, 1.0800, 1.0810, 1.0790, 1.0805)
    c1 = make_candle(base_time + timedelta(minutes=15), 1.0805, 1.0820, 1.0800, 1.0815)

    # Wide SL and TP that will not be touched
    proposal = make_proposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry=1.0800,
        sl=1.0500,
        tp=1.1200,
        lots=0.1,
    )

    engine = ForexBacktestEngine()
    result = engine.run_candles(
        pair="EURUSD",
        candles=[c0, c1],
        proposals_schedule={base_time: [proposal]},
    )

    assert len(engine.open_trades) == 0
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.status == TradeStatus.CLOSED
    assert trade.exit_reason == TradeExitReason.MANUAL
    assert trade.exit_price == c1.close
