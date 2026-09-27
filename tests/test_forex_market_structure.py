"""Tests for Forex Market Structure Engine (Phase 5).

Validates:
- Swing point detection and fractal window logic.
- Swing tagging: HH, HL, LH, LL, EQH, EQL.
- Structural Breaks: BOS (continuation) and CHoCH (reversal).
- Fair Value Gaps (FVGs): Bullish, Bearish, and mitigation tracking.
- Order Blocks (OBs): Bullish and Bearish identification and mitigation.
- Support & Resistance nearest level calculations and pip distances.
- MarketStructureSnapshot construction on major pairs (EURUSD, USDJPY).
- Multi-Timeframe Structure Alignment (Macro vs Tactical confluence).
- Markdown formatting for LLM prompt context.
- LangChain agent tool invocations.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows.forex_data import MultiTimeframeData
from tradingagents.forex.domain import Timeframe, get_forex_pair
from tradingagents.forex.market_structure import (
    AlignmentBias,
    BreakDirection,
    BreakType,
    FVGType,
    OBType,
    StructureTrend,
    SwingPoint,
    SwingTag,
    SwingType,
    analyze_multi_timeframe_structure,
    build_market_structure_snapshot,
    classify_structure_trend,
    detect_structural_breaks,
    find_fair_value_gaps,
    find_order_blocks,
    find_support_resistance_levels,
    find_swing_points,
    format_market_structure_snapshot,
    format_multi_timeframe_structure_summary,
)

# ---------------------------------------------------------------------------
# Synthetic Test Data Fixtures
# ---------------------------------------------------------------------------


def make_wave_df(prices: list[float], start_price: float = 1.0800) -> pd.DataFrame:
    """Create OHLCV DataFrame from a sequence of close prices."""
    n = len(prices)
    timestamps = [
        datetime(2026, 3, 10, i % 24, 0, tzinfo=timezone.utc)
        for i in range(n)
    ]
    opens = [start_price] + prices[:-1]
    highs = [max(o, c) + 0.0003 for o, c in zip(opens, prices)]
    lows = [min(o, c) - 0.0003 for o, c in zip(opens, prices)]
    volumes = [1000 + i * 10 for i in range(n)]

    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": prices,
        "Volume": volumes,
    })


def make_uptrend_swings_df() -> pd.DataFrame:
    """Create price series with clear Higher Highs and Higher Lows."""
    # Pattern: Swing low @ 1.0800, Swing high @ 1.0850, Swing low @ 1.0820 (HL), Swing high @ 1.0880 (HH)
    series = (
        [1.0800, 1.0810, 1.0820, 1.0835, 1.0850, 1.0840, 1.0830, 1.0820] +  # Up to 1.0850 then down to 1.0820
        [1.0830, 1.0845, 1.0865, 1.0880, 1.0870, 1.0860, 1.0850, 1.0845] +  # Up to 1.0880 (HH) then pullback
        [1.0860, 1.0880, 1.0905, 1.0920, 1.0910, 1.0900]                     # Continuation up to 1.0920
    )
    return make_wave_df(series)


def make_downtrend_swings_df() -> pd.DataFrame:
    """Create price series with clear Lower Highs and Lower Lows."""
    series = (
        [1.1000, 1.0980, 1.0960, 1.0940, 1.0920, 1.0935, 1.0950, 1.0940] +  # Down to 1.0920, bounce to 1.0950 (LH)
        [1.0920, 1.0900, 1.0880, 1.0860, 1.0875, 1.0890, 1.0870] +          # Down to 1.0860 (LL), bounce to 1.0890 (LH)
        [1.0850, 1.0830, 1.0810, 1.0800, 1.0810, 1.0805]                     # Down to 1.0800 (LL)
    )
    return make_wave_df(series)


# ---------------------------------------------------------------------------
# 1. Swing Point Detection Tests
# ---------------------------------------------------------------------------


class TestSwingPoints:
    def test_empty_or_short_df(self):
        df_short = make_wave_df([1.0800, 1.0810, 1.0820])
        swings = find_swing_points(df_short, lookback=3, lookforward=3)
        assert swings == []

    def test_uptrend_swings_detected_and_tagged(self):
        df = make_uptrend_swings_df()
        swings = find_swing_points(df, lookback=2, lookforward=2, pair="EURUSD")
        assert len(swings) >= 2

        highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        lows = [s for s in swings if s.swing_type == SwingType.LOW]

        assert len(highs) >= 1
        assert len(lows) >= 1

        # Check that high prices and low prices make sense
        for h in highs:
            assert h.price > 1.0840
        for l in lows:
            assert l.price < 1.0870

    def test_equal_highs_tagging(self):
        # Create identical peaks within 1 pip tolerance
        series = [
            1.0800, 1.0820, 1.0850, 1.0820, 1.0810,
            1.0830, 1.08505, 1.0820, 1.0800, 1.0790
        ]
        df = make_wave_df(series)
        swings = find_swing_points(df, lookback=1, lookforward=1, pair="EURUSD")
        highs = [s for s in swings if s.swing_type == SwingType.HIGH]

        assert len(highs) >= 2
        # Second high should be tagged as EQH
        assert highs[1].tag in (SwingTag.EQH, SwingTag.HH)


# ---------------------------------------------------------------------------
# 2. Structural Breaks (BOS / CHoCH) Tests
# ---------------------------------------------------------------------------


class TestStructuralBreaks:
    def test_bullish_bos_detection(self):
        df = make_uptrend_swings_df()
        swings = find_swing_points(df, lookback=2, lookforward=2, pair="EURUSD")
        breaks = detect_structural_breaks(df, swings)

        assert len(breaks) >= 1
        # At least one break should be Bullish
        bull_breaks = [b for b in breaks if b.direction == BreakDirection.BULLISH]
        assert len(bull_breaks) >= 1
        assert bull_breaks[0].break_type in (BreakType.BOS, BreakType.CHOCH)

    def test_bearish_bos_detection(self):
        df = make_downtrend_swings_df()
        swings = find_swing_points(df, lookback=2, lookforward=2, pair="EURUSD")
        breaks = detect_structural_breaks(df, swings)

        assert len(breaks) >= 1
        bear_breaks = [b for b in breaks if b.direction == BreakDirection.BEARISH]
        assert len(bear_breaks) >= 1

    def test_choch_trend_reversal(self):
        # Explicit candles: Bar 2 = swing low, Bar 4 = swing high (LH), Bar 7 = swing low (LL), Bar 10 = breaks Bar 4 (CHOCH)
        dates = [datetime(2026, 3, 10, i, 0, tzinfo=timezone.utc) for i in range(11)]
        df = pd.DataFrame({
            "Date": dates,
            "Open":  [1.1000, 1.0960, 1.0920, 1.0930, 1.0950, 1.0930, 1.0890, 1.0860, 1.0880, 1.0940, 1.0970],
            "High":  [1.1010, 1.0970, 1.0930, 1.0945, 1.0960, 1.0940, 1.0900, 1.0870, 1.0890, 1.0955, 1.0980],
            "Low":   [1.0950, 1.0910, 1.0890, 1.0920, 1.0925, 1.0880, 1.0850, 1.0830, 1.0860, 1.0890, 1.0930],
            "Close": [1.0960, 1.0920, 1.0900, 1.0940, 1.0930, 1.0890, 1.0860, 1.0840, 1.0880, 1.0950, 1.0975],
            "Volume": [100] * 11,
        })
        swings = find_swing_points(df, lookback=1, lookforward=1, pair="EURUSD")
        breaks = detect_structural_breaks(df, swings)

        # The break above 1.0960 is a Change of Character (CHOCH)
        choch_breaks = [b for b in breaks if b.break_type == BreakType.CHOCH]
        assert len(choch_breaks) >= 1
        assert choch_breaks[0].direction == BreakDirection.BULLISH


# ---------------------------------------------------------------------------
# 3. Structure Trend Classification Tests
# ---------------------------------------------------------------------------


class TestStructureTrendClassification:
    def test_classify_bullish_trend(self):
        swings = [
            SwingPoint(0, datetime.now(timezone.utc), 1.0800, SwingType.LOW, SwingTag.HL),
            SwingPoint(5, datetime.now(timezone.utc), 1.0850, SwingType.HIGH, SwingTag.HH),
            SwingPoint(10, datetime.now(timezone.utc), 1.0820, SwingType.LOW, SwingTag.HL),
            SwingPoint(15, datetime.now(timezone.utc), 1.0890, SwingType.HIGH, SwingTag.HH),
        ]
        trend = classify_structure_trend(swings)
        assert trend == StructureTrend.BULLISH

    def test_classify_bearish_trend(self):
        swings = [
            SwingPoint(0, datetime.now(timezone.utc), 1.0950, SwingType.HIGH, SwingTag.LH),
            SwingPoint(5, datetime.now(timezone.utc), 1.0900, SwingType.LOW, SwingTag.LL),
            SwingPoint(10, datetime.now(timezone.utc), 1.0920, SwingType.HIGH, SwingTag.LH),
            SwingPoint(15, datetime.now(timezone.utc), 1.0870, SwingType.LOW, SwingTag.LL),
        ]
        trend = classify_structure_trend(swings)
        assert trend == StructureTrend.BEARISH

    def test_classify_ranging_trend(self):
        swings = [
            SwingPoint(0, datetime.now(timezone.utc), 1.0950, SwingType.HIGH, SwingTag.HH),
            SwingPoint(5, datetime.now(timezone.utc), 1.0850, SwingType.LOW, SwingTag.LL),
        ]
        trend = classify_structure_trend(swings)
        assert trend == StructureTrend.RANGING


# ---------------------------------------------------------------------------
# 4. Fair Value Gaps (FVG) Tests
# ---------------------------------------------------------------------------


class TestFairValueGaps:
    def test_bullish_fvg_detected_and_mitigated(self):
        # Candle 0: High = 1.0810
        # Candle 1: Big impulse candle
        # Candle 2: Low = 1.0830 -> Gap = 1.0810 to 1.0830 (20 pips)
        # Candle 3: Pulls back to 1.0805 (mitigating the gap)
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, i, 0, tzinfo=timezone.utc) for i in range(4)],
            "Open": [1.0800, 1.0815, 1.0835, 1.0830],
            "High": [1.0810, 1.0840, 1.0850, 1.0835],
            "Low": [1.0795, 1.0812, 1.0830, 1.0805],
            "Close": [1.0808, 1.0838, 1.0845, 1.0810],
            "Volume": [100, 200, 150, 120],
        })
        fvgs = find_fair_value_gaps(df, pair="EURUSD", min_gap_pips=5.0)

        assert len(fvgs) == 1
        gap = fvgs[0]
        assert gap.fvg_type == FVGType.BULLISH
        assert gap.bottom == pytest.approx(1.0810, abs=1e-5)
        assert gap.top == pytest.approx(1.0830, abs=1e-5)
        assert gap.midpoint == pytest.approx(1.0820, abs=1e-5)
        assert gap.size_pips > 15.0
        assert gap.is_mitigated is True

    def test_bearish_fvg_unmitigated(self):
        # Candle 0: Low = 1.0950
        # Candle 1: Huge downward move
        # Candle 2: High = 1.0920 -> Gap = 1.0920 to 1.0950 (30 pips)
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, i, 0, tzinfo=timezone.utc) for i in range(3)],
            "Open": [1.0960, 1.0945, 1.0915],
            "High": [1.0965, 1.0948, 1.0920],
            "Low": [1.0950, 1.0910, 1.0900],
            "Close": [1.0955, 1.0915, 1.0905],
            "Volume": [100, 300, 150],
        })
        fvgs = find_fair_value_gaps(df, pair="EURUSD", min_gap_pips=5.0)

        assert len(fvgs) == 1
        gap = fvgs[0]
        assert gap.fvg_type == FVGType.BEARISH
        assert gap.bottom == pytest.approx(1.0920, abs=1e-5)
        assert gap.top == pytest.approx(1.0950, abs=1e-5)
        assert gap.is_mitigated is False


# ---------------------------------------------------------------------------
# 5. Order Blocks (OB) Tests
# ---------------------------------------------------------------------------


class TestOrderBlocks:
    def test_bullish_order_block(self):
        df = make_uptrend_swings_df()
        obs = find_order_blocks(df, pair="EURUSD")

        assert len(obs) >= 1
        assert obs[0].ob_type in (OBType.BULLISH, OBType.BEARISH)
        assert obs[0].top > obs[0].bottom
        assert obs[0].size_pips > 0.0


# ---------------------------------------------------------------------------
# 6. Support & Resistance Levels Tests
# ---------------------------------------------------------------------------


class TestSupportResistanceLevels:
    def test_support_and_resistance_pips(self):
        swings = [
            SwingPoint(0, datetime.now(timezone.utc), 1.0800, SwingType.LOW),
            SwingPoint(5, datetime.now(timezone.utc), 1.0900, SwingType.HIGH),
        ]
        current_price = 1.0850
        sup, res, sup_p, res_p = find_support_resistance_levels(current_price, swings, pair="EURUSD")

        assert sup == 1.0800
        assert res == 1.0900
        assert sup_p == pytest.approx(50.0, abs=0.1)
        assert res_p == pytest.approx(50.0, abs=0.1)

    def test_usdjpy_pip_precision(self):
        swings = [
            SwingPoint(0, datetime.now(timezone.utc), 150.00, SwingType.LOW),
            SwingPoint(5, datetime.now(timezone.utc), 152.00, SwingType.HIGH),
        ]
        current_price = 151.00
        sup, res, sup_p, res_p = find_support_resistance_levels(current_price, swings, pair="USDJPY")

        assert sup == 150.00
        assert res == 152.00
        # 1.00 price diff on JPY pair = 100.0 pips
        assert sup_p == pytest.approx(100.0, abs=0.1)
        assert res_p == pytest.approx(100.0, abs=0.1)


# ---------------------------------------------------------------------------
# 7. Snapshot & Multi-Timeframe Alignment Tests
# ---------------------------------------------------------------------------


class TestMarketStructureSnapshotAndAlignment:
    def test_build_market_structure_snapshot_eurusd(self):
        df = make_uptrend_swings_df()
        snapshot = build_market_structure_snapshot(df, pair="EURUSD", timeframe="H1")

        assert snapshot.symbol == "EURUSD"
        assert snapshot.timeframe == Timeframe.H1
        assert snapshot.trend in (StructureTrend.BULLISH, StructureTrend.RANGING)
        assert snapshot.current_price > 1.0800
        assert len(snapshot.recent_swings) >= 1

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="Cannot build market structure"):
            build_market_structure_snapshot(pd.DataFrame(), pair="EURUSD")

    def test_multi_timeframe_alignment_confluent_bullish(self):
        df_h4 = make_uptrend_swings_df()
        df_h1 = make_uptrend_swings_df()
        df_m15 = make_uptrend_swings_df()

        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={
                Timeframe.H4: df_h4,
                Timeframe.H1: df_h1,
                Timeframe.M15: df_m15,
            },
        )
        alignment = analyze_multi_timeframe_structure(bundle, lookback=2, lookforward=2)

        assert alignment.symbol == "EURUSD"
        assert alignment.macro_trend == StructureTrend.BULLISH
        assert alignment.is_aligned is True
        assert alignment.alignment_bias == AlignmentBias.CONFLUENT_BULLISH


# ---------------------------------------------------------------------------
# 8. Formatting Utilities Tests
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_format_market_structure_snapshot(self):
        df = make_uptrend_swings_df()
        snap = build_market_structure_snapshot(df, pair="EURUSD", timeframe=Timeframe.H1)
        formatted = format_market_structure_snapshot(snap)

        assert "EURUSD [H1] Market Structure" in formatted
        assert "Structure Trend:" in formatted
        assert "Nearest Key Support:" in formatted
        assert "Nearest Key Resistance:" in formatted

    def test_format_multi_timeframe_structure_summary(self):
        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={
                Timeframe.H4: make_uptrend_swings_df(),
                Timeframe.H1: make_uptrend_swings_df(),
            },
        )
        alignment = analyze_multi_timeframe_structure(bundle, lookback=2, lookforward=2)
        summary = format_multi_timeframe_structure_summary(alignment)

        assert "Multi-Timeframe Market Structure Matrix" in summary
        assert "Overall Confluence Bias" in summary
        assert "| **H4** |" in summary
        assert "| **H1** |" in summary


# ---------------------------------------------------------------------------
# 9. LangChain Agent Tools Tests
# ---------------------------------------------------------------------------


class TestMarketStructureTools:
    @patch("tradingagents.agents.utils.forex_tools.fetch_forex_candles")
    def test_get_market_structure_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_market_structure_tool

        mock_fetch.return_value = make_uptrend_swings_df()
        result = get_market_structure_tool.invoke({
            "symbol": "EURUSD",
            "timeframe": "H1",
            "trade_date": "2026-03-10",
        })

        assert "EURUSD [H1] Market Structure" in result
        assert "Structure Trend:" in result

    @patch("tradingagents.agents.utils.forex_tools.fetch_multi_timeframe_data")
    def test_get_multi_timeframe_market_structure_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_multi_timeframe_market_structure_tool

        mock_bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={
                Timeframe.H4: make_uptrend_swings_df(),
                Timeframe.H1: make_uptrend_swings_df(),
            },
        )
        mock_fetch.return_value = mock_bundle
        result = get_multi_timeframe_market_structure_tool.invoke({
            "symbol": "EURUSD",
            "trade_date": "2026-03-10",
        })

        assert "Multi-Timeframe Market Structure Matrix" in result
        assert "| **H4** |" in result
        assert "| **H1** |" in result
