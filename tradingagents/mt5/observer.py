"""MetaTrader 5 Read-Only Observer Adapter (Phase 14).

Provides strictly read-only inspection of MT5 accounts, symbols, market ticks,
multi-timeframe candles, open positions, pending orders, and execution deals.
Never places, modifies, or cancels trading orders.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from numbers import Real
from typing import Any

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
from tradingagents.forex.domain import Timeframe, normalize_forex_pair
from tradingagents.forex.pips import pip_size_for
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
)

logger = logging.getLogger(__name__)


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Safely extract field from an object (supports dicts, objects with attrs, and namedtuples)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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

    def __init__(self, connection: MT5ConnectionManager | None = None):
        self.connection = connection or MT5ConnectionManager()

    @property
    def api(self) -> Any:
        return self.connection.api

    def _ensure_connected(self) -> None:
        """Verify connection or attempt auto-connect."""
        if not self.connection.is_connected():
            logger.info("MT5Observer: connection not active; connecting...")
            self.connection.connect()

    # -----------------------------------------------------------------------
    # 1. Account Inspection
    # -----------------------------------------------------------------------

    def get_account_info(self) -> MT5AccountInfo:
        """Fetch current account balance, equity, margin, and trading permissions."""
        self._ensure_connected()
        mt5 = self.api
        raw_info = mt5.account_info()
        if raw_info is None:
            code, desc = self.connection._get_last_error()
            raise MT5DataError(f"Failed to fetch MT5 account info: {desc}", code=code)

        mode_map = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
        raw_trade_mode = _get_field(raw_info, "trade_mode", 0)
        trade_mode = mode_map.get(raw_trade_mode, "DEMO") if isinstance(raw_trade_mode, int) else str(raw_trade_mode)

        return MT5AccountInfo(
            login=int(_get_field(raw_info, "login", 0)),
            name=str(_get_field(raw_info, "name", "")),
            server=str(_get_field(raw_info, "server", "")),
            currency=str(_get_field(raw_info, "currency", "USD")),
            leverage=int(_get_field(raw_info, "leverage", 100)),
            balance=float(_get_field(raw_info, "balance", 0.0)),
            equity=float(_get_field(raw_info, "equity", 0.0)),
            profit=float(_get_field(raw_info, "profit", 0.0)),
            margin=float(_get_field(raw_info, "margin", 0.0)),
            margin_free=float(_get_field(raw_info, "margin_free", 0.0)),
            margin_level=float(_get_field(raw_info, "margin_level", 0.0)),
            margin_so_call=float(_get_field(raw_info, "margin_so_call", 100.0)),
            margin_so_so=float(_get_field(raw_info, "margin_so_so", 50.0)),
            trade_mode=trade_mode,
            trade_allowed=bool(_get_field(raw_info, "trade_allowed", True)),
        )

    def to_sizing_account_profile(self) -> ForexAccountProfile:
        """Export live MT5 account profile directly to ForexAccountProfile."""
        return self.get_account_info().to_forex_account_profile()

    # -----------------------------------------------------------------------
    # 2. Symbol Specification & Quotes
    # -----------------------------------------------------------------------

    def get_symbol_info(self, symbol: str) -> MT5SymbolInfo:
        """Fetch broker contract specifications, spread, and pricing for a symbol."""
        self._ensure_connected()
        mt5 = self.api

        canon = normalize_forex_pair(symbol)
        explicit_symbol = get_config().get("forex_broker_symbols", {}).get(canon) or (symbol if symbol != canon else None)
        broker_sym = explicit_symbol or canonical_to_broker(canon)

        # Select symbol in Market Watch if not already selected
        selected = mt5.symbol_select(broker_sym, True)
        if not selected and explicit_symbol:
            raise MT5SymbolError(f"Configured broker symbol {broker_sym!r} is unavailable")
        if not selected:
            # Try canonical symbol as fallback
            selected = mt5.symbol_select(canon, True)
            if selected:
                broker_sym = canon

        info = mt5.symbol_info(broker_sym)
        if info is None and not explicit_symbol:
            # Try raw symbol string
            info = mt5.symbol_info(symbol)
            broker_sym = symbol

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
        pip_sz = pip_size_for(canon) if canon else (point * 10.0)
        spread_pts = int(_get_field(info, "spread", 10))
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
            volume_min=float(_get_field(info, "volume_min", 0.01)),
            volume_max=float(_get_field(info, "volume_max", 100.0)),
            volume_step=float(_get_field(info, "volume_step", 0.01)),
            contract_size=float(_get_field(info, "trade_contract_size", 100000.0)),
            currency_base=str(_get_field(info, "currency_base", "EUR")),
            currency_profit=str(_get_field(info, "currency_profit", "USD")),
            currency_margin=str(_get_field(info, "currency_margin", "USD")),
            trade_mode=int(_get_field(info, "trade_mode", 4)),
        )

    def to_broker_constraints(self, symbol: str) -> BrokerExecutionConstraints:
        """Derive broker execution constraints for position sizing."""
        return self.get_symbol_info(symbol).to_broker_constraints()

    def get_symbols(self, group: str | None = None) -> list[MT5SymbolInfo]:
        """Query multiple symbols from the MT5 broker catalogue."""
        self._ensure_connected()
        mt5 = self.api
        raw_symbols = mt5.symbols_get(group) if group else mt5.symbols_get()
        if raw_symbols is None:
            return []
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
        validate_quote(bid, ask, dt, pip_size=pip_sz,
                       max_age_seconds=settings.get("forex_quote_max_age_seconds", 30),
                       max_spread_pips=settings.get("forex_max_spread_pips", 5.0))
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

    # -----------------------------------------------------------------------
    # 4. Open Positions & Pending Orders Inspection
    # -----------------------------------------------------------------------

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
            return []

        positions: list[MT5Position] = []
        for p in raw_positions:
            t_type = _get_field(p, "type", 0)
            action = ForexAction.LONG if (t_type == 0 or t_type == "POSITION_TYPE_BUY") else ForexAction.SHORT
            dt = _parse_timestamp(_get_field(p, "time", 0))

            positions.append(
                MT5Position(
                    ticket=int(_get_field(p, "ticket", 0)),
                    time=dt,
                    type=action,
                    magic=int(_get_field(p, "magic", 0)),
                    symbol=str(_get_field(p, "symbol", "")),
                    volume=float(_get_field(p, "volume", 0.0)),
                    price_open=float(_get_field(p, "price_open", 0.0)),
                    sl=float(_get_field(p, "sl", 0.0)),
                    tp=float(_get_field(p, "tp", 0.0)),
                    price_current=float(_get_field(p, "price_current", 0.0)),
                    swap=float(_get_field(p, "swap", 0.0)),
                    profit=float(_get_field(p, "profit", 0.0)),
                    comment=str(_get_field(p, "comment", "")),
                )
            )

        return positions

    def to_open_positions(self, symbol: str | None = None) -> list[OpenPosition]:
        """Convert MT5 positions to domain OpenPosition objects for portfolio risk controls."""
        return [p.to_open_position() for p in self.get_open_positions(symbol=symbol)]

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
            return []

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
                    ticket=int(_get_field(o, "ticket", 0)),
                    time_setup=dt,
                    type=type_str,
                    state=str(_get_field(o, "state", "PLACED")),
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

    # -----------------------------------------------------------------------
    # 5. Historical Deals & Orders Inspection
    # -----------------------------------------------------------------------

    def get_deals(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        position_ticket: int | None = None,
    ) -> list[MT5Deal]:
        """Query closed trades and executed deals from account history."""
        self._ensure_connected()
        mt5 = self.api
        kwargs: dict[str, Any] = {}
        if date_from is not None:
            kwargs["date_from"] = date_from
        if date_to is not None:
            kwargs["date_to"] = date_to
        if position_ticket is not None:
            kwargs["position"] = position_ticket

        # Default to last 30 days if no range provided
        if "date_from" not in kwargs and "position" not in kwargs:
            from datetime import timedelta
            kwargs["date_from"] = datetime.now(timezone.utc) - timedelta(days=30)
            kwargs["date_to"] = datetime.now(timezone.utc)

        raw_deals = mt5.history_deals_get(**kwargs)
        if raw_deals is None:
            return []

        entry_map = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
        deals: list[MT5Deal] = []
        for d in raw_deals:
            dt = _parse_timestamp(_get_field(d, "time", 0))
            e_val = _get_field(d, "entry", 0)
            entry_str = entry_map.get(e_val, str(e_val) if isinstance(e_val, str) else "IN")
            d_type = _get_field(d, "type", 0)
            deal_type_str = "DEAL_TYPE_BUY" if (d_type == 0 or d_type == "DEAL_TYPE_BUY") else "DEAL_TYPE_SELL"

            deals.append(
                MT5Deal(
                    ticket=int(_get_field(d, "ticket", 0)),
                    order=int(_get_field(d, "order", 0)),
                    position_id=int(_get_field(d, "position_id", 0)),
                    time=dt,
                    type=deal_type_str,
                    entry=entry_str,
                    magic=int(_get_field(d, "magic", 0)),
                    symbol=str(_get_field(d, "symbol", "")),
                    volume=float(_get_field(d, "volume", 0.0)),
                    price=float(_get_field(d, "price", 0.0)),
                    commission=float(_get_field(d, "commission", 0.0)),
                    swap=float(_get_field(d, "swap", 0.0)),
                    profit=float(_get_field(d, "profit", 0.0)),
                    comment=str(_get_field(d, "comment", "")),
                )
            )

        return deals

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

        raw_orders = mt5.history_orders_get(**kwargs)
        if raw_orders is None:
            return []

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
