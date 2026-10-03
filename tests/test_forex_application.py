"""Shared web/CLI Forex application-context contract."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex.application import (
    build_manual_application_context,
    build_mt5_application_context,
)
from tradingagents.mt5.errors import MT5Error
from tradingagents.risk.sizing import BrokerExecutionConstraints, ForexAccountProfile


def test_manual_context_requires_complete_values_and_preserves_zero_margin():
    context = build_manual_application_context(
        balance=10_000,
        equity=9_500,
        free_margin=0,
        leverage=50,
        currency="usd",
    )

    assert context.account_source == "manual"
    assert context.broker_verified is False
    assert context.risk_context is None
    assert context.account.free_margin == 0
    assert context.account.used_margin == 9_500
    assert context.account.currency == "USD"
    assert context.assumptions


def test_mt5_context_fails_closed_when_connection_is_unknown():
    observer = MagicMock()
    observer.connection.is_connected.return_value = False

    with pytest.raises(DataInsufficientError, match="not connected"):
        build_mt5_application_context(
            observer,
            pair="EURUSD",
            execution_timeframe="M15",
        )

    observer.to_sizing_account_profile.assert_not_called()


def test_mt5_context_contains_authoritative_market_account_and_portfolio():
    observed_at = datetime.now(timezone.utc)
    observer = MagicMock()
    observer.connection.is_connected.return_value = True
    observer.to_sizing_account_profile.return_value = ForexAccountProfile(
        balance=25_000,
        equity=24_500,
        free_margin=20_000,
        currency="USD",
        leverage=100,
    )
    observer.get_current_tick.return_value = SimpleNamespace(
        bid=1.1,
        ask=1.1002,
        spread_pips=2.0,
        source="MT5",
        broker_symbol="EURUSDm",
        time=observed_at,
    )
    observer.to_broker_constraints.return_value = BrokerExecutionConstraints(
        broker_symbol="EURUSDm",
        digits=5,
        point=0.00001,
        pip_size=0.0001,
    )
    observer.get_atr_pips.return_value = 18.0
    observer.get_conversion_observations.return_value = ()
    observer.get_pending_orders.return_value = []
    observer.to_open_positions.return_value = []

    context = build_mt5_application_context(
        observer,
        pair="EURUSD",
        execution_timeframe="M15",
    )

    assert context.account_source == "mt5"
    assert context.broker_verified is True
    assert context.risk_context is not None
    assert context.risk_context.account.balance == 25_000
    assert context.risk_context.market.bid == 1.1
    assert context.risk_context.market.ask == 1.1002
    assert context.risk_context.market.atr_pips == 18.0
    assert context.risk_context.broker.broker_symbol == "EURUSDm"
    assert context.risk_context.portfolio.pending_orders_status.value == "AVAILABLE"
    assert context.risk_context.portfolio.pending_order_count == 0
    observer.get_atr_pips.assert_called_once_with("EURUSD", "M15", as_of=observed_at)


@pytest.mark.parametrize(
    ("failing_method", "message"),
    [
        ("to_open_positions", "positions unavailable"),
        ("get_pending_orders", "pending orders unavailable"),
    ],
)
def test_mt5_context_fails_closed_when_exposure_is_unavailable(
    failing_method, message
):
    observed_at = datetime.now(timezone.utc)
    observer = MagicMock()
    observer.connection.is_connected.return_value = True
    observer.to_sizing_account_profile.return_value = ForexAccountProfile(
        balance=25_000,
        equity=24_500,
        free_margin=20_000,
        currency="USD",
        leverage=100,
    )
    observer.get_current_tick.return_value = SimpleNamespace(
        bid=1.1,
        ask=1.1002,
        spread_pips=2.0,
        source="MT5",
        broker_symbol="EURUSDm",
        time=observed_at,
    )
    observer.to_broker_constraints.return_value = BrokerExecutionConstraints(
        broker_symbol="EURUSDm",
        digits=5,
        point=0.00001,
        pip_size=0.0001,
    )
    observer.get_atr_pips.return_value = 18.0
    observer.get_conversion_observations.return_value = ()
    observer.get_pending_orders.return_value = []
    observer.to_open_positions.return_value = []
    getattr(observer, failing_method).side_effect = MT5Error(message, code=-10005)

    with pytest.raises(DataInsufficientError, match="complete MT5 risk context"):
        build_mt5_application_context(
            observer,
            pair="EURUSD",
            execution_timeframe="M15",
        )
