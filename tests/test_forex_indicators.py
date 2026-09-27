"""Tests for Forex Technical Indicators Engine (Phase 4).

Validates:
- Moving average math: SMA, EMA, Wilder's smoothing.
- Volatility indicators: True Range, ATR, ATR in pips, ATR%, Spread/ATR ratio, favorable cost filter.
- Momentum indicators: Wilder's RSI, MACD (line, signal, histogram), Stochastic (%K, %D).
- Trend strength: ADX, +DI, -DI, and classification into trend regimes.
- Volatility bands: Bollinger Bands, %B, and bandwidth in pips.
- EMA trend classification: Strong Bullish, Bullish, Strong Bearish, Bearish, Neutral.
- RSI regime classification: Overbought, Bullish, Neutral, Bearish, Oversold.
- DataFrame enrichment via compute_forex_indicators.
- Single-timeframe ForexIndicatorSnapshot and multi-timeframe indicator matrix.
- Markdown formatting for LLM prompt context.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows.forex_data import MultiTimeframeData
from tradingagents.forex.domain import Timeframe, get_forex_pair
from tradingagents.forex.indicators import (
    ForexIndicatorSnapshot,
    RSIRegime,
    TrendRegime,
    TrendStrength,
    build_forex_indicator_snapshot,
    calculate_adx,
    calculate_atr_percent,
    calculate_atr_pips,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_spread_atr_ratio,
    calculate_stochastic,
    calculate_true_range,
    calculate_wilder_ma,
    classify_adx_strength,
    classify_ema_trend,
    classify_rsi_regime,
    compute_forex_indicators,
    compute_multi_timeframe_indicators,
    format_indicator_snapshot,
    format_multi_timeframe_indicators_summary,
    is_spread_favorable,
)

# ---------------------------------------------------------------------------
# Synthetic Test Data Helpers
# ---------------------------------------------------------------------------


def make_trending_df(num_bars: int = 50, trend: str = "bullish", start_price: float = 1.0800) -> pd.DataFrame:
    """Create synthetic price series with strong bullish or bearish trend."""
    timestamps = [
        datetime(2026, 3, 10, tzinfo=timezone.utc) + timedelta(hours=i)
        for i in range(num_bars)
    ]
    step = 0.0005 if trend == "bullish" else -0.0005
    closes = [start_price + i * step for i in range(num_bars)]
    opens = [c - (step * 0.5) for c in closes]
    highs = [max(o, c) + 0.0006 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 0.0006 for o, c in zip(opens, closes, strict=True)]

    volumes = [1000 + i * 10 for i in range(num_bars)]

    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


def make_usdjpy_df(num_bars: int = 40) -> pd.DataFrame:
    """Create synthetic USDJPY price series."""
    timestamps = [
        datetime(2026, 3, 10, tzinfo=timezone.utc) + timedelta(hours=i)
        for i in range(num_bars)
    ]
    closes = [150.00 + i * 0.05 for i in range(num_bars)]
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.08 for c in closes]
    lows = [o - 0.08 for o in opens]
    volumes = [5000 for _ in range(num_bars)]

    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


# ---------------------------------------------------------------------------
# 1. Moving Averages Tests
# ---------------------------------------------------------------------------


class TestMovingAverages:
    def test_sma_calculation(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        sma3 = calculate_sma(s, 3)
        assert pytest.approx(sma3.iloc[-1], 1e-6) == 4.0  # (3 + 4 + 5) / 3

    def test_ema_calculation(self):
        s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        ema3 = calculate_ema(s, 3)
        # EMA responds faster to latest values than SMA
        assert ema3.iloc[-1] > calculate_sma(s, 3).iloc[-1]

    def test_wilder_ma_calculation(self):
        s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        wma = calculate_wilder_ma(s, 14)
        assert len(wma) == len(s)
        assert not wma.isna().all()


# ---------------------------------------------------------------------------
# 2. Volatility & ATR Tests
# ---------------------------------------------------------------------------


class TestVolatilityIndicators:
    def test_true_range(self):
        # Bar 0: High=1.10, Low=1.00, Close=1.05
        # Bar 1: High=1.12, Low=1.06, Close=1.10 -> Gap up, TR is High - prev_close (1.12 - 1.05 = 0.07)
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, 10, 0), datetime(2026, 3, 10, 11, 0)],
            "Open": [1.02, 1.07],
            "High": [1.10, 1.12],
            "Low": [1.00, 1.06],
            "Close": [1.05, 1.10],
            "Volume": [100, 100],
        })
        tr = calculate_true_range(df)
        assert pytest.approx(tr.iloc[0], 1e-6) == 0.10  # 1.10 - 1.00
        assert pytest.approx(tr.iloc[1], 1e-6) == 0.07  # 1.12 - 1.05

    def test_atr_pips_eurusd(self):
        df = make_trending_df(30, "bullish", start_price=1.0800)
        atr_pips = calculate_atr_pips(df, pair="EURUSD", period=14)
        # EURUSD pip is 0.0001; ATR in pips should be positive and reasonable (~10-50 pips)
        assert atr_pips.iloc[-1] > 0.0
        assert 5.0 <= atr_pips.iloc[-1] <= 100.0

    def test_atr_pips_usdjpy(self):
        df = make_usdjpy_df(30)
        atr_pips = calculate_atr_pips(df, pair="USDJPY", period=14)
        # USDJPY pip is 0.01; ATR in pips should be scaled by 0.01
        assert atr_pips.iloc[-1] > 0.0
        assert 5.0 <= atr_pips.iloc[-1] <= 100.0

    def test_atr_percent(self):
        df = make_trending_df(20, start_price=1.0800)
        atr_pct = calculate_atr_percent(df, 14)
        assert atr_pct.iloc[-1] > 0.0
        assert atr_pct.iloc[-1] < 5.0  # Daily/intraday ATR% is typically 0.1% to 2%

    def test_spread_atr_ratio(self):
        # 1.0 pip spread with 10.0 pip ATR -> 10% ratio
        ratio = calculate_spread_atr_ratio(spread_pips=1.0, atr_pips=10.0)
        assert pytest.approx(ratio, 1e-6) == 0.10

        # Zero or negative ATR handling
        assert calculate_spread_atr_ratio(1.0, 0.0) == 0.0
        assert calculate_spread_atr_ratio(-1.0, 10.0) == 0.0

    def test_is_spread_favorable(self):
        # 1.0 pip spread / 20.0 pip ATR = 5% (favorable)
        assert is_spread_favorable(1.0, 20.0, max_acceptable_ratio=0.12) is True
        # 3.0 pip spread / 10.0 pip ATR = 30% (unfavorable)
        assert is_spread_favorable(3.0, 10.0, max_acceptable_ratio=0.12) is False


# ---------------------------------------------------------------------------
# 3. Momentum: RSI, MACD, Stochastic Tests
# ---------------------------------------------------------------------------


class TestMomentumIndicators:
    def test_rsi_bullish_and_bearish(self):
        bull_df = make_trending_df(40, "bullish")
        rsi_bull = calculate_rsi(bull_df["Close"], period=14)
        # Steady up trend should push RSI well above 60
        assert rsi_bull.iloc[-1] > 60.0

        bear_df = make_trending_df(40, "bearish")
        rsi_bear = calculate_rsi(bear_df["Close"], period=14)
        # Steady down trend should push RSI well below 40
        assert rsi_bear.iloc[-1] < 40.0

    def test_macd(self):
        df = make_trending_df(40, "bullish")
        m_line, m_sig, m_hist = calculate_macd(df["Close"], 12, 26, 9)
        assert len(m_line) == len(df)
        assert len(m_sig) == len(df)
        assert len(m_hist) == len(df)
        # In a sustained uptrend, MACD line should be positive and above zero
        assert m_line.iloc[-1] > 0.0

    def test_stochastic(self):
        df = make_trending_df(30, "bullish")
        k, d = calculate_stochastic(df, 14, 3, 3)
        assert 0.0 <= k.iloc[-1] <= 100.0
        assert 0.0 <= d.iloc[-1] <= 100.0


# ---------------------------------------------------------------------------
# 4. ADX & Trend Strength Tests
# ---------------------------------------------------------------------------


class TestADX:
    def test_adx_trending_vs_flat(self):
        trend_df = make_trending_df(40, "bullish")
        adx, p_di, m_di = calculate_adx(trend_df, period=14)
        # Strong directional move gives positive +DI dominant over -DI
        assert p_di.iloc[-1] > m_di.iloc[-1]
        assert adx.iloc[-1] > 0.0

    def test_classify_adx_strength(self):
        assert classify_adx_strength(45.0) == TrendStrength.EXTREME_TREND
        assert classify_adx_strength(30.0) == TrendStrength.STRONG_TREND
        assert classify_adx_strength(18.0) == TrendStrength.WEAK_OR_RANGING


# ---------------------------------------------------------------------------
# 5. Bollinger Bands Tests
# ---------------------------------------------------------------------------


class TestBollingerBands:
    def test_bollinger_bands_geometry(self):
        df = make_trending_df(30)
        upper, middle, lower = calculate_bollinger_bands(df["Close"], period=20, std_dev=2.0)
        # Upper > Middle > Lower
        assert (upper.iloc[20:] >= middle.iloc[20:]).all()
        assert (middle.iloc[20:] >= lower.iloc[20:]).all()
        # Middle band is exactly 20 SMA
        sma20 = calculate_sma(df["Close"], 20)
        assert pytest.approx(middle.iloc[-1], 1e-6) == sma20.iloc[-1]


# ---------------------------------------------------------------------------
# 6. Regime & Trend Classification Tests
# ---------------------------------------------------------------------------


class TestRegimeClassification:
    def test_classify_ema_trend_strong_bullish(self):
        trend = classify_ema_trend(close=1.1000, ema8=1.0950, ema21=1.0900, ema50=1.0850, ema200=1.0700)
        assert trend == TrendRegime.STRONG_BULLISH

    def test_classify_ema_trend_strong_bearish(self):
        trend = classify_ema_trend(close=1.0600, ema8=1.0650, ema21=1.0700, ema50=1.0750, ema200=1.0900)
        assert trend == TrendRegime.STRONG_BEARISH

    def test_classify_ema_trend_neutral(self):
        # Mixed close between EMAs
        trend = classify_ema_trend(close=1.0820, ema8=1.0850, ema21=1.0800, ema50=1.0810, ema200=1.0800)
        assert trend == TrendRegime.NEUTRAL

    def test_classify_rsi_regime(self):
        assert classify_rsi_regime(75.0) == RSIRegime.OVERBOUGHT
        assert classify_rsi_regime(62.0) == RSIRegime.BULLISH_MOMENTUM
        assert classify_rsi_regime(50.0) == RSIRegime.NEUTRAL
        assert classify_rsi_regime(38.0) == RSIRegime.BEARISH_MOMENTUM
        assert classify_rsi_regime(25.0) == RSIRegime.OVERSOLD


# ---------------------------------------------------------------------------
# 7. DataFrame Enrichment & Snapshot Tests
# ---------------------------------------------------------------------------


class TestEnrichmentAndSnapshot:
    def test_compute_forex_indicators_enrichment(self):
        df = make_trending_df(40, "bullish")
        enriched = compute_forex_indicators(df, pair="EURUSD")

        # Verify added indicator columns
        expected_cols = [
            "ema_8", "ema_21", "ema_50", "ema_200",
            "tr", "atr_14", "atr_14_pips", "atr_percent",
            "rsi_14", "macd", "macd_signal", "macd_hist",
            "adx_14", "plus_di", "minus_di",
            "bb_upper", "bb_middle", "bb_lower", "bb_width_pips", "bb_pct_b",
        ]
        for col in expected_cols:
            assert col in enriched.columns
            assert not enriched[col].isna().all()

    def test_build_forex_indicator_snapshot_eurusd(self):
        df = make_trending_df(40, "bullish")
        snapshot = build_forex_indicator_snapshot(
            df=df,
            pair="EURUSD",
            timeframe=Timeframe.H1,
            spread_pips=1.2,
        )

        assert isinstance(snapshot, ForexIndicatorSnapshot)
        assert snapshot.symbol == "EURUSD"
        assert snapshot.timeframe == Timeframe.H1
        assert snapshot.spread_pips == 1.2
        assert snapshot.atr_14_pips > 0.0
        assert snapshot.spread_atr_ratio > 0.0
        assert snapshot.rsi_14 > 0.0
        assert isinstance(snapshot.ema_trend, TrendRegime)
        assert isinstance(snapshot.rsi_regime, RSIRegime)
        assert isinstance(snapshot.adx_strength, TrendStrength)

    def test_build_forex_indicator_snapshot_usdjpy(self):
        df = make_usdjpy_df(35)
        snapshot = build_forex_indicator_snapshot(
            df=df,
            pair="USDJPY",
            timeframe="15m",
            spread_pips=1.5,
        )
        assert snapshot.symbol == "USDJPY"
        assert snapshot.timeframe == Timeframe.M15
        assert snapshot.close > 140.0

    def test_empty_df_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot build indicator snapshot"):
            build_forex_indicator_snapshot(pd.DataFrame(), pair="EURUSD")


# ---------------------------------------------------------------------------
# 8. Multi-Timeframe Indicator Analysis Tests
# ---------------------------------------------------------------------------


class TestMultiTimeframeIndicators:
    def test_compute_multi_timeframe_indicators(self):
        df_d1 = make_trending_df(30, "bullish")
        df_h4 = make_trending_df(25, "bullish")
        df_h1 = make_trending_df(20, "bullish")

        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={Timeframe.D1: df_d1, Timeframe.H4: df_h4, Timeframe.H1: df_h1},
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )

        matrix = compute_multi_timeframe_indicators(bundle, spread_pips=1.0)
        assert Timeframe.D1 in matrix
        assert Timeframe.H4 in matrix
        assert Timeframe.H1 in matrix
        assert matrix[Timeframe.D1].symbol == "EURUSD"
        assert matrix[Timeframe.H1].atr_14_pips > 0.0


# ---------------------------------------------------------------------------
# 9. Prompt & Report Formatting Tests
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_format_indicator_snapshot(self):
        df = make_trending_df(35, "bullish")
        snapshot = build_forex_indicator_snapshot(df, pair="EURUSD", timeframe=Timeframe.H1)
        text = format_indicator_snapshot(snapshot)

        assert "EURUSD [H1] Indicator Snapshot" in text
        assert "ATR(14)" in text
        assert "Spread/ATR" in text
        assert "Trend (EMA Stack)" in text
        assert "RSI(14)" in text
        assert "Bollinger Bands" in text

    def test_format_multi_timeframe_indicators_summary(self):
        df_h4 = make_trending_df(25)
        df_h1 = make_trending_df(20)
        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={Timeframe.H4: df_h4, Timeframe.H1: df_h1},
        )
        matrix = compute_multi_timeframe_indicators(bundle, spread_pips=1.0)
        summary = format_multi_timeframe_indicators_summary(matrix)

        assert "Multi-Timeframe Technical Indicator Matrix" in summary
        assert "| **H4** |" in summary
        assert "| **H1** |" in summary
        assert "Detailed Snapshot Per Horizon" in summary


# ---------------------------------------------------------------------------
# 10. LangChain Agent Tools Tests
# ---------------------------------------------------------------------------


class TestForexIndicatorTools:
    @patch("tradingagents.agents.utils.forex_tools.fetch_forex_candles")
    def test_get_forex_indicators_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_forex_indicators_tool

        mock_fetch.return_value = make_trending_df(40, "bullish")
        result = get_forex_indicators_tool.invoke({
            "symbol": "EURUSD",
            "timeframe": "H1",
            "spread_pips": 1.2,
            "trade_date": "2026-03-10",
        })

        assert "EURUSD [H1] Indicator Snapshot" in result
        assert "ATR(14)" in result
        assert "Spread: 1.2 pips" in result

    @patch("tradingagents.agents.utils.forex_tools.fetch_multi_timeframe_data")
    def test_get_multi_timeframe_indicators_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_multi_timeframe_indicators_tool

        mock_bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={
                Timeframe.D1: make_trending_df(30, "bullish"),
                Timeframe.H4: make_trending_df(30, "bullish"),
                Timeframe.H1: make_trending_df(30, "bullish"),
            },
        )
        mock_fetch.return_value = mock_bundle
        result = get_multi_timeframe_indicators_tool.invoke({
            "symbol": "EURUSD",
            "spread_pips": 1.0,
            "trade_date": "2026-03-10",
        })

        assert "Multi-Timeframe Technical Indicator Matrix" in result
        assert "| **D1** |" in result
        assert "| **H4** |" in result
        assert "| **H1** |" in result

