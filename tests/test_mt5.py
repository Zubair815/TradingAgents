"""Comprehensive Unit & Integration Test Suite for MetaTrader 5 Adapter (Phase 14).

Tests:
1. Error taxonomy & hierarchy (MT5Error, MT5ConnectionError, MT5AuthorizationError, etc.).
2. MT5ConnectionManager lifecycle, terminal path validation, authentication, diagnostics.
3. MT5Observer read-only guarantees (strict non-mutating inspection).
4. Account inspection & ForexAccountProfile conversion.
5. Symbol specifications & BrokerExecutionConstraints conversion.
6. Real-time market tick extraction & spread calculation.
7. OHLCV candle streaming, ranges, and MultiTimeframeData coordination.
8. Live position inspection, unrealized pips, and OpenPosition risk conversion.
9. Pending orders inspection & historical deals analysis.
10. End-to-end integration with ForexPositionSizingEngine and ForexRiskEngine.
11. Package exports & lazy loading from tradingagents.forex.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.dataflows.forex_data import ForexBar, MultiTimeframeData
from tradingagents.forex.domain import Timeframe
from tradingagents.mt5.connection import MT5ConnectionManager
from tradingagents.mt5.errors import (
    MT5AuthorizationError,
    MT5ConnectionError,
    MT5DataError,
    MT5Error,
    MT5NotInstalledError,
    MT5SymbolError,
    MT5TerminalNotFoundError,
)
from tradingagents.mt5.models import (
    MT5AccountInfo,
    MT5ConnectionStatus,
    MT5SymbolInfo,
    MT5Tick,
)
from tradingagents.mt5.observer import MT5Observer
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    ForexPositionSizingEngine,
    OpenPosition,
)

# ===========================================================================
# Mock MT5 API Fixture
# ===========================================================================

class MockTerminalInfo:
    def __init__(self, connected: bool = True, trade_allowed: bool = True):
        self.connected = connected
        self.trade_allowed = trade_allowed
        self.name = "MetaTrader 5"
        self.path = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        self.data_path = "C:\\AppData\\MetaTrader 5"
        self.build = 4500

    def _asdict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "trade_allowed": self.trade_allowed,
            "name": self.name,
            "path": self.path,
            "data_path": self.data_path,
            "build": self.build,
        }


class MockAccountInfoData:
    def __init__(
        self,
        login: int = 88881234,
        name: str = "Quantitative Desk",
        server: str = "MetaQuotes-Demo",
        currency: str = "USD",
        leverage: int = 100,
        balance: float = 100000.0,
        equity: float = 102500.0,
        profit: float = 2500.0,
        margin: float = 2000.0,
        margin_free: float = 100500.0,
        margin_level: float = 5125.0,
        margin_so_call: float = 100.0,
        margin_so_so: float = 50.0,
        trade_mode: int = 0,  # DEMO
        trade_allowed: bool = True,
    ):
        self.login = login
        self.name = name
        self.server = server
        self.currency = currency
        self.leverage = leverage
        self.balance = balance
        self.equity = equity
        self.profit = profit
        self.margin = margin
        self.margin_free = margin_free
        self.margin_level = margin_level
        self.margin_so_call = margin_so_call
        self.margin_so_so = margin_so_so
        self.trade_mode = trade_mode
        self.trade_allowed = trade_allowed


class MockSymbolInfoData:
    def __init__(
        self,
        name: str = "EURUSD",
        digits: int = 5,
        point: float = 0.00001,
        spread: int = 12,
        bid: float = 1.08500,
        ask: float = 1.08512,
        last: float = 1.08506,
        volume_min: float = 0.01,
        volume_max: float = 100.0,
        volume_step: float = 0.01,
        trade_contract_size: float = 100000.0,
        currency_base: str = "EUR",
        currency_profit: str = "USD",
        currency_margin: str = "USD",
        trade_mode: int = 4,
    ):
        self.name = name
        self.digits = digits
        self.point = point
        self.spread = spread
        self.bid = bid
        self.ask = ask
        self.last = last
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.volume_step = volume_step
        self.trade_contract_size = trade_contract_size
        self.currency_base = currency_base
        self.currency_profit = currency_profit
        self.currency_margin = currency_margin
        self.trade_mode = trade_mode


class MockTickData:
    def __init__(
        self,
        time: int = 1700000000,
        bid: float = 1.08500,
        ask: float = 1.08512,
        last: float = 1.08506,
        volume: float = 15.0,
        flags: int = 6,
    ):
        self.time = time
        self.bid = bid
        self.ask = ask
        self.last = last
        self.volume = volume
        self.flags = flags


class MockPositionData:
    def __init__(
        self,
        ticket: int = 5001,
        time: int = 1700000000,
        type: int = 0,  # 0: BUY / LONG
        magic: int = 999,
        symbol: str = "EURUSD",
        volume: float = 1.0,
        price_open: float = 1.08000,
        sl: float = 1.07500,
        tp: float = 1.09500,
        price_current: float = 1.08500,
        swap: float = -2.5,
        profit: float = 500.0,
        comment: str = "ForexTrendSetup",
    ):
        self.ticket = ticket
        self.time = time
        self.type = type
        self.magic = magic
        self.symbol = symbol
        self.volume = volume
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.price_current = price_current
        self.swap = swap
        self.profit = profit
        self.comment = comment


class MockOrderData:
    def __init__(
        self,
        ticket: int = 6001,
        time_setup: int = 1700001000,
        type: int = 2,  # ORDER_TYPE_BUY_LIMIT
        state: str = "ORDER_STATE_PLACED",
        magic: int = 999,
        symbol: str = "EURUSD",
        volume_initial: float = 0.5,
        volume_current: float = 0.5,
        price_open: float = 1.07200,
        sl: float = 1.06800,
        tp: float = 1.08500,
        comment: str = "LimitBuy",
    ):
        self.ticket = ticket
        self.time_setup = time_setup
        self.type = type
        self.state = state
        self.magic = magic
        self.symbol = symbol
        self.volume_initial = volume_initial
        self.volume_current = volume_current
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.comment = comment


class MockDealData:
    def __init__(
        self,
        ticket: int = 7001,
        order: int = 6001,
        position_id: int = 5001,
        time: int = 1700002000,
        type: int = 0,  # DEAL_TYPE_BUY
        entry: int = 1,  # OUT
        magic: int = 999,
        symbol: str = "EURUSD",
        volume: float = 1.0,
        price: float = 1.08500,
        commission: float = -5.0,
        swap: float = -2.5,
        profit: float = 500.0,
        comment: str = "ClosedTakeProfit",
    ):
        self.ticket = ticket
        self.order = order
        self.position_id = position_id
        self.time = time
        self.type = type
        self.entry = entry
        self.magic = magic
        self.symbol = symbol
        self.volume = volume
        self.price = price
        self.commission = commission
        self.swap = swap
        self.profit = profit
        self.comment = comment


class MockMT5API:
    """Configurable mock of the MetaTrader 5 Python C-API for testing."""

    def __init__(self):
        self.init_calls: list[dict[str, Any]] = []
        self.login_calls: list[dict[str, Any]] = []
        self.shutdown_calls: int = 0
        self.should_fail_init: bool = False
        self.should_fail_login: bool = False
        self.last_err: tuple[int, str] = (1, "Success")
        self.terminal_connected: bool = True
        self.history_deals_calls: list[tuple[tuple, dict]] = []
        self.history_orders_calls: list[tuple[tuple, dict]] = []
        self.known_symbols: dict[str, MockSymbolInfoData] = {
            "EURUSD": MockSymbolInfoData("EURUSD", spread=10, bid=1.08500, ask=1.08510),
            "EURUSDm": MockSymbolInfoData("EURUSDm", spread=12, bid=1.08500, ask=1.08512),
            "GBPJPY": MockSymbolInfoData("GBPJPY", digits=3, point=0.001, spread=15, bid=190.500, ask=190.515),
        }

    def initialize(self, **kwargs) -> bool:
        self.init_calls.append(kwargs)
        if self.should_fail_init:
            self.last_err = (-1, "Terminal process failed to start")
            return False
        return True

    def login(self, **kwargs) -> bool:
        self.login_calls.append(kwargs)
        if self.should_fail_login:
            self.last_err = (-2, "Authorization failed: Invalid password")
            return False
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def terminal_info(self) -> MockTerminalInfo | None:
        if not self.terminal_connected:
            return None
        return MockTerminalInfo(connected=True)

    def version(self) -> tuple[int, int, str]:
        return (500, 4500, "20 Sep 2024")

    def last_error(self) -> tuple[int, str]:
        return self.last_err

    def account_info(self) -> MockAccountInfoData:
        return MockAccountInfoData()

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol in self.known_symbols

    def symbol_info(self, symbol: str) -> MockSymbolInfoData | None:
        return self.known_symbols.get(symbol)

    def symbols_get(self, group: str | None = None) -> list[MockSymbolInfoData]:
        return list(self.known_symbols.values())

    def symbol_info_tick(self, symbol: str) -> MockTickData | None:
        if symbol not in self.known_symbols:
            return None
        sym_data = self.known_symbols[symbol]
        return MockTickData(time=int(datetime.now(timezone.utc).timestamp()), bid=sym_data.bid, ask=sym_data.ask)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self.copy_rates_from(symbol, timeframe, datetime.now(timezone.utc), count)

    def copy_rates_from(self, symbol, timeframe, date_from, count):
        from tradingagents.forex.sessions import is_market_open
        step = next(t.seconds for t in Timeframe if t.mt5_timeframe == timeframe)
        current = int(date_from.timestamp()) // step * step
        stamps = []
        while len(stamps) < count:
            if is_market_open(datetime.fromtimestamp(current, timezone.utc)):
                stamps.append(current)
            current -= step
        dtype = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                 ("close", "<f8"), ("tick_volume", "<i8"), ("spread", "<i4"), ("real_volume", "<i8")]
        return np.array([(t, 1.0800, 1.0820, 1.0790, 1.0810, 1500, 12, 0)
                         for t in reversed(stamps)], dtype=dtype)

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        step = next(t.seconds for t in Timeframe if t.mt5_timeframe == timeframe)
        rates = self.copy_rates_from(symbol, timeframe, date_to, int((date_to-date_from).total_seconds()/step)+1)
        return rates[rates["time"] >= date_from.timestamp()]

    def positions_get(self, **kwargs) -> list[MockPositionData]:
        pos1 = MockPositionData(ticket=5001, symbol="EURUSD", type=0, volume=1.0, price_open=1.08000, price_current=1.08500, sl=1.07500, tp=1.09500, profit=500.0)
        pos2 = MockPositionData(ticket=5002, symbol="GBPJPY", type=1, volume=0.5, price_open=190.500, price_current=189.500, sl=191.500, tp=188.000, profit=350.0)
        positions = [pos1, pos2]

        sym = kwargs.get("symbol")
        if sym:
            positions = [p for p in positions if p.symbol == sym]
        ticket = kwargs.get("ticket")
        if ticket:
            positions = [p for p in positions if p.ticket == ticket]
        return positions

    def orders_get(self, **kwargs) -> list[MockOrderData]:
        order1 = MockOrderData(ticket=6001, symbol="EURUSD", type=2, volume_initial=0.5, price_open=1.07200)
        order2 = MockOrderData(ticket=6002, symbol="GBPJPY", type=5, volume_initial=0.2, price_open=188.500)
        orders = [order1, order2]

        sym = kwargs.get("symbol")
        if sym:
            orders = [o for o in orders if o.symbol == sym]
        ticket = kwargs.get("ticket")
        if ticket:
            orders = [o for o in orders if o.ticket == ticket]
        return orders

    def history_deals_get(self, *args, **kwargs) -> list[MockDealData]:
        self.history_deals_calls.append((args, kwargs))
        deals = [
            MockDealData(ticket=7001, order=6001, position_id=5001, type=0, entry=0, profit=0.0),
            MockDealData(ticket=7002, order=6005, position_id=5001, type=1, entry=1, profit=500.0),
        ]
        pos = kwargs.get("position")
        if pos is not None:
            deals = [d for d in deals if d.position_id == pos]
        return deals

    def history_orders_get(self, *args, **kwargs) -> list[MockOrderData]:
        self.history_orders_calls.append((args, kwargs))
        return [
            MockOrderData(ticket=6000, symbol="EURUSD", type=2, state="FILLED"),
        ]


# ===========================================================================
# Test Cases
# ===========================================================================

class TestMT5Errors:
    """Test MT5 exception taxonomy and status representation."""

    def test_mt5_error_hierarchy(self):
        err = MT5Error("General failure", code=1001)
        assert isinstance(err, Exception)
        assert err.code == 1001
        assert str(err) == "[1001] General failure"

        not_installed = MT5NotInstalledError()
        assert isinstance(not_installed, MT5Error)
        assert "MetaTrader5 package is not installed" in str(not_installed)

        conn_err = MT5ConnectionError("Network timeout", code=-5)
        assert isinstance(conn_err, MT5Error)
        assert conn_err.code == -5

        term_not_found = MT5TerminalNotFoundError("Path missing")
        assert isinstance(term_not_found, MT5ConnectionError)

        auth_err = MT5AuthorizationError("Bad password", code=-2)
        assert isinstance(auth_err, MT5ConnectionError)

        sym_err = MT5SymbolError("Unknown symbol", code=404)
        assert isinstance(sym_err, MT5Error)

        data_err = MT5DataError("Rates failed", code=500)
        assert isinstance(data_err, MT5Error)


class TestMT5ConnectionManager:
    """Test MT5 connection lifecycle, error diagnostics, and configuration."""

    def test_missing_mt5_library_raises_error(self):
        mgr = MT5ConnectionManager(mt5_api=None)
        # Force _mt5 to None to simulate missing package
        mgr._mt5 = None
        with pytest.raises(MT5NotInstalledError):
            _ = mgr.api

    def test_terminal_not_found_raises_error(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        with pytest.raises(MT5TerminalNotFoundError):
            mgr.connect(path="C:\\NonExistentDirectory\\terminal64.exe")

    def test_successful_connect_and_disconnect(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(
            login=123456,
            password="test_password",
            server="Demo-Server",
            timeout=30000,
            portable=True,
            mt5_api=mock_api,
        )

        assert mgr.status == MT5ConnectionStatus.DISCONNECTED
        assert not mgr.is_connected()

        res = mgr.connect()
        assert res is True
        assert mgr.status == MT5ConnectionStatus.CONNECTED
        assert mgr.is_connected()
        assert len(mock_api.init_calls) == 1
        assert mock_api.init_calls[0]["portable"] is True
        assert len(mock_api.login_calls) == 1
        assert mock_api.login_calls[0]["login"] == 123456
        assert mock_api.login_calls[0]["server"] == "Demo-Server"

        mgr.disconnect()
        assert mgr.status == MT5ConnectionStatus.DISCONNECTED
        assert mock_api.shutdown_calls == 1

    def test_initialization_failure_raises_connection_error(self):
        mock_api = MockMT5API()
        mock_api.should_fail_init = True
        mgr = MT5ConnectionManager(mt5_api=mock_api)

        with pytest.raises(MT5ConnectionError) as exc_info:
            mgr.connect()
        assert mgr.status == MT5ConnectionStatus.FAILED
        assert "Failed to initialize MetaTrader 5" in str(exc_info.value)
        assert exc_info.value.code == -1

    def test_authorization_failure_raises_auth_error(self):
        mock_api = MockMT5API()
        mock_api.should_fail_login = True
        mgr = MT5ConnectionManager(mt5_api=mock_api)

        # Connect with credentials not in initialize call
        with patch.object(mock_api, "initialize", return_value=True):
            with pytest.raises(MT5AuthorizationError) as exc_info:
                mgr.connect(login=9999, password="bad_password", server="Broker-Live")
            assert mgr.status == MT5ConnectionStatus.FAILED
            assert mock_api.shutdown_calls >= 1
            assert "MT5 authorization failed" in str(exc_info.value)
            assert exc_info.value.code == -2
            assert "bad_password" not in str(exc_info.value)

    def test_context_manager_connects_and_disconnects(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)

        with mgr as connected_mgr:
            assert connected_mgr.is_connected()
            assert connected_mgr.status == MT5ConnectionStatus.CONNECTED

        assert mgr.status == MT5ConnectionStatus.DISCONNECTED
        assert mock_api.shutdown_calls == 1

    def test_terminal_info_and_version(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        mgr.connect()

        info = mgr.get_terminal_info()
        assert info["connected"] is True
        assert info["build"] == 4500

        ver = mgr.get_version()
        assert ver == (500, 4500, "20 Sep 2024")


class TestMT5ObserverAccount:
    """Test account info inspection and domain profile conversion."""

    def test_get_account_info(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        acc = observer.get_account_info()
        assert isinstance(acc, MT5AccountInfo)
        assert acc.login == 88881234
        assert acc.name == "Quantitative Desk"
        assert acc.currency == "USD"
        assert acc.balance == 100000.0
        assert acc.equity == 102500.0
        assert acc.profit == 2500.0
        assert acc.leverage == 100
        assert acc.trade_mode == "DEMO"
        assert acc.trade_allowed is True

    def test_to_sizing_account_profile(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        profile = observer.to_sizing_account_profile()
        assert isinstance(profile, ForexAccountProfile)
        assert profile.balance == 100000.0
        assert profile.equity == 102500.0
        assert profile.free_margin == 100500.0
        assert profile.used_margin == 2000.0
        assert profile.currency == "USD"
        assert profile.leverage == 100.0
        assert profile.margin_call_level == 100.0
        assert profile.stop_out_level == 50.0

    def test_account_info_failure_raises_data_error(self):
        mock_api = MockMT5API()
        mock_api.account_info = MagicMock(return_value=None)
        mock_api.last_err = (4001, "Account not found")
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        with pytest.raises(MT5DataError) as exc_info:
            observer.get_account_info()
        assert "Account not found" in str(exc_info.value)


class TestMT5ObserverSymbols:
    """Test broker symbol specification, spread in pips, and constraints."""

    def test_get_symbol_info_and_spread(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        sym = observer.get_symbol_info("EURUSD")
        assert isinstance(sym, MT5SymbolInfo)
        assert sym.name in ("EURUSD", "EURUSDm")
        assert sym.canonical_symbol == "EURUSD"
        assert sym.digits == 5
        assert sym.pip_size == 0.0001
        assert sym.spread_pips >= 1.0
        assert sym.volume_min == 0.01
        assert sym.contract_size == 100000.0

    def test_to_broker_constraints(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        constraints = observer.to_broker_constraints("EURUSD")
        assert isinstance(constraints, BrokerExecutionConstraints)
        assert constraints.min_volume == 0.01
        assert constraints.max_volume == 100.0
        assert constraints.volume_step == 0.01
        assert constraints.contract_size == 100000.0
        assert constraints.broker_symbol in ("EURUSD", "EURUSDm")
        assert constraints.digits == 5
        assert constraints.point == 0.00001
        assert constraints.pip_size == 0.0001

    def test_unknown_symbol_raises_symbol_error(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        with pytest.raises(MT5SymbolError):
            observer.get_symbol_info("INVALID_COIN")

    def test_get_symbols_catalog(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        symbols = observer.get_symbols()
        assert len(symbols) >= 2
        names = [s.canonical_symbol for s in symbols]
        assert "EURUSD" in names
        assert "GBPJPY" in names


class TestMT5ObserverTicksAndCandles:
    """Test tick quotes, candle bars, and multi-timeframe aggregation."""

    def test_get_current_tick(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        tick = observer.get_current_tick("EURUSD")
        assert isinstance(tick, MT5Tick)
        assert tick.bid == 1.08500
        assert tick.ask in (1.08510, 1.08512)
        assert tick.spread_pips == pytest.approx(1.2, 0.2)
        assert tick.time.tzinfo == timezone.utc

    def test_get_candles(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        candles = observer.get_candles("EURUSD", timeframe="H1", count=20)
        assert len(candles) == 20
        assert isinstance(candles[0], ForexBar)
        assert candles[0].open > 0.0
        assert candles[0].high >= candles[0].low
        assert candles[0].volume > 0
        assert candles[0].timestamp.tzinfo == timezone.utc

    def test_get_candles_with_as_of(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        as_of = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        candles = observer.get_candles("EURUSD", timeframe="H4", count=10, as_of=as_of)
        assert len(candles) == 10

    def test_get_candles_range(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        d_from = datetime(2024, 5, 1, tzinfo=timezone.utc)
        d_to = datetime(2024, 5, 10, tzinfo=timezone.utc)
        candles = observer.get_candles_range("EURUSD", "D1", d_from, d_to)
        assert len(candles) == 7
        assert all(b.close_time <= d_to for b in candles)

    def test_fetch_multi_timeframe_data(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        mtf = observer.fetch_multi_timeframe_data("EURUSD", timeframes=("M15", "H1", "H4", "D1"), count=15)
        assert isinstance(mtf, MultiTimeframeData)
        assert mtf.symbol == "EURUSD"
        assert set(mtf.candles.keys()) == {"M15", "H1", "H4", "D1"}
        assert len(mtf.candles["H1"]) == 15

    def test_get_atr_pips_uses_completed_mt5_candles(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        atr_pips = observer.get_atr_pips("EURUSD", "H1")
        assert atr_pips > 0.0


class TestMT5ObserverPositionsAndOrders:
    """Test live open positions, unrealized pips, pending orders, and deals."""

    def test_get_open_positions_and_unrealized_pips(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        positions = observer.get_open_positions()
        assert len(positions) == 2

        pos_long = next(p for p in positions if p.type == ForexAction.LONG)
        assert pos_long.symbol == "EURUSD"
        assert pos_long.price_open == 1.08000
        assert pos_long.price_current == 1.08500
        # (1.08500 - 1.08000) / 0.0001 = 50.0 pips
        assert pos_long.unrealized_pips == pytest.approx(50.0, 0.1)

        pos_short = next(p for p in positions if p.type == ForexAction.SHORT)
        assert pos_short.symbol == "GBPJPY"
        assert pos_short.price_open == 190.500
        assert pos_short.price_current == 189.500
        # (190.500 - 189.500) / 0.01 = 100.0 pips
        assert pos_short.unrealized_pips == pytest.approx(100.0, 0.1)

    def test_to_open_positions_risk_conversion(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        open_positions = observer.to_open_positions()
        assert len(open_positions) == 2
        assert isinstance(open_positions[0], OpenPosition)
        assert open_positions[0].pair == "EURUSD"
        assert open_positions[0].action == ForexAction.LONG
        assert open_positions[0].lots == 1.0
        assert open_positions[0].risk_amount > 0.0

    def test_get_pending_orders(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        orders = observer.get_pending_orders()
        assert len(orders) == 2
        assert orders[0].ticket == 6001
        assert orders[0].type == "ORDER_TYPE_BUY_LIMIT"
        assert orders[0].price_open == 1.07200

    def test_get_deals_history(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        deals = observer.get_deals()
        assert len(mock_api.history_deals_calls[-1][0]) == 2
        assert mock_api.history_deals_calls[-1][1] == {}
        assert len(deals) == 2
        assert deals[0].ticket == 7001
        assert deals[0].entry == "IN"
        assert deals[1].ticket == 7002
        assert deals[1].entry == "OUT"
        assert deals[1].profit == 500.0

        # Test position keyword argument
        pos_deals = observer.get_deals(position=5001)
        assert mock_api.history_deals_calls[-1] == ((), {"position": 5001})
        assert len(pos_deals) == 2
        no_deals = observer.get_deals(position=9999)
        assert len(no_deals) == 0

        # Test position_ticket keyword argument (backward compatibility)
        pos_ticket_deals = observer.get_deals(position_ticket=5001)
        assert len(pos_ticket_deals) == 2

        # Test count parameter
        limited_deals = observer.get_deals(count=1)
        assert len(limited_deals) == 1
        assert limited_deals[0].ticket == 7002

    def test_daily_realized_pnl_uses_utc_deal_history(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)
        cutoff = datetime(2026, 9, 30, 14, tzinfo=timezone.utc)

        realized, day_start = observer.get_daily_realized_pnl(cutoff)

        assert realized == 485.0
        assert day_start == datetime(2026, 9, 30, tzinfo=timezone.utc)

    def test_get_orders_history(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        past_orders = observer.get_orders_history()
        assert len(mock_api.history_orders_calls[-1][0]) == 2
        assert mock_api.history_orders_calls[-1][1] == {}
        assert len(past_orders) == 1
        assert past_orders[0].ticket == 6000
        assert past_orders[0].state == "FILLED"


class TestMT5ReadOnlySafety:
    """Verify strictly read-only guarantee: adapter has NO order execution capabilities."""

    def test_observer_has_no_trade_placement_methods(self):
        observer = MT5Observer(connection=MagicMock())
        forbidden_methods = [
            "order_send",
            "order_calc_margin",
            "order_check",
            "order_close",
            "buy",
            "sell",
            "close_position",
            "modify_position",
            "place_order",
            "cancel_order",
        ]
        for method_name in forbidden_methods:
            assert not hasattr(observer, method_name), f"MT5Observer violates read-only guarantee with method '{method_name}'"


class TestEndToEndEngineIntegration:
    """Test full bridging of MT5 live inspection data into Phase 10 & 11 risk/sizing engines."""

    def test_sizing_engine_accepts_mt5_profile_and_positions(self):
        mock_api = MockMT5API()
        mgr = MT5ConnectionManager(mt5_api=mock_api)
        observer = MT5Observer(connection=mgr)

        # 1. Fetch live profile and constraints from MT5
        account_profile = observer.to_sizing_account_profile()
        broker_constraints = observer.to_broker_constraints("EURUSD")
        existing_positions = observer.to_open_positions()

        # 2. Run sizing engine using live MT5 parameters
        engine = ForexPositionSizingEngine()
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            account=account_profile,
            entry_price=1.0850,
            stop_loss=1.0810,  # 40 pips stop
            constraints=broker_constraints,
            open_positions=existing_positions,
        )

        assert result.is_executable is False
        assert result.recommended_lot_size == 0.0
        assert "unbounded" in (result.rejection_reason or "")


class TestForexPackageExports:
    """Verify that tradingagents.forex cleanly exports all MT5 objects."""

    def test_forex_init_lazy_exports_mt5(self):
        import tradingagents.forex as fx

        assert hasattr(fx, "MT5ConnectionManager")
        assert hasattr(fx, "MT5ConnectionStatus")
        assert hasattr(fx, "MT5Observer")
        assert hasattr(fx, "MT5AccountInfo")
        assert hasattr(fx, "MT5SymbolInfo")
        assert hasattr(fx, "MT5Tick")
        assert hasattr(fx, "MT5Position")
        assert hasattr(fx, "MT5Order")
        assert hasattr(fx, "MT5Deal")
        assert hasattr(fx, "MT5Error")
        assert hasattr(fx, "MT5ConnectionError")

        # Test instantiation from forex package
        mock_api = MockMT5API()
        conn = fx.MT5ConnectionManager(mt5_api=mock_api)
        obs = fx.MT5Observer(connection=conn)
        acc = obs.get_account_info()
        assert acc.balance == 100000.0
