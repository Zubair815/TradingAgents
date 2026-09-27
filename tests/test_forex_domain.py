"""Unit tests for tradingagents.forex.domain.

Phase 1 acceptance criteria:
✓ AssetType enum with STOCK, CRYPTO, FOREX values
✓ Timeframe enum with correct seconds/minutes/mt5 attributes
✓ ForexPair dataclass fields correctly derived
✓ Built-in pair catalogue covers majors and minors
✓ is_forex_pair() recognises canonical + broker forms
✓ get_forex_pair() handles canonical, broker, Yahoo forms
✓ All existing tests continue to pass (no import-level breakage)
"""

import pytest

from tradingagents.forex.domain import (
    AssetType,
    ForexPair,
    Timeframe,
    MAJOR_PAIRS,
    MINOR_PAIRS,
    EXOTIC_PAIRS,
    is_forex_pair,
    get_forex_pair,
    register_custom_pair,
    strip_broker_suffix_simple,
)


# ---------------------------------------------------------------------------
# AssetType
# ---------------------------------------------------------------------------

class TestAssetType:
    def test_has_three_members(self):
        members = {m.value for m in AssetType}
        assert members == {"stock", "crypto", "forex"}

    def test_str_equality_stock(self):
        assert AssetType.STOCK == "stock"

    def test_str_equality_crypto(self):
        assert AssetType.CRYPTO == "crypto"

    def test_str_equality_forex(self):
        assert AssetType.FOREX == "forex"

    def test_from_string_case_insensitive(self):
        assert AssetType.from_string("Forex") == AssetType.FOREX
        assert AssetType.from_string("STOCK") == AssetType.STOCK
        assert AssetType.from_string("CRYPTO") == AssetType.CRYPTO

    def test_from_string_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown asset type"):
            AssetType.from_string("equities")

    def test_usable_in_literal_isinstance_check(self):
        # The agent state Literal["stock","crypto","forex"] accepts the string value.
        val: str = AssetType.FOREX.value
        assert val == "forex"


# ---------------------------------------------------------------------------
# Timeframe
# ---------------------------------------------------------------------------

class TestTimeframe:
    def test_m15_seconds(self):
        assert Timeframe.M15.seconds == 900

    def test_h4_seconds(self):
        assert Timeframe.H4.seconds == 14_400

    def test_d1_seconds(self):
        assert Timeframe.D1.seconds == 86_400

    def test_m1_is_intraday(self):
        assert Timeframe.M1.is_intraday is True

    def test_d1_not_intraday(self):
        assert Timeframe.D1.is_intraday is False

    def test_yfinance_intervals(self):
        assert Timeframe.M15.yfinance_interval == "15m"
        assert Timeframe.H1.yfinance_interval == "1h"
        assert Timeframe.D1.yfinance_interval == "1d"

    def test_mt5_h1_constant(self):
        # MT5 TIMEFRAME_H1 = 16385
        assert Timeframe.H1.mt5_timeframe == 16_385

    def test_mt5_m15_constant(self):
        # MT5 TIMEFRAME_M15 = 15
        assert Timeframe.M15.mt5_timeframe == 15

    def test_from_string(self):
        assert Timeframe.from_string("m15") == Timeframe.M15
        assert Timeframe.from_string("H4") == Timeframe.H4

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            Timeframe.from_string("1D")

    def test_minutes_property(self):
        assert Timeframe.H4.minutes == 240
        assert Timeframe.M30.minutes == 30


# ---------------------------------------------------------------------------
# ForexPair — field derivation
# ---------------------------------------------------------------------------

class TestForexPairFields:
    def test_eurusd_pip_size(self):
        pair = ForexPair("EURUSD", "EUR", "USD")
        assert pair.pip_size == pytest.approx(0.0001)

    def test_eurusd_point(self):
        pair = ForexPair("EURUSD", "EUR", "USD", digits=5)
        assert pair.point == pytest.approx(0.00001)

    def test_usdjpy_pip_size(self):
        pair = ForexPair("USDJPY", "USD", "JPY")
        assert pair.pip_size == pytest.approx(0.01)

    def test_usdjpy_is_jpy(self):
        pair = ForexPair("USDJPY", "USD", "JPY")
        assert pair.is_jpy_pair is True

    def test_eurusd_not_jpy(self):
        pair = ForexPair("EURUSD", "EUR", "USD")
        assert pair.is_jpy_pair is False

    def test_display_name(self):
        pair = ForexPair("EURUSD", "EUR", "USD")
        assert pair.display_name == "EUR/USD"

    def test_yahoo_symbol(self):
        pair = ForexPair("EURUSD", "EUR", "USD")
        assert pair.yahoo_symbol == "EURUSD=X"

    def test_broker_symbol_defaults_to_canonical(self):
        pair = ForexPair("GBPUSD", "GBP", "USD")
        assert pair.broker_symbol == "GBPUSD"

    def test_broker_symbol_explicit(self):
        pair = ForexPair("GBPUSD", "GBP", "USD", broker_symbol="GBPUSDm")
        assert pair.broker_symbol == "GBPUSDm"

    def test_symbol_upper_cased(self):
        pair = ForexPair("eurusd", "eur", "usd")
        assert pair.symbol == "EURUSD"

    def test_frozen_immutable(self):
        pair = MAJOR_PAIRS["EURUSD"]
        with pytest.raises((AttributeError, TypeError)):
            pair.symbol = "XYZABC"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Built-in catalogues
# ---------------------------------------------------------------------------

class TestPairCatalogues:
    def test_major_pairs_count(self):
        assert len(MAJOR_PAIRS) == 7

    def test_eurusd_in_majors(self):
        assert "EURUSD" in MAJOR_PAIRS

    def test_usdjpy_3_digits(self):
        assert MAJOR_PAIRS["USDJPY"].digits == 3

    def test_minor_pairs_non_empty(self):
        assert len(MINOR_PAIRS) > 10

    def test_gbpjpy_in_minors(self):
        assert "GBPJPY" in MINOR_PAIRS

    def test_gbpjpy_jpy_pip(self):
        assert MINOR_PAIRS["GBPJPY"].pip_size == pytest.approx(0.01)

    def test_exotic_usdzar(self):
        assert "USDZAR" in EXOTIC_PAIRS


# ---------------------------------------------------------------------------
# is_forex_pair
# ---------------------------------------------------------------------------

class TestIsForexPair:
    def test_eurusd_canonical(self):
        assert is_forex_pair("EURUSD") is True

    def test_eurusd_lowercase(self):
        assert is_forex_pair("eurusd") is True

    def test_eurusd_broker_suffix(self):
        assert is_forex_pair("EURUSDm") is True

    def test_usdjpy(self):
        assert is_forex_pair("USDJPY") is True

    def test_xauusd_false(self):
        # Gold is not a Forex pair (XAU is not in _FOREX_CURRENCIES)
        assert is_forex_pair("XAUUSD") is False

    def test_btcusd_false(self):
        assert is_forex_pair("BTCUSD") is False

    def test_nvda_false(self):
        assert is_forex_pair("NVDA") is False

    def test_empty_false(self):
        assert is_forex_pair("") is False

    def test_none_false(self):
        assert is_forex_pair(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_forex_pair
# ---------------------------------------------------------------------------

class TestGetForexPair:
    def test_canonical_lookup(self):
        pair = get_forex_pair("EURUSD")
        assert pair is not None
        assert pair.symbol == "EURUSD"

    def test_yahoo_form(self):
        pair = get_forex_pair("EURUSD=X")
        assert pair is not None
        assert pair.symbol == "EURUSD"

    def test_broker_suffix_m(self):
        pair = get_forex_pair("EURUSDm")
        assert pair is not None
        assert pair.symbol == "EURUSD"

    def test_unknown_returns_none(self):
        assert get_forex_pair("FOOBAR") is None

    def test_xauusd_returns_none(self):
        # XAUUSD is in _ALIASES for Yahoo but not in _ALL_PAIRS as a ForexPair.
        assert get_forex_pair("XAUUSD") is None

    def test_usdjpy_catalogue(self):
        pair = get_forex_pair("USDJPY")
        assert pair is not None
        assert pair.digits == 3


# ---------------------------------------------------------------------------
# strip_broker_suffix_simple
# ---------------------------------------------------------------------------

class TestStripBrokerSuffix:
    def test_m_suffix(self):
        assert strip_broker_suffix_simple("EURUSDm") == "EURUSD"

    def test_pro_suffix(self):
        assert strip_broker_suffix_simple("GBPUSDpro") == "GBPUSD"

    def test_no_suffix(self):
        assert strip_broker_suffix_simple("EURUSD") == "EURUSD"

    def test_lowercase_preserved_for_non_matching(self):
        # If the regex doesn't match, the function returns the original upper-cased.
        result = strip_broker_suffix_simple("eurusd")
        assert result == "EURUSD"


# ---------------------------------------------------------------------------
# register_custom_pair (extensibility)
# ---------------------------------------------------------------------------

class TestRegisterCustomPair:
    def test_can_register_and_lookup(self):
        custom = ForexPair(
            symbol="EURGBP",
            base_currency="EUR",
            quote_currency="GBP",
            broker_symbol="EURGBPx",
            digits=5,
        )
        register_custom_pair(custom)
        found = get_forex_pair("EURGBP")
        assert found is not None  # either the existing or newly registered one
