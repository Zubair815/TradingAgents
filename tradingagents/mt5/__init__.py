"""MetaTrader 5 Integration Package (Phase 14).

Exports the read-only MetaTrader 5 adapter:
- ``MT5ConnectionManager``: Lifecycle connection management, authentication, diagnostics.
- ``MT5Observer``: Read-only inspection of accounts, symbols, market ticks, multi-timeframe candles, positions, and orders.
- Domain models: ``MT5AccountInfo``, ``MT5SymbolInfo``, ``MT5Tick``, ``MT5Position``, ``MT5Order``, ``MT5Deal``.
- Error taxonomy: ``MT5Error``, ``MT5ConnectionError``, ``MT5AuthorizationError``, etc.
"""

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
    MT5Deal,
    MT5Order,
    MT5Position,
    MT5SymbolInfo,
    MT5Tick,
)
from tradingagents.mt5.observer import MT5Observer

__all__ = [
    # Connection
    "MT5ConnectionManager",
    "MT5ConnectionStatus",
    # Observer
    "MT5Observer",
    # Models
    "MT5AccountInfo",
    "MT5SymbolInfo",
    "MT5Tick",
    "MT5Position",
    "MT5Order",
    "MT5Deal",
    # Errors
    "MT5Error",
    "MT5NotInstalledError",
    "MT5ConnectionError",
    "MT5TerminalNotFoundError",
    "MT5AuthorizationError",
    "MT5SymbolError",
    "MT5DataError",
]
