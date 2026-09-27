"""Forex domain model — core types.

This module defines the first-class Forex domain objects that distinguish a
Forex trading system from a generic equity platform.  Every downstream module
(data fetching, analysis, risk, journal) imports from here rather than using
ad-hoc strings.

Design principles:
- No network calls; all values are either static or supplied by caller.
- Immutable (frozen dataclasses / enums) so accidental mutation in agents is
  impossible.
- Deterministic: pip sizes, point sizes, and digit counts are facts, not LLM
  output.
- Backward-compatible: AssetType.STOCK and AssetType.CRYPTO are added here so
  the enum is the single source; existing code that passes "stock" / "crypto"
  strings continues to work via the helpers at the bottom of this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# AssetType — replaces ad-hoc "stock" / "crypto" string literals
# ---------------------------------------------------------------------------


class AssetType(str, Enum):
    """Asset class.  Use string comparison (``asset_type == "forex"``) or the
    enum member (``asset_type == AssetType.FOREX``) interchangeably because the
    enum inherits from ``str``."""

    STOCK = "stock"
    CRYPTO = "crypto"
    FOREX = "forex"

    @classmethod
    def from_string(cls, value: str) -> "AssetType":
        """Parse case-insensitive string → AssetType.  Raises ValueError if unknown."""
        normalised = (value or "").strip().lower()
        for member in cls:
            if member.value == normalised:
                return member
        raise ValueError(
            f"Unknown asset type {value!r}. Valid values: "
            + ", ".join(m.value for m in cls)
        )


# ---------------------------------------------------------------------------
# Timeframe — first-class typed object instead of arbitrary text
# ---------------------------------------------------------------------------


class Timeframe(str, Enum):
    """Canonical timeframe identifiers.

    The enum value is the standard MT5/TradingView label.  Additional
    attributes expose the duration in seconds and the yfinance interval string
    for each frame.
    """

    M1  = "M1"
    M5  = "M5"
    M15 = "M15"
    M30 = "M30"
    H1  = "H1"
    H4  = "H4"
    D1  = "D1"
    W1  = "W1"

    @property
    def seconds(self) -> int:
        """Candle duration in seconds."""
        _map = {
            "M1":  60,
            "M5":  300,
            "M15": 900,
            "M30": 1800,
            "H1":  3600,
            "H4":  14400,
            "D1":  86400,
            "W1":  604800,
        }
        return _map[self.value]

    @property
    def minutes(self) -> int:
        """Candle duration in minutes."""
        return self.seconds // 60

    @property
    def yfinance_interval(self) -> str:
        """yfinance ``interval`` parameter string for this timeframe.

        yfinance intraday data is limited to the last 60 days for 1m and
        730 days for 1h.  D1 uses '1d'.
        """
        _map = {
            "M1":  "1m",
            "M5":  "5m",
            "M15": "15m",
            "M30": "30m",
            "H1":  "1h",
            "H4":  "1h",   # yfinance has no 4h; callers must resample from 1h
            "D1":  "1d",
            "W1":  "1wk",
        }
        return _map[self.value]

    @property
    def mt5_timeframe(self) -> int:
        """MetaTrader 5 TIMEFRAME_* integer constant.

        These values match the MT5 Python API constants exactly.  The import of
        the ``MetaTrader5`` package is deferred to the mt5 adapter so this
        module remains importable without MT5 installed.
        """
        _map = {
            "M1":  1,
            "M5":  5,
            "M15": 15,
            "M30": 30,
            "H1":  16385,
            "H4":  16388,
            "D1":  16408,
            "W1":  32769,
        }
        return _map[self.value]

    @property
    def is_intraday(self) -> bool:
        """True for timeframes shorter than D1."""
        return self.seconds < 86400

    @classmethod
    def from_string(cls, value: str) -> "Timeframe":
        """Parse case-insensitive string → Timeframe."""
        normalised = (value or "").strip().upper()
        for member in cls:
            if member.value == normalised:
                return member
        raise ValueError(
            f"Unknown timeframe {value!r}. Valid values: "
            + ", ".join(m.value for m in cls)
        )




# ---------------------------------------------------------------------------
# ForexPair — instrument-level Forex metadata
# ---------------------------------------------------------------------------

# ISO-4217 currency codes that appear in standard retail Forex pairs.
_FOREX_CURRENCIES: frozenset[str] = frozenset({
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    "CNY", "CNH", "HKD", "SGD", "SEK", "NOK", "DKK", "PLN",
    "MXN", "ZAR", "TRY", "INR", "KRW", "BRL", "RUB", "THB",
    "CZK", "HUF", "ILS",
})

# JPY-quoted pairs use 3 decimal places (2 pips); all others use 5 decimal
# places (4 pips).
_JPY_QUOTES: frozenset[str] = frozenset({"JPY", "HUF", "KRW", "CLP", "IDR"})

# Pairs where the convention is inverted (USD/xxx rather than xxx/USD).
_USD_BASE_PAIRS: frozenset[str] = frozenset({"USDJPY", "USDCHF", "USDCAD"})


@dataclass(frozen=True)
class ForexPair:
    """Canonical descriptor for one Forex instrument.

    Instances are immutable by design.  The factory function
    :func:`get_forex_pair` is the primary way to obtain one.

    Attributes
    ----------
    symbol          Canonical 6-letter symbol, e.g. ``EURUSD``.
    base_currency   The currency being bought/sold, e.g. ``EUR``.
    quote_currency  The currency used for pricing, e.g. ``USD``.
    broker_symbol   Broker-native symbol, e.g. ``EURUSDm``.
                    Defaults to ``symbol`` when not supplied.
    digits          Decimal places quoted by the broker (typically 5 or 3).
    pip_size        Size of one pip as a price movement,
                    e.g. 0.0001 for EURUSD, 0.01 for USDJPY.
    point           Smallest price increment (1 / 10^digits).
                    One pip = 10 points for 5-digit brokers.
    contract_size   Notional value of 1.0 standard lot in base currency.
                    100 000 for standard FX pairs.
    """

    symbol:         str
    base_currency:  str
    quote_currency: str
    broker_symbol:  str        = field(default="")
    digits:         int        = field(default=0)   # 0 = auto-derive from quote currency
    pip_size:       float      = field(default=0.0)   # set by __post_init__
    point:          float      = field(default=0.0)   # set by __post_init__
    contract_size:  float      = field(default=100_000.0)

    def __post_init__(self) -> None:
        # Frozen dataclass cannot use self.x = ..., must use object.__setattr__
        sym = self.symbol.upper()
        object.__setattr__(self, "symbol", sym)
        base = self.base_currency.upper()
        object.__setattr__(self, "base_currency", base)
        quote = self.quote_currency.upper()
        object.__setattr__(self, "quote_currency", quote)
        broker = self.broker_symbol if self.broker_symbol else sym
        # Preserve broker_symbol case as supplied — XM uses lowercase 'm' suffix
        # (EURUSDm), not EURUSDM.
        object.__setattr__(self, "broker_symbol", broker)

        # Derive pip_size and point from digits if not explicitly supplied.
        if self.digits == 0:
            # Determine digits from quote currency if not supplied.
            d = 3 if quote in _JPY_QUOTES else 5
            object.__setattr__(self, "digits", d)

        d = self.digits
        point = 10 ** (-d)
        object.__setattr__(self, "point", point)

        if self.pip_size == 0.0:
            # Standard pip convention for retail Forex:
            # - 5-digit brokers (EURUSD): pip = 0.0001 = 10^(-4) = 10^(-(digits-1))
            # - 3-digit brokers (USDJPY): pip = 0.01  = 10^(-2) = 10^(-(digits-1))
            # General formula: pip_size = 10 ** (-(digits - 1))
            ps = round(10.0 ** (-(d - 1)), max(0, d - 1))
            object.__setattr__(self, "pip_size", ps)

    @property
    def yahoo_symbol(self) -> str:
        """Yahoo Finance symbol for this pair (e.g. ``EURUSD=X``)."""
        return f"{self.symbol}=X"

    @property
    def base(self) -> str:
        """Alias for base_currency."""
        return self.base_currency

    @property
    def quote(self) -> str:
        """Alias for quote_currency."""
        return self.quote_currency

    @property
    def is_jpy_pair(self) -> bool:
        """True when the quote currency is JPY (or another low-decimal currency)."""
        return self.quote_currency in _JPY_QUOTES

    @property
    def display_name(self) -> str:
        """Human-readable form: ``EUR/USD``."""
        return f"{self.base_currency}/{self.quote_currency}"


    def __str__(self) -> str:
        return self.symbol

    def __repr__(self) -> str:
        return (
            f"ForexPair('{self.symbol}', digits={self.digits}, "
            f"pip={self.pip_size}, broker='{self.broker_symbol}')"
        )


# ---------------------------------------------------------------------------
# Pair catalogue — covers all major, minor, and common exotic pairs.
# Callers can extend or override this via :class:`ForexSymbolMap`.
# ---------------------------------------------------------------------------

def _pair(sym: str, broker: str = "", digits: int = 0) -> ForexPair:
    """Convenience constructor for the built-in catalogue."""
    base, quote = sym[:3].upper(), sym[3:].upper()
    return ForexPair(
        symbol=sym.upper(),
        base_currency=base,
        quote_currency=quote,
        broker_symbol=broker or sym.upper(),
        digits=digits,
    )


# Major pairs — highest liquidity, tightest spreads
MAJOR_PAIRS: dict[str, ForexPair] = {
    "EURUSD": _pair("EURUSD", digits=5),
    "GBPUSD": _pair("GBPUSD", digits=5),
    "USDJPY": _pair("USDJPY", digits=3),
    "USDCHF": _pair("USDCHF", digits=5),
    "AUDUSD": _pair("AUDUSD", digits=5),
    "USDCAD": _pair("USDCAD", digits=5),
    "NZDUSD": _pair("NZDUSD", digits=5),
}

# Minor pairs (crosses — no USD)
MINOR_PAIRS: dict[str, ForexPair] = {
    "EURGBP": _pair("EURGBP", digits=5),
    "EURJPY": _pair("EURJPY", digits=3),
    "EURCHF": _pair("EURCHF", digits=5),
    "EURAUD": _pair("EURAUD", digits=5),
    "EURCAD": _pair("EURCAD", digits=5),
    "EURNZD": _pair("EURNZD", digits=5),
    "GBPJPY": _pair("GBPJPY", digits=3),
    "GBPCHF": _pair("GBPCHF", digits=5),
    "GBPAUD": _pair("GBPAUD", digits=5),
    "GBPCAD": _pair("GBPCAD", digits=5),
    "GBPNZD": _pair("GBPNZD", digits=5),
    "AUDJPY": _pair("AUDJPY", digits=3),
    "AUDCHF": _pair("AUDCHF", digits=5),
    "AUDCAD": _pair("AUDCAD", digits=5),
    "AUDNZD": _pair("AUDNZD", digits=5),
    "CADJPY": _pair("CADJPY", digits=3),
    "CADCHF": _pair("CADCHF", digits=5),
    "CHFJPY": _pair("CHFJPY", digits=3),
    "NZDJPY": _pair("NZDJPY", digits=3),
    "NZDCHF": _pair("NZDCHF", digits=5),
    "NZDCAD": _pair("NZDCAD", digits=5),
}

# Common exotic pairs
EXOTIC_PAIRS: dict[str, ForexPair] = {
    "USDZAR": _pair("USDZAR", digits=5),
    "USDMXN": _pair("USDMXN", digits=5),
    "USDTRY": _pair("USDTRY", digits=5),
    "USDSEK": _pair("USDSEK", digits=5),
    "USDNOK": _pair("USDNOK", digits=5),
    "USDDKK": _pair("USDDKK", digits=5),
    "USDPLN": _pair("USDPLN", digits=5),
    "USDSGD": _pair("USDSGD", digits=5),
    "USDHKD": _pair("USDHKD", digits=5),
    "USDCNH": _pair("USDCNH", digits=5),
    "EURTRY": _pair("EURTRY", digits=5),
    "GBPTRY": _pair("GBPTRY", digits=5),
    "EURHUF": _pair("EURHUF", digits=3),
    "EURSEK": _pair("EURSEK", digits=5),
    "EURNOK": _pair("EURNOK", digits=5),
}

_ALL_PAIRS: dict[str, ForexPair] = {
    **MAJOR_PAIRS,
    **MINOR_PAIRS,
    **EXOTIC_PAIRS,
}

# Broker suffix patterns to strip when normalising (e.g. EURUSDm → EURUSD).
_BROKER_SUFFIX_RE = re.compile(
    r"^([A-Z]{6})(m|s|pro|mini|micro|eco|fix|raw|std|cent|nano)?$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_forex_pair(symbol: str) -> bool:
    """Return True when *symbol* looks like a valid Forex currency pair.

    Accepts both canonical forms (``EURUSD``) and broker forms (``EURUSDm``).
    Purely syntactic — no network calls.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    clean = strip_broker_suffix_simple(symbol)
    if len(clean) != 6:
        return False
    base, quote = clean[:3].upper(), clean[3:].upper()
    return base in _FOREX_CURRENCIES and quote in _FOREX_CURRENCIES


def strip_broker_suffix_simple(symbol: str) -> str:
    """Strip common broker suffixes from *symbol* and return the base 6-letter pair.

    Examples: ``EURUSDm`` → ``EURUSD``, ``GBPJPYpro`` → ``GBPJPY``.
    Returns the original string when no suffix is detected.
    """
    s = symbol.strip().upper()
    m = _BROKER_SUFFIX_RE.match(s)
    return m.group(1) if m else s


def get_forex_pair(symbol: str) -> Optional[ForexPair]:
    """Look up a :class:`ForexPair` from the built-in catalogue.

    Accepts canonical (``EURUSD``), broker (``EURUSDm``), or Yahoo
    (``EURUSD=X``) forms.  Returns ``None`` for unknown symbols rather than
    raising, so callers can decide how to handle gaps gracefully.
    """
    if not isinstance(symbol, str):
        return None
    # Strip Yahoo =X suffix
    s = symbol.strip().upper().rstrip("=X").rstrip("=")
    # Strip broker suffixes
    s = strip_broker_suffix_simple(s)
    return _ALL_PAIRS.get(s)


def register_custom_pair(pair: ForexPair) -> None:
    """Add or override a pair in the global catalogue at runtime.

    Use this to register broker-specific pairs (e.g. XAUUSD quoted as Forex
    by a CFD broker) or to supply correct contract sizes that differ from the
    standard 100 000 default.

    This mutates the module-level ``_ALL_PAIRS`` dict; call once at startup.
    """
    _ALL_PAIRS[pair.symbol] = pair


def normalize_forex_pair(symbol: str) -> str:
    """Normalize a Forex symbol into 6-character canonical uppercase format.

    Examples:
        'EUR/USD' -> 'EURUSD'
        'eurusdm' -> 'EURUSD'
        'EURUSD=X' -> 'EURUSD'
        'gbp_jpy' -> 'GBPJPY'
    """
    if not isinstance(symbol, str):
        return ""
    clean = symbol.strip().upper().replace("/", "").replace("_", "").replace("-", "")
    if clean.endswith("=X"):
        clean = clean[:-2]
    return strip_broker_suffix_simple(clean)
