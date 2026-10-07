"""MetaTrader 5 Pydantic Domain Models (Phase 14).

Provides strongly-typed, immutable representation of MT5 accounts, symbols,
market ticks, positions, orders, and deals.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.forex.conversion import ForexConversionRate, FXConversionUnavailable
from tradingagents.forex.domain import normalize_forex_pair
from tradingagents.forex.pips import pip_size_for, pip_value_in_account_currency
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    OpenPosition,
    PendingExposure,
)


class MT5ConnectionStatus(str, Enum):
    """Lifecycle status of the MetaTrader 5 terminal connection."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class MT5AccountInfo(BaseModel):
    """Institutional snapshot of a live MetaTrader 5 account."""

    login: int = Field(description="Account login ID")
    name: str = Field(default="", description="Account holder name")
    server: str = Field(default="", description="Broker access server")
    currency: str = Field(default="USD", description="Deposit currency (e.g. USD, EUR, GBP)")
    leverage: int = Field(default=100, description="Account leverage ratio (e.g. 100 for 1:100)")
    balance: float = Field(default=0.0, description="Account cash balance")
    equity: float = Field(default=0.0, description="Account floating equity (balance + unrealized PnL)")
    profit: float = Field(default=0.0, description="Total floating profit/loss across open positions")
    margin: float = Field(default=0.0, description="Committed margin for open positions")
    margin_free: float = Field(default=0.0, description="Available free margin for new positions")
    margin_level: float = Field(default=0.0, description="Margin level percentage (equity / margin * 100)")
    margin_so_call: float = Field(default=100.0, description="Margin call threshold (%)")
    margin_so_so: float = Field(default=50.0, description="Stop out liquidation threshold (%)")
    trade_mode: str = Field(default="DEMO", description="Account trading mode: DEMO, CONTEST, REAL")
    trade_allowed: bool = Field(default=True, description="Whether trading is permitted on this account")

    def to_forex_account_profile(self) -> ForexAccountProfile:
        """Convert MT5 account snapshot to a ForexAccountProfile for risk & position sizing."""
        return ForexAccountProfile(
            balance=self.balance,
            equity=self.equity,
            free_margin=self.margin_free,
            used_margin=self.margin,
            currency=self.currency,
            leverage=float(self.leverage),
            margin_call_level=self.margin_so_call,
            stop_out_level=self.margin_so_so,
        )


class MT5SymbolInfo(BaseModel):
    """Detailed broker specification and live quotes for a Forex symbol."""

    name: str = Field(description="Broker-native symbol name (e.g. EURUSD, EURUSDm)")
    canonical_symbol: str = Field(default="", description="Standard 6-letter ISO pair (e.g. EURUSD)")
    path: str = Field(default="", description="Symbol path in market tree")
    digits: int = Field(default=5, description="Number of decimal digits")
    point: float = Field(default=0.00001, description="Point size (smallest quote unit)")
    pip_size: float = Field(default=0.0001, description="Pip size (typically 10 points)")
    spread_points: int = Field(default=10, description="Current broker spread in points")
    spread_pips: float = Field(default=1.0, description="Current broker spread in pips")
    bid: float = Field(default=0.0, description="Current market bid price")
    ask: float = Field(default=0.0, description="Current market ask price")
    last: float = Field(default=0.0, description="Last traded deal price")
    volume_min: float = Field(default=0.01, description="Minimum order volume in lots")
    volume_max: float = Field(default=100.0, description="Maximum order volume in lots")
    volume_step: float = Field(default=0.01, description="Volume increment step in lots")
    contract_size: float = Field(default=100000.0, description="Trade contract size (units per 1.0 lot)")
    currency_base: str = Field(default="EUR", description="Base currency")
    currency_profit: str = Field(default="USD", description="Profit currency")
    currency_margin: str = Field(default="USD", description="Margin currency")
    trade_mode: int = Field(default=4, description="Trade mode (0: disabled, 4: full)")

    def model_post_init(self, __context: Any) -> None:
        if not self.canonical_symbol:
            object.__setattr__(self, "canonical_symbol", normalize_forex_pair(self.name))
        if self.pip_size <= 0.0:
            object.__setattr__(self, "pip_size", pip_size_for(self.canonical_symbol))

    def to_broker_constraints(self) -> BrokerExecutionConstraints:
        """Convert MT5 symbol specification to BrokerExecutionConstraints."""
        return BrokerExecutionConstraints(
            broker_symbol=self.name,
            digits=self.digits,
            point=self.point,
            pip_size=self.pip_size,
            min_volume=self.volume_min,
            max_volume=self.volume_max,
            volume_step=self.volume_step,
            contract_size=self.contract_size,
        )


class MT5Tick(BaseModel):
    """Real-time market tick quote."""

    source: str = "MT5"
    broker_symbol: str | None = None
    broker: str | None = None
    retrieved_at_utc: datetime | None = None
    time: datetime = Field(description="Tick timestamp (UTC)")
    bid: float = Field(description="Bid price")
    ask: float = Field(description="Ask price")
    last: float = Field(default=0.0, description="Last trade price")
    volume: float = Field(default=0.0, description="Tick volume")
    spread_points: int = Field(default=0, description="Spread in points")
    spread_pips: float = Field(default=0.0, description="Spread in pips")
    flags: int = Field(default=0, description="Tick flags bitmask")


class MT5Position(BaseModel):
    """Live open position currently managed in the MT5 terminal."""

    ticket: int = Field(description="Unique position ticket ID")
    identifier: int = Field(default=0, description="Stable broker position identifier used by history deals")
    time: datetime = Field(description="Position opening timestamp (UTC)")
    type: ForexAction = Field(description="Position direction (LONG / SHORT)")
    magic: int = Field(default=0, description="Expert Advisor magic number")
    symbol: str = Field(description="Broker symbol name (e.g. EURUSD, GBPJPYm)")
    volume: float = Field(description="Volume in standard lots")
    price_open: float = Field(description="Execution entry price")
    sl: float = Field(default=0.0, description="Stop-loss price level")
    tp: float = Field(default=0.0, description="Take-profit price level")
    price_current: float = Field(default=0.0, description="Current market price")
    swap: float = Field(default=0.0, description="Accumulated overnight financing swap")
    profit: float = Field(default=0.0, description="Current floating profit in deposit currency")
    comment: str = Field(default="", description="Position comment / tag")

    @property
    def unrealized_pips(self) -> float:
        """Calculate live floating profit/loss in pips."""
        if self.price_open <= 0 or self.price_current <= 0:
            return 0.0
        pip_sz = pip_size_for(self.symbol)
        if self.type == ForexAction.LONG:
            diff = self.price_current - self.price_open
        else:
            diff = self.price_open - self.price_current
        return round(diff / pip_sz, 1)

    def to_open_position(
        self,
        account_currency: str = "USD",
        conversions: Sequence[ForexConversionRate] = (),
        as_of_utc: datetime | None = None,
        max_conversion_age: timedelta | None = None,
        contract_size: float | None = None,
    ) -> OpenPosition:
        """Convert MT5 position to an OpenPosition for portfolio correlation & risk aggregation."""
        normalized_pair = normalize_forex_pair(self.symbol)
        stop_loss = self.sl if self.sl > 0 else None
        est_risk: float | None = None
        if stop_loss is not None:
            pip_sz = pip_size_for(normalized_pair)
            stop_pips = abs(self.price_open - stop_loss) / pip_sz
            try:
                pip_value = pip_value_in_account_currency(
                    normalized_pair,
                    self.volume,
                    account_currency,
                    current_quote_price=self.price_current or self.price_open,
                    conversions=conversions,
                    as_of_utc=as_of_utc,
                    max_conversion_age=max_conversion_age,
                    contract_size=contract_size,
                )
                est_risk = round(stop_pips * pip_value, 2)
            except FXConversionUnavailable:
                est_risk = None

        return OpenPosition(
            position_id=str(self.ticket),
            pair=normalized_pair,
            action=self.type,
            lots=self.volume,
            entry_price=self.price_open,
            stop_loss=stop_loss,
            risk_amount=est_risk,
        )


class MT5Order(BaseModel):
    """Pending limit/stop order currently active in the MT5 terminal."""

    ticket: int = Field(description="Order ticket ID")
    time_setup: datetime = Field(description="Order placement timestamp (UTC)")
    type: str = Field(description="Order type (e.g. ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_STOP)")
    state: str = Field(default="ORDER_STATE_PLACED", description="Order state")
    magic: int = Field(default=0, description="Magic number")
    symbol: str = Field(description="Symbol name")
    volume_initial: float = Field(description="Initial requested volume")
    volume_current: float = Field(description="Remaining unfulfilled volume")
    price_open: float = Field(description="Order trigger price")
    sl: float = Field(default=0.0, description="Stop-loss price")
    tp: float = Field(default=0.0, description="Take-profit price")
    comment: str = Field(default="", description="Order comment")

    def to_pending_exposure(
        self,
        account_currency: str = "USD",
        conversions: Sequence[ForexConversionRate] = (),
        as_of_utc: datetime | None = None,
        max_conversion_age: timedelta | None = None,
        contract_size: float | None = None,
    ) -> PendingExposure:
        """Convert MT5Order to PendingExposure for portfolio risk controls (PORT-010, PORT-011)."""
        normalized_pair = normalize_forex_pair(self.symbol)
        stop_loss = self.sl if self.sl > 0 else None
        action = ForexAction.LONG if "BUY" in self.type.upper() else ForexAction.SHORT
        est_risk: float | None = None
        if stop_loss is not None:
            pip_sz = pip_size_for(normalized_pair)
            stop_pips = abs(self.price_open - stop_loss) / pip_sz
            try:
                pip_value = pip_value_in_account_currency(
                    normalized_pair,
                    self.volume_current,
                    account_currency,
                    current_quote_price=self.price_open,
                    conversions=conversions,
                    as_of_utc=as_of_utc,
                    max_conversion_age=max_conversion_age,
                    contract_size=contract_size,
                )
                est_risk = round(stop_pips * pip_value, 2)
            except FXConversionUnavailable:
                est_risk = None

        return PendingExposure(
            order_id=str(self.ticket),
            pair=normalized_pair,
            action=action,
            order_type=self.type,
            lots=self.volume_current,
            trigger_price=self.price_open,
            stop_loss=stop_loss,
            reserved_risk=est_risk,
            setup_time=self.time_setup,
        )


class MT5Deal(BaseModel):
    """Executed trade transaction in the MT5 account history."""

    ticket: int = Field(description="Deal ticket ID")
    order: int = Field(description="Associated order ticket ID")
    position_id: int = Field(default=0, description="Position ID this deal affected")
    time: datetime = Field(description="Deal execution timestamp (UTC)")
    type: str = Field(description="Deal type (e.g. DEAL_TYPE_BUY, DEAL_TYPE_SELL)")
    entry: str = Field(default="IN", description="Deal entry type: IN, OUT, INOUT, OUT_BY")
    magic: int = Field(default=0, description="Magic number")
    symbol: str = Field(description="Symbol name")
    volume: float = Field(description="Executed volume in lots")
    price: float = Field(description="Executed deal price")
    commission: float = Field(default=0.0, description="Charged commission")
    swap: float = Field(default=0.0, description="Swap booked on closing")
    profit: float = Field(default=0.0, description="Realized gross profit/loss")
    fee: float = Field(default=0.0, description="Signed broker execution fee")
    reason: int = Field(default=0, description="Broker deal reason, including SL and TP")
    comment: str = Field(default="", description="Deal comment")
