"""Tests for Forex Multi-Timeframe OHLCV Market Data (Phase 3).

Validates:
- ForexBar dataclass (bullish/bearish, body, range, wicks, body_pips, range_pips).
- MultiTimeframeData container (timeframe indexing, latest bar/close, summaries).
- validate_forex_candles (schema integrity, numeric conversions, High >= Low, deduplication).
- filter_candles_by_cutoff (zero-lookahead point-in-time enforcement).
- resample_candles (synthetic H4 generation from H1, M5 -> M15, downsampling rejection).
- fetch_forex_candles & caching (canonical mapping, H4 resampling, count limits, error handling).
- fetch_multi_timeframe_data (bundling multiple timeframes).
- Formatting utilities (pair-accurate decimal precision in CSV and markdown summaries).
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.forex_data import (
    ForexBar,
    MultiTimeframeData,
    fetch_forex_candles,
    fetch_multi_timeframe_data,
    filter_candles_by_cutoff,
    format_forex_candles_csv,
    format_multi_timeframe_summary,
    resample_candles,
    validate_forex_candles,
)
from tradingagents.forex.domain import Timeframe, get_forex_pair

# ---------------------------------------------------------------------------
# Helper: Synthetic Candle Generators for Deterministic Tests
# ---------------------------------------------------------------------------


def make_sample_h1_df(num_bars: int = 12, start_hour: int = 0) -> pd.DataFrame:
    """Generate deterministic H1 candles starting at 2026-03-10 start_hour:00:00 UTC."""
    timestamps = [
        datetime(2026, 3, 10, (start_hour + i) % 24, 0, tzinfo=timezone.utc)
        for i in range(num_bars)
    ]
    # Synthetic prices oscillating around 1.0800
    opens = [1.0800 + i * 0.0005 for i in range(num_bars)]
    highs = [o + 0.0010 for o in opens]
    lows = [o - 0.0008 for o in opens]
    closes = [o + 0.0004 for o in opens]
    volumes = [1000 + i * 100 for i in range(num_bars)]

    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


def make_sample_m5_df(num_bars: int = 9) -> pd.DataFrame:
    """Generate deterministic M5 candles starting at 2026-03-10 10:00:00 UTC."""
    from datetime import timedelta
    timestamps = [
        datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 5)
        for i in range(num_bars)
    ]
    opens = [1.0850 + i * 0.0002 for i in range(num_bars)]
    highs = [o + 0.0005 for o in opens]
    lows = [o - 0.0003 for o in opens]
    closes = [o + 0.0001 for o in opens]
    volumes = [200 + i * 20 for i in range(num_bars)]


    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


# ---------------------------------------------------------------------------
# 1. ForexBar Dataclass Tests
# ---------------------------------------------------------------------------


class TestForexBar:
    def test_bullish_bar(self):
        bar = ForexBar(
            timestamp=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
            open=1.0800,
            high=1.0860,
            low=1.0790,
            close=1.0850,
            volume=5000,
        )
        assert bar.is_bullish is True
        assert bar.is_bearish is False
        assert pytest.approx(bar.body, 1e-6) == 0.0050
        assert pytest.approx(bar.range, 1e-6) == 0.0070
        assert pytest.approx(bar.upper_wick, 1e-6) == 0.0010
        assert pytest.approx(bar.lower_wick, 1e-6) == 0.0010

    def test_bearish_bar(self):
        bar = ForexBar(
            timestamp=datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc),
            open=1.0850,
            high=1.0860,
            low=1.0780,
            close=1.0790,
            volume=4200,
        )
        assert bar.is_bearish is True
        assert bar.is_bullish is False
        assert pytest.approx(bar.body, 1e-6) == 0.0060
        assert pytest.approx(bar.range, 1e-6) == 0.0080
        assert pytest.approx(bar.upper_wick, 1e-6) == 0.0010
        assert pytest.approx(bar.lower_wick, 1e-6) == 0.0010

    def test_neutral_bar(self):
        bar = ForexBar(
            timestamp=datetime(2026, 3, 10, 16, 0, tzinfo=timezone.utc),
            open=1.0800,
            high=1.0820,
            low=1.0780,
            close=1.0800,
        )
        assert bar.is_bullish is True
        assert bar.is_bearish is False
        assert bar.body == 0.0
        assert pytest.approx(bar.range, 1e-6) == 0.0040

    def test_pip_calculations_eurusd(self):
        bar = ForexBar(
            timestamp=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
            open=1.0800,
            high=1.0850,
            low=1.0780,
            close=1.0830,
        )
        # EURUSD pip_size is 0.0001
        assert pytest.approx(bar.body_pips("EURUSD"), 1e-4) == 30.0
        assert pytest.approx(bar.range_pips("EURUSD"), 1e-4) == 70.0

    def test_pip_calculations_usdjpy(self):
        bar = ForexBar(
            timestamp=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
            open=150.00,
            high=150.80,
            low=149.70,
            close=150.50,
        )
        # USDJPY pip_size is 0.01
        assert pytest.approx(bar.body_pips("USDJPY"), 1e-4) == 50.0
        assert pytest.approx(bar.range_pips("USDJPY"), 1e-4) == 110.0


# ---------------------------------------------------------------------------
# 2. MultiTimeframeData Container Tests
# ---------------------------------------------------------------------------


class TestMultiTimeframeData:
    def test_container_initialization_and_access(self):
        df_h1 = make_sample_h1_df(10)
        df_m5 = make_sample_m5_df(6)

        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={Timeframe.H1: df_h1, Timeframe.M5: df_m5},
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )

        assert Timeframe.H1 in bundle.available_timeframes
        assert Timeframe.M5 in bundle.available_timeframes
        assert bundle.get_timeframe(Timeframe.H1) is df_h1
        assert bundle.get_timeframe("H1") is df_h1
        assert bundle.get_timeframe("5m") is df_m5

    def test_missing_timeframe_raises_key_error(self):
        bundle = MultiTimeframeData(symbol="EURUSD", candles={})
        with pytest.raises(KeyError, match="Timeframe D1 not found"):
            bundle.get_timeframe(Timeframe.D1)

    def test_latest_bar_and_close(self):
        df_h1 = make_sample_h1_df(5)
        bundle = MultiTimeframeData(symbol="EURUSD", candles={Timeframe.H1: df_h1})

        bar = bundle.latest_bar(Timeframe.H1)
        assert bar is not None
        assert bar["Close"] == df_h1.iloc[-1]["Close"]

        close = bundle.latest_close("H1")
        assert close == df_h1.iloc[-1]["Close"]

    def test_latest_close_missing_timeframe_returns_none(self):
        bundle = MultiTimeframeData(symbol="EURUSD", candles={})
        assert bundle.latest_close(Timeframe.H1) is None
        assert bundle.latest_bar(Timeframe.H1) is None

    def test_to_summary_dict(self):
        df_h1 = make_sample_h1_df(5)
        bundle = MultiTimeframeData(
            symbol="EURUSD",
            candles={Timeframe.H1: df_h1},
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )
        s = bundle.to_summary_dict()
        assert s["symbol"] == "EURUSD"
        assert "H1" in s["timeframes"]
        assert s["timeframes"]["H1"]["bars"] == 5


# ---------------------------------------------------------------------------
# 3. Candle Validation Tests
# ---------------------------------------------------------------------------


class TestValidateForexCandles:
    def test_valid_df_passes(self):
        df = make_sample_h1_df(5)
        validated = validate_forex_candles(df)
        assert len(validated) == 5
        assert list(validated.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]

    def test_empty_or_none_df_raises(self):
        with pytest.raises(ValueError, match="Cannot validate an empty"):
            validate_forex_candles(pd.DataFrame())

    def test_missing_required_column_raises(self):
        df = pd.DataFrame({"Date": [datetime.now()], "Open": [1.0], "High": [1.1], "Low": [0.9]})
        with pytest.raises(ValueError, match="Required OHLCV column 'Close' missing"):
            validate_forex_candles(df)

    def test_datetime_column_renamed_to_date(self):
        df = pd.DataFrame({
            "Datetime": [datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)],
            "Open": [1.08], "High": [1.09], "Low": [1.07], "Close": [1.085], "Volume": [100],
        })
        validated = validate_forex_candles(df)
        assert "Date" in validated.columns
        assert "Datetime" not in validated.columns

    def test_high_less_than_low_raises(self):
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)],
            "Open": [1.08], "High": [1.05], "Low": [1.09], "Close": [1.07], "Volume": [100],
        })
        with pytest.raises(ValueError, match="High < Low"):
            validate_forex_candles(df)

    def test_float_precision_repair_high_low(self):
        # Open is 1.0850, High is 1.08499 due to slight float rounding
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)],
            "Open": [1.0850], "High": [1.0849], "Low": [1.0800], "Close": [1.0830], "Volume": [100],
        })
        with pytest.raises(ValueError, match="OHLC geometry"):
            validate_forex_candles(df)

    def test_unsorted_and_duplicate_dates_handled(self):
        t1 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc)
        df = pd.DataFrame({
            "Date": [t2, t1, t1],  # Unsorted with duplicate t1
            "Open": [1.082, 1.080, 1.081],
            "High": [1.085, 1.083, 1.084],
            "Low": [1.079, 1.078, 1.079],
            "Close": [1.083, 1.081, 1.082],
            "Volume": [200, 100, 150],
        })
        with pytest.raises(ValueError, match="duplicate"):
            validate_forex_candles(df)


# ---------------------------------------------------------------------------
# 4. Point-In-Time Cutoff Tests
# ---------------------------------------------------------------------------


class TestFilterCandlesByCutoff:
    def test_strict_exclusion_of_future_bars(self):
        df = make_sample_h1_df(10, start_hour=8)  # 08:00 to 17:00
        cutoff = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)

        filtered = filter_candles_by_cutoff(df, cutoff, Timeframe.H1)
        assert len(filtered) == 4  # Only 08:00 through 11:00 have closed
        assert filtered["Date"].max() <= cutoff

    def test_string_cutoff_parsed_accurately(self):
        df = make_sample_h1_df(10, start_hour=8)
        filtered = filter_candles_by_cutoff(df, "2026-03-10 11:00:00", Timeframe.H1)
        assert len(filtered) == 3  # 08, 09, 10 have closed
        assert filtered["Date"].max() == datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)

    def test_none_cutoff_leaves_df_unchanged(self):
        df = make_sample_h1_df(5)
        filtered = filter_candles_by_cutoff(df, None, Timeframe.H1)
        assert len(filtered) == 5


# ---------------------------------------------------------------------------
# 5. Resampling Engine Tests (H1 -> H4, M5 -> M15)
# ---------------------------------------------------------------------------


class TestResampleCandles:
    def test_h1_to_h4_resampling(self):
        # 8 hourly bars (00:00 through 07:00) should form exactly two 4-hour bars:
        # Bar 1: 00:00 - 03:00 (hours 0, 1, 2, 3)
        # Bar 2: 04:00 - 07:00 (hours 4, 5, 6, 7)
        h1_df = make_sample_h1_df(8, start_hour=0)
        h4_df = resample_candles(h1_df, target_timeframe=Timeframe.H4, source_timeframe=Timeframe.H1)

        assert len(h4_df) == 2
        # Bar 1 check:
        # Open should match first bar (hour 0)
        assert h4_df.iloc[0]["Open"] == h1_df.iloc[0]["Open"]
        # High should be max of first 4 hours
        assert h4_df.iloc[0]["High"] == h1_df.iloc[:4]["High"].max()
        # Low should be min of first 4 hours
        assert h4_df.iloc[0]["Low"] == h1_df.iloc[:4]["Low"].min()
        # Close should be close of hour 3
        assert h4_df.iloc[0]["Close"] == h1_df.iloc[3]["Close"]
        # Volume should be sum of first 4 hours
        assert h4_df.iloc[0]["Volume"] == h1_df.iloc[:4]["Volume"].sum()

        # Bar 2 check:
        assert h4_df.iloc[1]["Open"] == h1_df.iloc[4]["Open"]
        assert h4_df.iloc[1]["Close"] == h1_df.iloc[7]["Close"]

    def test_m5_to_m15_resampling(self):
        # 9 5-minute bars form exactly three 15-minute bars
        m5_df = make_sample_m5_df(9)
        m15_df = resample_candles(m5_df, target_timeframe=Timeframe.M15, source_timeframe=Timeframe.M5)

        assert len(m15_df) == 3
        assert m15_df.iloc[0]["Open"] == m5_df.iloc[0]["Open"]
        assert m15_df.iloc[0]["Close"] == m5_df.iloc[2]["Close"]
        assert m15_df.iloc[0]["High"] == m5_df.iloc[:3]["High"].max()
        assert m15_df.iloc[0]["Low"] == m5_df.iloc[:3]["Low"].min()

    def test_downsampling_raises_value_error(self):
        h1_df = make_sample_h1_df(5)
        with pytest.raises(ValueError, match="Cannot resample from higher timeframe"):
            resample_candles(h1_df, target_timeframe=Timeframe.M15, source_timeframe=Timeframe.H1)

    def test_same_timeframe_returns_copy(self):
        h1_df = make_sample_h1_df(5)
        res = resample_candles(h1_df, target_timeframe=Timeframe.H1, source_timeframe=Timeframe.H1)
        assert len(res) == len(h1_df)


# ---------------------------------------------------------------------------
# 6. Fetching & Caching Tests (Mocked yfinance)
# ---------------------------------------------------------------------------


class TestFetchForexCandles:
    @patch("yfinance.download")
    def test_canonical_and_broker_symbol_resolution(self, mock_download, tmp_path):
        mock_df = make_sample_h1_df(10)
        mock_download.return_value = mock_df.copy()

        with patch("tradingagents.dataflows.forex_data.get_config", return_value={"data_cache_dir": str(tmp_path), "forex_market_source": "yahoo", "forex_candle_max_age_seconds": 10**9}):
            # Pass XM broker symbol EURUSDm -> should query Yahoo with EURUSD=X
            df = fetch_forex_candles("EURUSDm", timeframe=Timeframe.H1, use_cache=False)
            assert len(df) == 10
            # Ensure yfinance called with EURUSD=X
            assert mock_download.call_args[0][0] == "EURUSD=X"

    @patch("yfinance.download")
    def test_h4_synthesized_via_h1(self, mock_download, tmp_path):
        # 16 H1 bars simulate 4 H4 bars
        mock_df = make_sample_h1_df(16, start_hour=0)
        mock_download.return_value = mock_df.copy()

        with patch("tradingagents.dataflows.forex_data.get_config", return_value={"data_cache_dir": str(tmp_path), "forex_market_source": "yahoo", "forex_candle_max_age_seconds": 10**9}):
            h4_df = fetch_forex_candles("EURUSD", timeframe=Timeframe.H4, count=4, use_cache=False)
            # Should have called yfinance with 1h interval
            assert mock_download.call_args[1]["interval"] == "1h"
            assert len(h4_df) == 4

    @patch("yfinance.download")
    def test_count_limit_enforced(self, mock_download, tmp_path):
        mock_df = make_sample_h1_df(20)
        mock_download.return_value = mock_df.copy()

        with patch("tradingagents.dataflows.forex_data.get_config", return_value={"data_cache_dir": str(tmp_path), "forex_market_source": "yahoo", "forex_candle_max_age_seconds": 10**9}):
            df = fetch_forex_candles("EURUSD", timeframe=Timeframe.H1, count=5, use_cache=False)
            assert len(df) == 5

    @patch("yfinance.download")
    def test_timezone_aware_history_boundaries_use_yahoo_date_strings(self, mock_download, tmp_path):
        mock_download.return_value = make_sample_h1_df(12)
        start = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 7, 30, tzinfo=timezone.utc)

        with patch("tradingagents.dataflows.forex_data.get_config", return_value={
            "data_cache_dir": str(tmp_path),
            "forex_market_source": "yahoo",
            "forex_candle_max_age_seconds": 10**9,
        }):
            df = fetch_forex_candles(
                "EURUSD", timeframe=Timeframe.H1, count=None, as_of=end,
                start_date=start, end_date=end, use_cache=False,
            )

        assert mock_download.call_args.kwargs["start"] == "2026-03-10"
        assert mock_download.call_args.kwargs["end"] == "2026-03-11"
        assert len(df) == 7

    @patch("yfinance.download")
    def test_empty_response_raises_no_market_data(self, mock_download, tmp_path):
        mock_download.return_value = pd.DataFrame()

        with (
            patch("tradingagents.dataflows.forex_data.get_config", return_value={"data_cache_dir": str(tmp_path), "forex_market_source": "yahoo", "forex_candle_max_age_seconds": 10**9}),
            pytest.raises(NoMarketDataError, match="no candles"),
        ):
            fetch_forex_candles("UNKNOWNPAIR", timeframe=Timeframe.H1, use_cache=False)


    @patch("yfinance.download")
    def test_caching_avoids_repeated_download(self, mock_download, tmp_path):
        mock_df = make_sample_h1_df(10)
        mock_download.return_value = mock_df.copy()

        with patch("tradingagents.dataflows.forex_data.get_config", return_value={"data_cache_dir": str(tmp_path), "forex_market_source": "yahoo", "forex_candle_max_age_seconds": 10**9}):
            # First call fetches from yfinance and writes cache
            df1 = fetch_forex_candles("EURUSD", timeframe=Timeframe.H1, use_cache=True)
            assert mock_download.call_count == 1

            # Second call should read from disk cache without hitting mock_download
            df2 = fetch_forex_candles("EURUSD", timeframe=Timeframe.H1, use_cache=True)
            assert mock_download.call_count == 1
            assert len(df1) == len(df2)


# ---------------------------------------------------------------------------
# 7. Multi-Timeframe Fetcher Tests
# ---------------------------------------------------------------------------


class TestFetchMultiTimeframeData:
    @patch("tradingagents.dataflows.forex_data.fetch_forex_candles")
    def test_bundles_all_timeframes(self, mock_fetch):
        df_d1 = make_sample_h1_df(10)
        df_h4 = make_sample_h1_df(8)
        df_h1 = make_sample_h1_df(15)
        df_m15 = make_sample_m5_df(20)

        def mock_fetch_impl(symbol, timeframe, **kwargs):
            tf = Timeframe.from_string(timeframe) if isinstance(timeframe, str) else timeframe
            mapping = {
                Timeframe.D1: df_d1,
                Timeframe.H4: df_h4,
                Timeframe.H1: df_h1,
                Timeframe.M15: df_m15,
            }
            return mapping[tf]

        mock_fetch.side_effect = mock_fetch_impl

        bundle = fetch_multi_timeframe_data(
            "EURUSD",
            timeframes=(Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15),
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )

        assert isinstance(bundle, MultiTimeframeData)
        assert bundle.symbol == "EURUSD"
        assert bundle.pair is not None
        assert Timeframe.D1 in bundle.available_timeframes
        assert Timeframe.H4 in bundle.available_timeframes
        assert Timeframe.H1 in bundle.available_timeframes
        assert Timeframe.M15 in bundle.available_timeframes


# ---------------------------------------------------------------------------
# 8. Formatting Utilities Tests
# ---------------------------------------------------------------------------


class TestFormattingUtilities:
    def test_format_forex_candles_csv_eurusd(self):
        df = make_sample_h1_df(3)
        csv_str = format_forex_candles_csv(df, pair="EURUSD")
        # EURUSD uses 5 digits
        assert "EUR/USD" in csv_str
        assert "1.08000" in csv_str or "1.08040" in csv_str

    def test_format_forex_candles_csv_usdjpy(self):
        df = pd.DataFrame({
            "Date": [datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)],
            "Open": [150.1234],
            "High": [150.8012],
            "Low": [149.9011],
            "Close": [150.5099],
            "Volume": [1000],
        })
        csv_str = format_forex_candles_csv(df, pair="USDJPY")
        # USDJPY should be rounded to 3 digits (150.510 or 150.123)
        assert "USD/JPY" in csv_str
        assert "150.123" in csv_str
        assert "150.510" in csv_str

    def test_format_multi_timeframe_summary(self):
        df_h1 = make_sample_h1_df(5)
        df_h4 = make_sample_h1_df(3)
        bundle = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={Timeframe.H4: df_h4, Timeframe.H1: df_h1},
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )

        md = format_multi_timeframe_summary(bundle)
        assert "Multi-Timeframe Market Data: EUR/USD" in md
        assert "Timeframe H1" in md
        assert "Timeframe H4" in md
        assert "BULLISH" in md or "BEARISH" in md


# ---------------------------------------------------------------------------
# 9. LangChain Forex Tools Tests
# ---------------------------------------------------------------------------


class TestForexTools:
    @patch("tradingagents.agents.utils.forex_tools.fetch_forex_candles")
    def test_get_forex_candles_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_forex_candles_tool

        mock_fetch.return_value = make_sample_h1_df(5)
        res = get_forex_candles_tool.invoke({"symbol": "EURUSD", "timeframe": "H1", "count": 5})
        assert "EUR/USD" in res
        assert "Date" in res
        assert "Close" in res

    @patch("tradingagents.agents.utils.forex_tools.fetch_multi_timeframe_data")
    def test_get_multi_timeframe_data_tool(self, mock_fetch):
        from tradingagents.agents.utils.forex_tools import get_multi_timeframe_data_tool

        df_h1 = make_sample_h1_df(5)
        mock_fetch.return_value = MultiTimeframeData(
            symbol="EURUSD",
            pair=get_forex_pair("EURUSD"),
            candles={Timeframe.H1: df_h1},
            as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )
        res = get_multi_timeframe_data_tool.invoke({"symbol": "EURUSD"})
        assert "Multi-Timeframe Market Data: EUR/USD" in res
        assert "Timeframe H1" in res

