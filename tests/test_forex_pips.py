"""Unit tests for tradingagents.forex.pips — deterministic pip calculations.

All tests are pure arithmetic: no LLM calls, no network I/O.
Phase 1 acceptance criteria verified here:
✓ pip_size_for returns correct values for majors and JPY pairs
✓ pips_between / pips_directional / pips_to_price / price_to_pips are
  arithmetically correct
✓ lot_size_from_risk produces correct risk-normalised lot sizes
✓ pip_value_in_account_currency works for USD-quoted and JPY-quoted pairs
✓ risk_reward_ratio validates LONG and SHORT geometry
✓ Error cases raise ValueError, not silent bad values
"""


import pytest

from tradingagents.forex.domain import MAJOR_PAIRS
from tradingagents.forex.pips import (
    lot_size_from_risk,
    normalize_price,
    pip_size_for,
    pip_value_in_account_currency,
    pips_between,
    pips_directional,
    pips_to_price,
    price_to_pips,
    risk_reward_ratio,
)


def test_normalize_price_uses_broker_digits_and_point():
    assert normalize_price(1.234567, 5, 0.00001) == 1.23457
    assert normalize_price(150.1236, 3, 0.001) == 150.124
    assert normalize_price(1.2349, 3, 0.005) == 1.235


def test_explicit_broker_contract_size_controls_pip_value_and_lot_size():
    assert pip_value_in_account_currency(
        "EURUSD", 1.0, "USD", contract_size=10_000.0
    ) == pytest.approx(1.0)
    standard = lot_size_from_risk(
        10_000.0, 1.0, 1.1000, 1.0900, "EURUSD", contract_size=100_000.0
    )
    mini = lot_size_from_risk(
        10_000.0, 1.0, 1.1000, 1.0900, "EURUSD", contract_size=10_000.0
    )
    assert mini["pip_value_per_lot"] == pytest.approx(standard["pip_value_per_lot"] / 10.0)
    assert mini["lot_size"] == pytest.approx(standard["lot_size"] * 10.0)

# ---------------------------------------------------------------------------
# pip_size_for
# ---------------------------------------------------------------------------

class TestPipSizeFor:
    def test_eurusd(self):
        assert pip_size_for("EURUSD") == pytest.approx(0.0001)

    def test_eurusd_broker_form(self):
        assert pip_size_for("EURUSDm") == pytest.approx(0.0001)

    def test_usdjpy(self):
        assert pip_size_for("USDJPY") == pytest.approx(0.01)

    def test_gbpjpy(self):
        assert pip_size_for("GBPJPY") == pytest.approx(0.01)

    def test_unknown_usd_pair_default(self):
        # Unknown pair without JPY → 0.0001
        assert pip_size_for("FOOBAR") == pytest.approx(0.0001)

    def test_unknown_jpy_heuristic(self):
        # Heuristic: if "JPY" in quote position → 0.01
        assert pip_size_for("XXXJPY") == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# pips_between
# ---------------------------------------------------------------------------

class TestPipsBetween:
    def test_eurusd_50_pips(self):
        result = pips_between(1.1000, 1.1050, 0.0001)
        assert result == pytest.approx(50.0)

    def test_eurusd_order_invariant(self):
        assert pips_between(1.1050, 1.1000, 0.0001) == pytest.approx(50.0)

    def test_usdjpy_30_pips(self):
        result = pips_between(140.00, 140.30, 0.01)
        assert result == pytest.approx(30.0)

    def test_zero_pip_size_raises(self):
        with pytest.raises(ValueError, match="pip_size must be positive"):
            pips_between(1.1000, 1.1050, 0.0)

    def test_negative_pip_size_raises(self):
        with pytest.raises(ValueError):
            pips_between(1.1000, 1.1050, -0.0001)


# ---------------------------------------------------------------------------
# pips_directional
# ---------------------------------------------------------------------------

class TestPipsDirectional:
    def test_long_positive(self):
        # Bought at 1.1000, price moved to 1.1050 → +50 pips
        assert pips_directional(1.1000, 1.1050, 0.0001) == pytest.approx(50.0)

    def test_long_negative(self):
        # Bought at 1.1000, price moved to 1.0950 → -50 pips (adverse)
        assert pips_directional(1.1000, 1.0950, 0.0001) == pytest.approx(-50.0)

    def test_short_positive(self):
        # Sold at 1.1050, price moved to 1.1000 → +50 pips
        assert pips_directional(1.1050, 1.1000, 0.0001) == pytest.approx(-50.0)


# ---------------------------------------------------------------------------
# pips_to_price / price_to_pips
# ---------------------------------------------------------------------------

class TestPipsConversion:
    def test_pips_to_price_eurusd(self):
        assert pips_to_price(25, 0.0001) == pytest.approx(0.0025)

    def test_pips_to_price_jpy(self):
        assert pips_to_price(30, 0.01) == pytest.approx(0.30)

    def test_price_to_pips_eurusd(self):
        assert price_to_pips(0.0025, 0.0001) == pytest.approx(25.0)

    def test_price_to_pips_absolute_value(self):
        # Negative distance should still return positive pips.
        assert price_to_pips(-0.0050, 0.0001) == pytest.approx(50.0)

    def test_round_trip(self):
        pips = 47.3
        p = pips_to_price(pips, 0.0001)
        back = price_to_pips(p, 0.0001)
        assert back == pytest.approx(pips)

    def test_zero_pip_size_raises(self):
        with pytest.raises(ValueError):
            price_to_pips(0.0025, 0.0)


# ---------------------------------------------------------------------------
# pip_value_in_account_currency
# ---------------------------------------------------------------------------

class TestPipValue:
    def test_eurusd_usd_account_1_lot(self):
        pair = MAJOR_PAIRS["EURUSD"]
        # EURUSD, quote=USD, account=USD: 0.0001 × 100000 × 1.0 = 10.0
        val = pip_value_in_account_currency(pair, 1.0, "USD")
        assert val == pytest.approx(10.0)

    def test_eurusd_usd_account_001_lot(self):
        pair = MAJOR_PAIRS["EURUSD"]
        val = pip_value_in_account_currency(pair, 0.01, "USD")
        assert val == pytest.approx(0.10)

    def test_usdjpy_usd_account_with_rate(self):
        pair = MAJOR_PAIRS["USDJPY"]
        # USDJPY, quote=JPY, base=USD, account=USD.
        # pip_value = 0.01 × 100000 × 1.0 / 140.0 ≈ 7.14
        val = pip_value_in_account_currency(pair, 1.0, "USD", current_quote_price=140.0)
        assert val == pytest.approx(0.01 * 100_000 / 140.0, rel=1e-4)

    def test_string_symbol(self):
        val = pip_value_in_account_currency("EURUSD", 0.10, "USD")
        assert val == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# lot_size_from_risk
# ---------------------------------------------------------------------------

class TestLotSizeFromRisk:
    def test_eurusd_basic(self):
        """1% of $500 account, 25-pip stop on EURUSD → 0.02 lots."""
        pair = MAJOR_PAIRS["EURUSD"]
        result = lot_size_from_risk(
            account_equity=500.0,
            risk_percent=1.0,
            entry_price=1.1000,
            stop_loss_price=1.0975,   # 25 pips
            pair=pair,
            account_currency="USD",
            volume_step=0.01,
        )
        assert result["risk_amount"] == pytest.approx(5.0)
        assert result["stop_distance_pips"] == pytest.approx(25.0)
        assert result["lot_size"] == pytest.approx(0.02)

    def test_lot_floored_to_volume_step(self):
        """Verify floor (not round) to volume step — correct arithmetic."""
        pair = MAJOR_PAIRS["EURUSD"]
        result = lot_size_from_risk(
            account_equity=10_000.0,
            risk_percent=1.0,
            entry_price=1.1000,
            stop_loss_price=1.0990,   # 10 pips
            pair=pair,
            account_currency="USD",
            volume_step=0.01,
        )
        # risk_amount = $100, stop_pips=10, pip_val=$10 → raw_lot = 100/(10×10) = 1.0
        assert result["stop_distance_pips"] == pytest.approx(10.0)
        assert result["raw_lot_size"] == pytest.approx(1.0)
        assert result["lot_size"] == pytest.approx(1.0)


    def test_min_volume_enforced(self):
        pair = MAJOR_PAIRS["EURUSD"]
        result = lot_size_from_risk(
            account_equity=100.0,
            risk_percent=0.1,
            entry_price=1.1000,
            stop_loss_price=1.0900,   # 100 pips
            pair=pair,
            account_currency="USD",
            volume_step=0.01,
            min_volume=0.01,
        )
        # Raw ≈ 0.001; clamped to min_volume=0.01
        assert result["lot_size"] >= 0.01

    def test_max_volume_enforced(self):
        pair = MAJOR_PAIRS["EURUSD"]
        result = lot_size_from_risk(
            account_equity=10_000_000.0,
            risk_percent=1.0,
            entry_price=1.1000,
            stop_loss_price=1.0999,
            pair=pair,
            account_currency="USD",
            volume_step=0.01,
            max_volume=100.0,
        )
        assert result["lot_size"] <= 100.0

    def test_invalid_equity_raises(self):
        pair = MAJOR_PAIRS["EURUSD"]
        with pytest.raises(ValueError, match="account_equity must be positive"):
            lot_size_from_risk(0.0, 1.0, 1.1, 1.09, pair)

    def test_zero_stop_raises(self):
        pair = MAJOR_PAIRS["EURUSD"]
        with pytest.raises(ValueError, match="must differ"):
            lot_size_from_risk(1000.0, 1.0, 1.1000, 1.1000, pair)

    def test_unknown_pair_string_raises(self):
        with pytest.raises(ValueError, match="Unknown pair"):
            lot_size_from_risk(1000.0, 1.0, 1.1, 1.09, "FOOBAR")

    def test_result_dict_keys(self):
        pair = MAJOR_PAIRS["GBPUSD"]
        result = lot_size_from_risk(1000.0, 1.0, 1.2700, 1.2650, pair)
        for key in ("risk_amount", "stop_distance_pips", "pip_value_per_lot",
                    "raw_lot_size", "lot_size", "actual_risk"):
            assert key in result


# ---------------------------------------------------------------------------
# risk_reward_ratio
# ---------------------------------------------------------------------------

class TestRiskRewardRatio:
    def test_long_2_to_1(self):
        rr = risk_reward_ratio(entry=1.1000, stop_loss=1.0950, take_profit=1.1100)
        assert rr == pytest.approx(2.0)

    def test_short_2_to_1(self):
        rr = risk_reward_ratio(entry=1.1000, stop_loss=1.1050, take_profit=1.0900)
        assert rr == pytest.approx(2.0)

    def test_long_invalid_tp_below_entry_raises(self):
        with pytest.raises(ValueError, match="TP must be above entry"):
            risk_reward_ratio(1.1000, 1.0950, 1.0980)

    def test_short_invalid_tp_above_entry_raises(self):
        with pytest.raises(ValueError, match="TP must be below entry"):
            risk_reward_ratio(1.1000, 1.1050, 1.1020)

    def test_zero_stop_raises(self):
        with pytest.raises(ValueError, match="Stop loss must differ"):
            risk_reward_ratio(1.1000, 1.1000, 1.1100)

    def test_usdjpy_short_3_to_1(self):
        # SHORT USDJPY: entry=145.00, SL=145.50 (+50 pips), TP=143.50 (-150 pips)
        rr = risk_reward_ratio(145.00, 145.50, 143.50)
        assert rr == pytest.approx(3.0)
