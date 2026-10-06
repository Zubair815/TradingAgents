"""MetaTrader 5 Read-Only Observer Adapter (Phase 14).

Provides strictly read-only inspection of MT5 accounts, symbols, market ticks,
multi-timeframe candles, open positions, pending orders, and execution deals.
Never places, modifies, or cancels trading orders.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.forex_data import (
    ForexBar,
    MultiTimeframeData,
    bars_to_frame,
    validate_forex_candles,
)
from tradingagents.dataflows.forex_quality import (
    DataInsufficientError,
    utc_timestamp,
    validate_quote,
)
from tradingagents.forex.conversion import AvailabilityStatus, ForexConversionRate
from tradingagents.forex.domain import Timeframe, normalize_forex_pair
from tradingagents.forex.indicators import calculate_atr_pips
from tradingagents.forex.symbols import canonical_to_broker
from tradingagents.mt5.connection import MT5ConnectionManager
from tradingagents.mt5.errors import (
    MT5DataError,
    MT5SymbolError,
)
from tradingagents.mt5.models import (
    MT5AccountInfo,
    MT5Deal,
    MT5Order,
    MT5Position,
    MT5SymbolInfo,
    MT5Tick,
)
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    OpenPosition,
    PendingExposure,
)

logger = logging.getLogger(__name__)
_MT5_API_LOCK = threading.RLock()


def _serialized_mt5(method):
    """Serialize native MT5 IPC; the vendor module is process-global and not thread-safe."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with _MT5_API_LOCK:
            return method(self, *args, **kwargs)
    return wrapped


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Safely extract field from an object (supports dicts, objects with attrs, and namedtuples)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_MISSING = object()

# Canonical instruments whose common MT5 broker name is not a simple suffix
# variant. Explicit ``forex_broker_symbols`` configuration still takes
# precedence over these discovery fallbacks.
_COMMON_BROKER_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("GOLD",),
}

_XM_SERVER_TIMEZONE = ZoneInfo("Europe/Athens")


def _broker_timezone(server: str | None) -> ZoneInfo | None:
    """Return the wall-clock timezone for brokers that expose local epochs."""
    return _XM_SERVER_TIMEZONE if str(server or "").upper().startswith("XM") else None


def _utc_to_broker_query_time(value: datetime, server: str | None) -> datetime:
    """Translate a UTC boundary to the broker wall-clock epoch expected by MT5."""
    broker_tz = _broker_timezone(server)
    utc_value = value.astimezone(timezone.utc)
    if broker_tz is None:
        return utc_value
    broker_wall = utc_value.astimezone(broker_tz)
    return broker_wall.replace(tzinfo=timezone.utc)


def _parse_broker_timestamp(value: Any, server: str | None) -> datetime:
    """Normalize a broker wall-clock epoch into a truthful UTC timestamp."""
    raw = _parse_timestamp(value)
    broker_tz = _broker_timezone(server)
    if broker_tz is None:
        return raw
    return raw.replace(tzinfo=None).replace(tzinfo=broker_tz).astimezone(timezone.utc)


def _required_field(obj: Any, key: str) -> Any:
    """Read a broker field without substituting a plausible financial default."""
    value = _get_field(obj, key, _MISSING)
    if value is _MISSING or value is None:
        raise DataInsufficientError(f"required MT5 field unavailable: {key}")
    return value


def _required_number(obj: Any, key: str, *, positive: bool = False) -> float:
    try:
        value = float(_required_field(obj, key))
    except (TypeError, ValueError) as exc:
        raise DataInsufficientError(f"invalid MT5 numeric field: {key}") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise DataInsufficientError(f"invalid MT5 numeric field: {key}")
    return value


def _extract_rate_val(r: Any, key: str, index: int, default: Any = 0.0) -> Any:
    """Safely extract field from an MT5 rates record (numpy record, dict, tuple, or object)."""
    if isinstance(r, dict):
        return r.get(key, default)
    if hasattr(r, key):
        return getattr(r, key)
    try:
        return r[key]
    except (TypeError, KeyError, IndexError):
        pass
    try:
        return r[index]
    except (TypeError, IndexError):
        pass
    return default


def _parse_timestamp(val: Any) -> datetime:
    """Safely parse datetime from timestamp or datetime object."""
    if isinstance(val, Real):
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    raise DataInsufficientError("invalid MT5 timestamp")


class MT5Observer:
    """Read-only inspection and market observation adapter for MetaTrader 5."""

    def __init__(self, connection: MT5ConnectionManager | None = None, *, auto_connect: bool = True):
        self.connection = connection or MT5ConnectionManager()
        self.auto_connect = auto_connect

    @property
    def api(self) -> Any:
        return self.connection.api

    def _ensure_connected(self) -> None:
        """Verify connection or attempt auto-connect."""
        if not self.connection.is_connected():
            if not self.auto_connect:
                from tradingagents.mt5.errors import MT5ConnectionError
                raise MT5ConnectionError("MT5 is disconnected; connect explicitly to resume observation")
            logger.info("MT5Observer: connection not active; connecting...")
            self.connection.connect()

    # -----------------------------------------------------------------------
    # 1. Account Inspection
    # -----------------------------------------------------------------------

    @_serialized_mt5
    def get_account_info(self) -> MT5AccountInfo:
        """Fetch current account balance, equity, margin, and trading permissions."""
        self._ensure_connected()
        mt5 = self.api
        raw_info = mt5.account_info()
        if raw_info is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 account info: {desc}", code=code)

        mode_map = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
        raw_trade_mode = _required_field(raw_info, "trade_mode")
        trade_mode = mode_map.get(raw_trade_mode, "DEMO") if isinstance(raw_trade_mode, int) else str(raw_trade_mode)

        return MT5AccountInfo(
            login=int(_required_number(raw_info, "login", positive=True)),
            name=str(_get_field(raw_info, "name", "")),
            server=str(_required_field(raw_info, "server")),
            currency=str(_required_field(raw_info, "currency")),
            leverage=int(_required_number(raw_info, "leverage", positive=True)),
            balance=_required_number(raw_info, "balance"),
            equity=_required_number(raw_info, "equity"),
            profit=_required_number(raw_info, "profit"),
            margin=_required_number(raw_info, "margin"),
            margin_free=_required_number(raw_info, "margin_free"),
            margin_level=_required_number(raw_info, "margin_level"),
            margin_so_call=_required_number(raw_info, "margin_so_call"),
            margin_so_so=_required_number(raw_info, "margin_so_so"),
            trade_mode=trade_mode,
            trade_allowed=bool(_required_field(raw_info, "trade_allowed")),
        )

    def to_sizing_account_profile(self) -> ForexAccountProfile:
        """Export live MT5 account profile directly to ForexAccountProfile."""
        return self.get_account_info().to_forex_account_profile()

    # -----------------------------------------------------------------------
    # 2. Symbol Specification & Quotes
    # -----------------------------------------------------------------------

    @_serialized_mt5
    def get_symbol_info(self, symbol: str) -> MT5SymbolInfo:
        """Fetch broker contract specifications, spread, and pricing for a symbol."""
        self._ensure_connected()
        mt5 = self.api

        canon = normalize_forex_pair(symbol)
        explicit_symbol = get_config().get("forex_broker_symbols", {}).get(canon) or (symbol if symbol != canon else None)
        if explicit_symbol:
            candidates = [explicit_symbol]
        else:
            candidates = [
                canonical_to_broker(canon),
                canon,
                symbol,
                *_COMMON_BROKER_SYMBOL_ALIASES.get(canon, ()),
            ]

        info = None
        broker_sym = candidates[0]
        for candidate in dict.fromkeys(candidates):
            mt5.symbol_select(candidate, True)
            candidate_info = mt5.symbol_info(candidate)
            if candidate_info is not None:
                broker_sym = candidate
                info = candidate_info
                break

        if info is None and explicit_symbol:
            raise MT5SymbolError(f"Configured broker symbol {broker_sym!r} is unavailable")

        if info is None:
            code, desc = self.connection._get_last_error()
            raise MT5SymbolError(f"Symbol '{symbol}' not found in MetaTrader 5: {desc}", code=code)

        try:
            digits = int(_get_field(info, "digits", None))
            point = float(_get_field(info, "point", None))
            if not 0 <= digits <= 10 or not 0 < point < 1:
                raise ValueError("invalid precision")
        except (TypeError, ValueError):
            raise DataInsufficientError("broker symbol precision unavailable") from None
        # MT5 quotes fractional pips for 3/5-digit FX contracts. For other
        # instruments (for example XM GOLD with 2 digits), one point is the
        # smallest meaningful price increment and must not inherit the
        # unknown-Forex fallback of 0.0001.
        pip_sz = point * 10.0 if digits in {3, 5} else point
        spread_pts = int(_required_number(info, "spread"))
        spread_pips = round((spread_pts * point) / pip_sz, 2) if pip_sz > 0 else float(spread_pts)

        return MT5SymbolInfo(
            name=str(_get_field(info, "name", broker_sym)),
            canonical_symbol=canon or broker_sym,
            path=str(_get_field(info, "path", "")),
            digits=digits,
            point=point,
            pip_size=pip_sz,
            spread_points=spread_pts,
            spread_pips=spread_pips,
            bid=float(_get_field(info, "bid", 0.0)),
            ask=float(_get_field(info, "ask", 0.0)),
            last=float(_get_field(info, "last", 0.0)),
            volume_min=_required_number(info, "volume_min", positive=True),
            volume_max=_required_number(info, "volume_max", positive=True),
            volume_step=_required_number(info, "volume_step", positive=True),
            contract_size=_required_number(info, "trade_contract_size", positive=True),
            currency_base=str(_required_field(info, "currency_base")),
            currency_profit=str(_required_field(info, "currency_profit")),
            currency_margin=str(_required_field(info, "currency_margin")),
            trade_mode=int(_required_number(info, "trade_mode")),
        )

    def to_broker_constraints(self, symbol: str) -> BrokerExecutionConstraints:
        """Derive broker execution constraints for position sizing."""
        return self.get_symbol_info(symbol).to_broker_constraints()

    @_serialized_mt5
    def get_symbols(self, group: str | None = None) -> list[MT5SymbolInfo]:
        """Query multiple symbols from the MT5 broker catalogue."""
        self._ensure_connected()
        mt5 = self.api
        raw_symbols = mt5.symbols_get(group) if group else mt5.symbols_get()
        if raw_symbols is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 symbol catalogue: {desc}", code=code)
        result = []
        for s in raw_symbols:
            try:
                name = _get_field(s, "name", "")
                if name:
                    result.append(self.get_symbol_info(name))
            except Exception:
                continue
        return result

    # -----------------------------------------------------------------------
    # 3. Market Ticks & Candle Streaming
    # -----------------------------------------------------------------------

    @_serialized_mt5
    def get_current_tick(self, symbol: str) -> MT5Tick:
        """Fetch the most recent market tick for a symbol."""
        self._ensure_connected()
        mt5 = self.api
        spec = self.get_symbol_info(symbol)
        broker_sym = spec.name
        raw_tick = mt5.symbol_info_tick(broker_sym)

        if raw_tick is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch tick for '{symbol}': {desc}", code=code)

        dt = _parse_timestamp(_get_field(raw_tick, "time", 0))
        bid = float(_get_field(raw_tick, "bid", 0.0))
        ask = float(_get_field(raw_tick, "ask", 0.0))
        last = float(_get_field(raw_tick, "last", 0.0))
        vol = float(_get_field(raw_tick, "volume", 0.0))
        flags = int(_get_field(raw_tick, "flags", 0))

        pip_sz = spec.pip_size
        settings = get_config()
        validate_quote(
            bid,
            ask,
            dt,
            pip_size=pip_sz,
            max_age_seconds=settings.get("forex_quote_max_age_seconds", 30),
            # Risk policy is enforced by ForexRiskEngine. This bound only rejects
            # structurally implausible observations before they enter context.
            max_spread_pips=settings.get("forex_quote_sanity_max_spread_pips", 1000.0),
        )
        spread_price = ask - bid
        spread_pips = round(spread_price / pip_sz, 1) if pip_sz > 0 else 0.0
        point = spec.point
        spread_points = int(round(spread_price / point)) if point > 0 else 0

        return MT5Tick(
            time=dt,
            bid=bid,
            ask=ask,
            last=last,
            volume=vol,
            spread_points=spread_points,
            spread_pips=spread_pips,
            flags=flags,
            source="MT5", broker_symbol=broker_sym,
            broker=self.connection.server or str(_get_field(self.api.account_info(), "server", "unknown")),
            retrieved_at_utc=datetime.now(timezone.utc),
        )

    def _bars_from_rates(self, rates, spec, timeframe, cutoff, start=None):
        if rates is None or len(rates) == 0:
            raise MT5DataError("No MT5 candle data returned")
        retrieved = datetime.now(timezone.utc)
        bars = []
        for row in rates:
            opened = _parse_timestamp(_extract_rate_val(row, "time", 0))
            closed = opened + timedelta(seconds=timeframe.seconds)
            if opened > retrieved:
                raise DataInsufficientError("future MT5 candle timestamp")
            if closed > cutoff or (start is not None and opened < start):
                continue
            bars.append(ForexBar(
                timestamp=opened, close_time=closed, is_closed=True,
                open=float(_extract_rate_val(row, "open", 1)),
                high=float(_extract_rate_val(row, "high", 2)),
                low=float(_extract_rate_val(row, "low", 3)),
                close=float(_extract_rate_val(row, "close", 4)),
                volume=float(_extract_rate_val(row, "tick_volume", 5)),
                spread_pips=float(_extract_rate_val(row, "spread", 6)) * spec.point / spec.pip_size,
                data_source="MT5", broker_symbol=spec.name, retrieved_at_utc=retrieved,
                broker=self.connection.server or str(_get_field(self.api.account_info(), "server", "unknown")),
            ))
        if not bars:
            raise DataInsufficientError("no completed MT5 candles at cutoff")
        validate_forex_candles(bars_to_frame(bars, timeframe), timeframe=timeframe)
        return bars

    @_serialized_mt5
    def get_candles(self, symbol, timeframe="H1", count=100, as_of=None):
        """Fetch completed bars; MT5 timestamps identify UTC candle opens."""
        self._ensure_connected()
        if count < 1:
            raise ValueError("count must be positive")
        spec = self.get_symbol_info(symbol)
        tf = Timeframe(str(timeframe).upper()) if not isinstance(timeframe, Timeframe) else timeframe
        cutoff = utc_timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
        rates = self.api.copy_rates_from(spec.name, tf.mt5_timeframe, cutoff, count + 1)
        return self._bars_from_rates(rates, spec, tf, cutoff)[-count:]

    @_serialized_mt5
    def get_candles_range(self, symbol, timeframe, date_from, date_to):
        """Fetch complete bars in an explicitly bounded UTC interval."""
        self._ensure_connected()
        start, cutoff = utc_timestamp(date_from), utc_timestamp(date_to)
        if start >= cutoff:
            raise ValueError("date_from must precede date_to")
        spec = self.get_symbol_info(symbol)
        tf = Timeframe(str(timeframe).upper()) if not isinstance(timeframe, Timeframe) else timeframe
        rates = self.api.copy_rates_range(spec.name, tf.mt5_timeframe, start, cutoff)
        return self._bars_from_rates(rates, spec, tf, cutoff, start)

    def fetch_multi_timeframe_data(self, symbol, timeframes=("M15", "H1", "H4", "D1"),
                                  count=100, as_of=None):
        """Use one cutoff and DataFrame/Timeframe contracts for all required frames."""
        cutoff = utc_timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
        candles = {}
        for item in timeframes:
            tf = Timeframe(item)
            candles[tf] = bars_to_frame(self.get_candles(symbol, tf, count, cutoff), tf)
        return MultiTimeframeData(symbol=normalize_forex_pair(symbol), candles=candles, as_of=cutoff)

    def get_atr_pips(
        self,
        symbol: str,
        timeframe: str | Timeframe = "H1",
        *,
        period: int = 14,
        as_of: datetime | None = None,
    ) -> float:
        """Calculate ATR from the same completed MT5 candle contract used by analysis."""
        tf = Timeframe(str(timeframe).upper()) if not isinstance(timeframe, Timeframe) else timeframe
        bars = self.get_candles(symbol, tf, max(period * 3, period + 1), as_of)
        values = calculate_atr_pips(bars_to_frame(bars, tf), normalize_forex_pair(symbol), period)
        valid = values.dropna()
        if valid.empty or float(valid.iloc[-1]) <= 0:
            raise DataInsufficientError("deterministic MT5 ATR is unavailable")
        return float(valid.iloc[-1])

    def get_conversion_observations(
        self,
        pair: str,
        account_currency: str,
    ) -> tuple[ForexConversionRate, ...]:
        """Observe the base/quote conversion pairs needed by sizing and margin."""
        canon = normalize_forex_pair(pair)
        account = account_currency.upper()
        required = {canon[:3], canon[3:6]} - {account}
        observations: list[ForexConversionRate] = []
        for currency in sorted(required):
            found = False
            for symbol in (f"{currency}{account}", f"{account}{currency}"):
                try:
                    tick = self.get_current_tick(symbol)
                except (MT5DataError, MT5SymbolError, DataInsufficientError):
                    continue
                midpoint = (tick.bid + tick.ask) / 2.0
                observations.append(
                    ForexConversionRate(
                        from_currency=symbol[:3],
                        to_currency=symbol[3:6],
                        status=AvailabilityStatus.AVAILABLE,
                        rate=midpoint,
                        conversion_path=(tick.broker_symbol or symbol,),
                        source=tick.source,
                        observed_at=tick.time,
                    )
                )
                found = True
                break
            if not found:
                observations.append(
                    ForexConversionRate(
                        from_currency=currency,
                        to_currency=account,
                        status=AvailabilityStatus.UNAVAILABLE,
                        conversion_path=(f"{currency}->{account}",),
                        source="MT5",
                    )
                )
        return tuple(observations)

    # -----------------------------------------------------------------------
    # 4. Open Positions & Pending Orders Inspection
    # -----------------------------------------------------------------------

    @_serialized_mt5
    def get_open_positions(
        self, symbol: str | None = None, ticket: int | None = None
    ) -> list[MT5Position]:
        """Query currently active open positions in the terminal."""
        self._ensure_connected()
        mt5 = self.api
        kwargs: dict[str, Any] = {}
        if symbol:
            kwargs["symbol"] = canonical_to_broker(normalize_forex_pair(symbol)) or symbol
        if ticket:
            kwargs["ticket"] = ticket

        raw_positions = mt5.positions_get(**kwargs)
        if raw_positions is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 open positions: {desc}", code=code)

        positions: list[MT5Position] = []
        for p in raw_positions:
            t_type = _required_field(p, "type")
            action = ForexAction.LONG if (t_type == 0 or t_type == "POSITION_TYPE_BUY") else ForexAction.SHORT
            dt = _parse_timestamp(_get_field(p, "time", 0))

            positions.append(
                MT5Position(
                    ticket=int(_required_number(p, "ticket", positive=True)),
                    identifier=int(_get_field(p, "identifier", _get_field(p, "ticket", 0))),
                    time=dt,
                    type=action,
                    magic=int(_get_field(p, "magic", 0)),
                    symbol=str(_required_field(p, "symbol")),
                    volume=_required_number(p, "volume", positive=True),
                    price_open=_required_number(p, "price_open", positive=True),
                    sl=float(_get_field(p, "sl", 0.0)),
                    tp=float(_get_field(p, "tp", 0.0)),
                    price_current=_required_number(p, "price_current", positive=True),
                    swap=_required_number(p, "swap"),
                    profit=_required_number(p, "profit"),
                    comment=str(_get_field(p, "comment", "")),
                )
            )

        return positions

    def to_open_positions(
        self,
        symbol: str | None = None,
        *,
        account_currency: str = "USD",
        conversions: tuple[ForexConversionRate, ...] = (),
        as_of_utc: datetime | None = None,
        max_conversion_age: timedelta | None = None,
    ) -> list[OpenPosition]:
        """Convert MT5 positions to domain OpenPosition objects for portfolio risk controls."""
        raw_positions = self.get_open_positions(symbol=symbol)
        observed = list(conversions)
        seen = {(item.from_currency, item.to_currency) for item in observed}
        for position in raw_positions:
            for item in self.get_conversion_observations(position.symbol, account_currency):
                key = (item.from_currency, item.to_currency)
                if key not in seen:
                    observed.append(item)
                    seen.add(key)
        return [
            p.to_open_position(
                account_currency=account_currency,
                conversions=tuple(observed),
                as_of_utc=as_of_utc,
                max_conversion_age=max_conversion_age,
            )
            for p in raw_positions
        ]

    @_serialized_mt5
    def get_pending_orders(
        self, symbol: str | None = None, ticket: int | None = None
    ) -> list[MT5Order]:
        """Query currently placed pending orders (limit/stop orders)."""
        self._ensure_connected()
        mt5 = self.api
        kwargs: dict[str, Any] = {}
        if symbol:
            kwargs["symbol"] = canonical_to_broker(normalize_forex_pair(symbol)) or symbol
        if ticket:
            kwargs["ticket"] = ticket

        raw_orders = mt5.orders_get(**kwargs)
        if raw_orders is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 pending orders: {desc}", code=code)

        type_names = {
            2: "ORDER_TYPE_BUY_LIMIT",
            3: "ORDER_TYPE_SELL_LIMIT",
            4: "ORDER_TYPE_BUY_STOP",
            5: "ORDER_TYPE_SELL_STOP",
            6: "ORDER_TYPE_BUY_STOP_LIMIT",
            7: "ORDER_TYPE_SELL_STOP_LIMIT",
        }

        orders: list[MT5Order] = []
        for o in raw_orders:
            dt = _parse_timestamp(_get_field(o, "time_setup", 0))
            t_type = _get_field(o, "type", 2)
            type_str = type_names.get(t_type, str(t_type) if isinstance(t_type, str) else f"ORDER_TYPE_{t_type}")

            orders.append(
                MT5Order(
                    ticket=int(_required_number(o, "ticket", positive=True)),
                    time_setup=dt,
                    type=type_str,
                    state=str(_required_field(o, "state")),
                    magic=int(_get_field(o, "magic", 0)),
                    symbol=str(_required_field(o, "symbol")),
                    volume_initial=_required_number(o, "volume_initial", positive=True),
                    volume_current=_required_number(o, "volume_current", positive=True),
                    price_open=_required_number(o, "price_open", positive=True),
                    sl=float(_get_field(o, "sl", 0.0)),
                    tp=float(_get_field(o, "tp", 0.0)),
                    comment=str(_get_field(o, "comment", "")),
                )
            )

        return orders

    def to_pending_exposures(
        self,
        symbol: str | None = None,
        *,
        account_currency: str = "USD",
        conversions: tuple[ForexConversionRate, ...] = (),
        as_of_utc: datetime | None = None,
        max_conversion_age: timedelta | None = None,
    ) -> list[PendingExposure]:
        """Convert MT5 pending orders to domain PendingExposure objects for portfolio risk controls."""
        raw_orders = self.get_pending_orders(symbol=symbol)
        observed = list(conversions)
        seen = {(item.from_currency, item.to_currency) for item in observed}
        for order in raw_orders:
            for item in self.get_conversion_observations(order.symbol, account_currency):
                key = (item.from_currency, item.to_currency)
                if key not in seen:
                    observed.append(item)
                    seen.add(key)
        return [
            o.to_pending_exposure(
                account_currency=account_currency,
                conversions=tuple(observed),
                as_of_utc=as_of_utc,
                max_conversion_age=max_conversion_age,
            )
            for o in raw_orders
        ]

    # -----------------------------------------------------------------------
    # 5. Historical Deals & Orders Inspection
    # -----------------------------------------------------------------------

    @_serialized_mt5
    def get_deals(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        position: int | None = None,
        position_ticket: int | None = None,
        count: int | None = None,
    ) -> list[MT5Deal]:
        """Query closed trades and executed deals from account history."""
        self._ensure_connected()
        mt5 = self.api
        kwargs: dict[str, Any] = {}
        if date_from is not None:
            kwargs["date_from"] = date_from
        if date_to is not None:
            kwargs["date_to"] = date_to

        pos = position if position is not None else position_ticket
        if pos is not None:
            kwargs["position"] = pos

        account_info = mt5.account_info()
        broker_server = self.connection.server or str(_get_field(account_info, "server", ""))

        # Default to last 30 days if no range provided
        if "date_from" not in kwargs and "position" not in kwargs:
            from datetime import timedelta
            kwargs["date_from"] = datetime.now(timezone.utc) - timedelta(days=30)
            kwargs["date_to"] = datetime.now(timezone.utc)

        if "position" in kwargs:
            raw_deals = mt5.history_deals_get(position=kwargs["position"])
        else:
            raw_deals = mt5.history_deals_get(
                _utc_to_broker_query_time(kwargs["date_from"], broker_server),
                _utc_to_broker_query_time(kwargs["date_to"], broker_server),
            )
        if raw_deals is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 deal history: {desc}", code=code)

        if count is not None and count > 0 and len(raw_deals) > count:
            raw_deals = raw_deals[-count:]

        entry_map = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
        deals: list[MT5Deal] = []
        for d in raw_deals:
            dt = _parse_broker_timestamp(_required_field(d, "time"), broker_server)
            e_val = _required_field(d, "entry")
            entry_str = entry_map.get(e_val, str(e_val) if isinstance(e_val, str) else "IN")
            d_type = _required_field(d, "type")
            if d_type == 0 or d_type == "DEAL_TYPE_BUY":
                deal_type_str = "DEAL_TYPE_BUY"
            elif d_type == 1 or d_type == "DEAL_TYPE_SELL":
                deal_type_str = "DEAL_TYPE_SELL"
            else:
                deal_type_str = str(d_type)

            symbol = str(_get_field(d, "symbol", ""))
            volume = _required_number(d, "volume")
            price = _required_number(d, "price")
            if deal_type_str in {"DEAL_TYPE_BUY", "DEAL_TYPE_SELL"} and (
                not symbol or volume <= 0 or price <= 0
            ):
                raise DataInsufficientError("trade deal is missing symbol, volume, or price")
            deals.append(
                MT5Deal(
                    ticket=int(_required_number(d, "ticket", positive=True)),
                    order=int(_required_number(d, "order")),
                    position_id=int(_required_number(d, "position_id")),
                    time=dt,
                    type=deal_type_str,
                    entry=entry_str,
                    magic=int(_get_field(d, "magic", 0)),
                    symbol=symbol,
                    volume=volume,
                    price=price,
                    commission=_required_number(d, "commission"),
                    swap=_required_number(d, "swap"),
                    fee=_required_number(d, "fee"),
                    reason=int(_required_number(d, "reason")),
                    profit=_required_number(d, "profit"),
                    comment=str(_get_field(d, "comment", "")),
                )
            )

        return deals

    def get_daily_realized_pnl(self, as_of_utc: datetime | None = None) -> tuple[float, datetime]:
        """Return broker-realized net P&L for the current UTC trading day."""
        cutoff = utc_timestamp(as_of_utc) if as_of_utc is not None else datetime.now(timezone.utc)
        day_start = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = self.get_deals(date_from=day_start, date_to=cutoff)
        realized = sum(
            deal.profit + deal.commission + deal.swap + deal.fee
            for deal in deals
            if deal.type in {"DEAL_TYPE_BUY", "DEAL_TYPE_SELL"}
        )
        return round(realized, 2), day_start

    @_serialized_mt5
    def get_orders_history(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        ticket: int | None = None,
    ) -> list[MT5Order]:
        """Query past filled, cancelled, or expired orders from history."""
        self._ensure_connected()
        mt5 = self.api
        kwargs: dict[str, Any] = {}
        if date_from is not None:
            kwargs["date_from"] = date_from
        if date_to is not None:
            kwargs["date_to"] = date_to
        if ticket is not None:
            kwargs["ticket"] = ticket

        if "date_from" not in kwargs and "ticket" not in kwargs:
            from datetime import timedelta
            kwargs["date_from"] = datetime.now(timezone.utc) - timedelta(days=30)
            kwargs["date_to"] = datetime.now(timezone.utc)

        if "ticket" in kwargs:
            raw_orders = mt5.history_orders_get(ticket=kwargs["ticket"])
        else:
            raw_orders = mt5.history_orders_get(kwargs["date_from"], kwargs["date_to"])
        if raw_orders is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 order history: {desc}", code=code)

        orders: list[MT5Order] = []
        for o in raw_orders:
            dt = _parse_timestamp(_get_field(o, "time_setup", 0))
            t_type = _get_field(o, "type", 0)
            type_str = str(t_type) if isinstance(t_type, str) else f"ORDER_TYPE_{t_type}"

            orders.append(
                MT5Order(
                    ticket=int(_get_field(o, "ticket", 0)),
                    time_setup=dt,
                    type=type_str,
                    state=str(_get_field(o, "state", "FILLED")),
                    magic=int(_get_field(o, "magic", 0)),
                    symbol=str(_get_field(o, "symbol", "")),
                    volume_initial=float(_get_field(o, "volume_initial", 0.0)),
                    volume_current=float(_get_field(o, "volume_current", 0.0)),
                    price_open=float(_get_field(o, "price_open", 0.0)),
                    sl=float(_get_field(o, "sl", 0.0)),
                    tp=float(_get_field(o, "tp", 0.0)),
                    comment=str(_get_field(o, "comment", "")),
                )
            )
        return orders
