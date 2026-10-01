"""Immutable deterministic inputs for Forex risk and position sizing."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.forex.conversion import AvailabilityStatus, ForexConversionRate
from tradingagents.forex.domain import normalize_forex_pair
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    OpenPosition,
)


class DeterministicContextRecord(BaseModel):
    """Strict immutable base for risk-context records."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ForexMarketContext(DeterministicContextRecord):
    """Observed market values; ``None`` means unavailable, while zero remains zero."""

    quote_status: AvailabilityStatus
    bid: float | None = Field(default=None, gt=0.0)
    ask: float | None = Field(default=None, gt=0.0)
    spread_pips: float | None = Field(default=None, ge=0.0)
    atr_status: AvailabilityStatus
    atr_pips: float | None = Field(default=None, gt=0.0)
    source: str
    observed_at: datetime

    @field_validator("observed_at", mode="before")
    @classmethod
    def observed_at_is_utc(cls, value: Any) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def quote_is_consistent(self):
        if self.quote_status == AvailabilityStatus.AVAILABLE:
            if self.bid is None or self.ask is None or self.spread_pips is None:
                raise ValueError("available quote requires bid, ask, and spread_pips")
        elif self.bid is not None or self.ask is not None or self.spread_pips is not None:
            raise ValueError("unavailable or not-applicable quote cannot carry quote values")
        if self.ask is not None and self.bid is not None and self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        if self.atr_status == AvailabilityStatus.AVAILABLE and self.atr_pips is None:
            raise ValueError("available ATR requires atr_pips")
        if self.atr_status != AvailabilityStatus.AVAILABLE and self.atr_pips is not None:
            raise ValueError("unavailable or not-applicable ATR cannot carry atr_pips")
        return self


class ForexPortfolioContext(DeterministicContextRecord):
    """Point-in-time portfolio snapshot used by deterministic controls."""

    open_positions: tuple[OpenPosition, ...] = ()
    daily_pnl_status: AvailabilityStatus = AvailabilityStatus.NOT_APPLICABLE
    realized_pnl_today: float | None = None
    day_start_balance: float | None = Field(default=None, gt=0.0)
    daily_pnl_source: str | None = None
    trading_day_start_utc: datetime | None = None

    @field_validator("trading_day_start_utc", mode="before")
    @classmethod
    def day_start_is_utc(cls, value: Any) -> datetime | None:
        return None if value is None else _require_utc(value)

    @model_validator(mode="after")
    def daily_pnl_is_consistent(self):
        values = (
            self.realized_pnl_today,
            self.day_start_balance,
            self.daily_pnl_source,
            self.trading_day_start_utc,
        )
        if self.daily_pnl_status == AvailabilityStatus.AVAILABLE:
            if any(value is None for value in values):
                raise ValueError("available daily P&L requires value, day-start balance, source, and UTC boundary")
        elif any(value is not None for value in values):
            raise ValueError("unavailable or not-applicable daily P&L cannot carry values")
        return self

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)


class ForexRiskContext(DeterministicContextRecord):
    """Complete deterministic snapshot consumed by Forex risk and sizing."""

    pair: str
    as_of_utc: datetime
    market: ForexMarketContext
    account: ForexAccountProfile
    broker: BrokerExecutionConstraints
    portfolio: ForexPortfolioContext = Field(default_factory=ForexPortfolioContext)
    conversions: tuple[ForexConversionRate, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def detach_mutable_inputs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        detached = copy.deepcopy(value)
        for name in ("account", "broker", "portfolio"):
            item = detached.get(name)
            if isinstance(item, BaseModel):
                detached[name] = item.model_dump(mode="python")
        detached["conversions"] = tuple(detached.get("conversions") or ())
        return detached

    @field_validator("pair")
    @classmethod
    def normalize_pair(cls, value: str) -> str:
        return normalize_forex_pair(value)

    @field_validator("as_of_utc", mode="before")
    @classmethod
    def as_of_is_utc(cls, value: Any) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def observations_are_point_in_time(self):
        if self.market.observed_at > self.as_of_utc:
            raise ValueError("market observation cannot be later than as_of_utc")
        for conversion in self.conversions:
            if conversion.observed_at is not None and conversion.observed_at > self.as_of_utc:
                raise ValueError("conversion observation cannot be later than as_of_utc")
        return self

    def quote_to_account_conversion(self) -> ForexConversionRate | None:
        quote = self.pair[3:6]
        account_currency = self.account.currency.upper()
        if quote == account_currency:
            return None
        return next(
            (
                item
                for item in self.conversions
                if item.from_currency == quote and item.to_currency == account_currency
            ),
            None,
        )

    def quote_to_account_rate(self) -> float | None:
        if self.pair[3:6] == self.account.currency.upper():
            return 1.0
        conversion = self.quote_to_account_conversion()
        if conversion is None or conversion.status != AvailabilityStatus.AVAILABLE:
            return None
        return conversion.rate

    def sizing_quote_price(self) -> float | None:
        """Return the legacy quote-per-account price expected by current sizing code."""
        quote = self.pair[3:6]
        account_currency = self.account.currency.upper()
        if quote == account_currency:
            return 1.0
        direct = next(
            (
                item
                for item in self.conversions
                if item.from_currency == account_currency
                and item.to_currency == quote
                and item.status == AvailabilityStatus.AVAILABLE
            ),
            None,
        )
        if direct is not None:
            return direct.rate
        inverse = self.quote_to_account_rate()
        return (1.0 / inverse) if inverse is not None else None


def _require_utc(value: Any) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("an explicit UTC offset is required")
    return stamp.astimezone(timezone.utc)
