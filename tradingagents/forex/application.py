"""Framework-independent Forex account and deterministic risk-context assembly.

Both the browser worker and CLI use this module so account-source semantics and
fail-closed MT5 behavior cannot drift between presentation layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.mt5.errors import MT5Error
from tradingagents.risk.context import (
    AvailabilityStatus,
    ForexMarketContext,
    ForexPortfolioContext,
    ForexRiskContext,
)
from tradingagents.risk.sizing import ForexAccountProfile

AccountSource = Literal["mt5", "manual"]


@dataclass(frozen=True)
class ForexApplicationContext:
    """Presentation-neutral account context supplied to the Forex graph."""

    account_source: AccountSource
    account: ForexAccountProfile
    risk_context: ForexRiskContext | None
    broker_verified: bool
    assumptions: tuple[str, ...] = ()


def build_manual_application_context(
    *,
    balance: float,
    equity: float,
    free_margin: float,
    leverage: float,
    currency: str,
    max_open_positions: int = 5,
    max_account_risk_percent: float = 6.0,
    max_currency_exposure_percent: float = 5.0,
    max_daily_loss_percent: float | None = None,
    max_daily_loss_amount: float | None = None,
) -> ForexApplicationContext:
    """Build a complete, explicitly non-broker-verified manual account."""

    account = ForexAccountProfile(
        balance=balance,
        equity=equity,
        free_margin=free_margin,
        used_margin=max(0.0, equity - free_margin),
        currency=currency.strip().upper(),
        leverage=leverage,
        max_open_positions=max_open_positions,
        max_account_risk_percent=max_account_risk_percent,
        max_currency_exposure_percent=max_currency_exposure_percent,
        max_daily_loss_percent=max_daily_loss_percent,
        max_daily_loss_amount=max_daily_loss_amount,
    )
    return ForexApplicationContext(
        account_source="manual",
        account=account,
        risk_context=None,
        broker_verified=False,
        assumptions=(
            "Caller-supplied account values",
            "Broker constraints and live portfolio state are not verified",
        ),
    )


def build_mt5_application_context(
    observer,
    *,
    pair: str,
    execution_timeframe: str,
    max_open_positions: int = 5,
    max_account_risk_percent: float = 6.0,
    max_currency_exposure_percent: float = 5.0,
    max_daily_loss_percent: float | None = None,
    max_daily_loss_amount: float | None = None,
    quote_max_age_seconds: float | None = None,
) -> ForexApplicationContext:
    """Build the authoritative live MT5 context or fail closed."""

    connection = getattr(observer, "connection", None)
    try:
        connected = bool(
            connection
            and hasattr(connection, "is_connected")
            and connection.is_connected()
        )
    except Exception as exc:
        raise DataInsufficientError("MT5 connection status is unavailable") from exc
    if not connected:
        raise DataInsufficientError("MT5 account is not connected")

    try:
        account = observer.to_sizing_account_profile().model_copy(
            update={
                "max_open_positions": max_open_positions,
                "max_account_risk_percent": max_account_risk_percent,
                "max_currency_exposure_percent": max_currency_exposure_percent,
                "max_daily_loss_percent": max_daily_loss_percent,
                "max_daily_loss_amount": max_daily_loss_amount,
            }
        )
        tick = observer.get_current_tick(pair)
        constraints = observer.to_broker_constraints(pair)
        atr_pips = observer.get_atr_pips(pair, execution_timeframe, as_of=tick.time)
        conversions = observer.get_conversion_observations(pair, account.currency)
        pending_orders = observer.get_pending_orders()
        as_of_utc = datetime.now(timezone.utc)
        max_conversion_age = timedelta(
            seconds=float(
                quote_max_age_seconds
                if quote_max_age_seconds is not None
                else get_config().get("forex_quote_max_age_seconds", 30)
            )
        )
        positions = observer.to_open_positions(
            account_currency=account.currency,
            conversions=conversions,
            as_of_utc=as_of_utc,
            max_conversion_age=max_conversion_age,
        )
        pending_exposures = (
            observer.to_pending_exposures(
                account_currency=account.currency,
                conversions=conversions,
                as_of_utc=as_of_utc,
                max_conversion_age=max_conversion_age,
            )
            if hasattr(observer, "to_pending_exposures")
            else []
        )

        daily_rule_enabled = (
            account.max_daily_loss_percent is not None
            or account.max_daily_loss_amount is not None
        )
        daily_pnl_status = AvailabilityStatus.NOT_APPLICABLE
        daily_pnl = None
        day_start_balance = None
        daily_pnl_source = None
        trading_day_start = None
        if daily_rule_enabled:
            try:
                daily_pnl, trading_day_start = observer.get_daily_realized_pnl(as_of_utc)
                day_start_balance = account.balance - daily_pnl
                daily_pnl_source = "MT5 deal history"
                daily_pnl_status = AvailabilityStatus.AVAILABLE
            except MT5Error:
                daily_pnl_status = AvailabilityStatus.UNAVAILABLE

        risk_context = ForexRiskContext(
            pair=pair,
            as_of_utc=as_of_utc,
            market=ForexMarketContext(
                quote_status=AvailabilityStatus.AVAILABLE,
                bid=tick.bid,
                ask=tick.ask,
                spread_pips=tick.spread_pips,
                atr_status=AvailabilityStatus.AVAILABLE,
                atr_pips=atr_pips,
                source=f"{tick.source}:{tick.broker_symbol or pair}",
                observed_at=tick.time,
            ),
            account=account,
            broker=constraints,
            portfolio=ForexPortfolioContext(
                open_positions=tuple(positions),
                pending_orders_status=AvailabilityStatus.AVAILABLE,
                pending_order_count=len(pending_orders),
                pending_exposures=tuple(pending_exposures),
                daily_pnl_status=daily_pnl_status,
                realized_pnl_today=daily_pnl,
                day_start_balance=day_start_balance,
                daily_pnl_source=daily_pnl_source,
                trading_day_start_utc=trading_day_start,
            ),
            conversions=conversions,
        )
    except DataInsufficientError:
        raise
    except Exception as exc:
        raise DataInsufficientError("complete MT5 risk context is unavailable") from exc

    return ForexApplicationContext(
        account_source="mt5",
        account=account,
        risk_context=risk_context,
        broker_verified=True,
    )
