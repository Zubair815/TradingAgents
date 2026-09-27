"""MetaTrader 5 Error Taxonomy (Phase 14).

Provides specific, actionable exceptions for MT5 connection, authorization,
symbol lookup, and data streaming operations.
"""

from __future__ import annotations


class MT5Error(Exception):
    """Base exception for all MetaTrader 5 operations."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if self.code is not None:
            return f"[{self.code}] {self.message}"
        return self.message


class MT5NotInstalledError(MT5Error):
    """Raised when the official MetaTrader5 Python package is not installed."""

    def __init__(self, message: str = "MetaTrader5 package is not installed. Install via: pip install 'tradingagents[mt5]' or pip install MetaTrader5."):
        super().__init__(message)


class MT5ConnectionError(MT5Error):
    """Raised when connection to the MetaTrader 5 terminal fails."""
    pass


class MT5TerminalNotFoundError(MT5ConnectionError):
    """Raised when the MT5 terminal executable path cannot be located."""
    pass


class MT5AuthorizationError(MT5ConnectionError):
    """Raised when broker account login credentials or server connection fails."""
    pass


class MT5SymbolError(MT5Error):
    """Raised when an instrument symbol is not found or cannot be selected."""
    pass


class MT5DataError(MT5Error):
    """Raised when querying candles, ticks, account info, or positions fails."""
    pass
