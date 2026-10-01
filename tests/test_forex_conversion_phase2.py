from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.forex.conversion import (
    AvailabilityStatus,
    ForexConversionRate,
    FXConversionUnavailable,
)
from tradingagents.forex.pips import lot_size_from_risk, pip_value_in_account_currency
from tradingagents.mt5.models import MT5Position
from tradingagents.risk.sizing import calculate_required_margin

NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def rate(source: str, target: str, value: float, symbol: str) -> ForexConversionRate:
    return ForexConversionRate(
        from_currency=source,
        to_currency=target,
        status=AvailabilityStatus.AVAILABLE,
        rate=value,
        conversion_path=(symbol,),
        source="historical-test-feed",
        observed_at=NOW,
    )


@pytest.mark.parametrize(
    ("pair", "price", "conversions", "expected"),
    [
        ("EURUSD", 1.10, (), 10.0),
        ("GBPUSD", 1.25, (), 10.0),
        ("USDJPY", 150.0, (), 1000.0 / 150.0),
        ("USDCHF", 0.90, (), 10.0 / 0.90),
        ("EURJPY", 160.0, (rate("USD", "JPY", 150.0, "USDJPY"),), 1000.0 / 150.0),
        ("GBPJPY", 190.0, (rate("JPY", "USD", 1 / 150.0, "USDJPY inverse"),), 1000.0 / 150.0),
        ("EURGBP", 0.86, (rate("GBP", "USD", 1.25, "GBPUSD"),), 12.5),
    ],
)
def test_usd_account_pip_value_matrix(pair, price, conversions, expected):
    assert pip_value_in_account_currency(
        pair, 1.0, "USD", price, conversions, NOW
    ) == pytest.approx(expected)


def test_non_usd_account_and_opposite_direction_conversion():
    eurusd = rate("EUR", "USD", 1.10, "EURUSD")
    assert pip_value_in_account_currency(
        "GBPUSD", 1.0, "EUR", 1.25, (eurusd,), NOW
    ) == pytest.approx(10.0 / 1.10)


def test_missing_and_stale_conversion_fail_closed():
    with pytest.raises(FXConversionUnavailable):
        pip_value_in_account_currency("EURJPY", 1.0, "USD", 160.0)

    stale = rate("USD", "JPY", 150.0, "USDJPY").model_copy(
        update={"observed_at": NOW - timedelta(minutes=6)}
    )
    with pytest.raises(FXConversionUnavailable):
        pip_value_in_account_currency(
            "EURJPY", 1.0, "USD", 160.0, (stale,), NOW, timedelta(minutes=5)
        )


def test_lot_size_and_cross_margin_use_distinct_conversions():
    usd_jpy = rate("USD", "JPY", 150.0, "USDJPY")
    sized = lot_size_from_risk(
        account_equity=10_000,
        risk_percent=1.0,
        entry_price=160.0,
        stop_loss_price=159.80,
        pair="EURJPY",
        conversions=(usd_jpy,),
        as_of_utc=NOW,
    )
    assert sized["pip_value_per_lot"] == pytest.approx(6.6667)
    assert sized["lot_size"] == pytest.approx(0.75)

    eur_usd = rate("EUR", "USD", 1.10, "EURUSD")
    assert calculate_required_margin(
        "EURJPY", 1.0, 160.0, 100.0, "USD",
        conversions=(eur_usd,), as_of_utc=NOW,
    ) == pytest.approx(1100.0)


def test_usdjpy_numeric_acceptance_case_at_150():
    """$100 risk / ($6.666... per pip * 20 pips) = 0.75 lots."""
    sized = lot_size_from_risk(
        account_equity=10_000,
        risk_percent=1.0,
        entry_price=150.0,
        stop_loss_price=149.80,
        pair="USDJPY",
        account_currency="USD",
        current_quote_price=150.0,
        as_of_utc=NOW,
    )

    assert sized["pip_value_per_lot"] == pytest.approx(6.6667)
    assert sized["lot_size"] == pytest.approx(0.75)
    assert sized["pip_value_per_lot"] < 10.0


def test_cross_margin_does_not_treat_pair_price_as_conversion():
    with pytest.raises(FXConversionUnavailable):
        calculate_required_margin(
            "EURGBP", 1.0, 0.86, 100.0, "USD", current_quote_price=0.86
        )


def test_mt5_no_stop_has_unbounded_risk_and_stopped_usdjpy_is_converted():
    position = MT5Position(
        ticket=1,
        time=NOW,
        type=ForexAction.LONG,
        symbol="USDJPY",
        volume=1.0,
        price_open=150.0,
        price_current=150.0,
        sl=0.0,
    )
    unbounded = position.to_open_position(account_currency="USD", as_of_utc=NOW)
    assert unbounded.stop_loss is None
    assert unbounded.risk_amount is None

    bounded = position.model_copy(update={"sl": 149.0}).to_open_position(
        account_currency="USD", as_of_utc=NOW
    )
    assert bounded.risk_amount == pytest.approx(666.67)
