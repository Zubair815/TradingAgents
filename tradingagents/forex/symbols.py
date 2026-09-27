"""Forex symbol mapping — canonical ↔ broker ↔ Yahoo Finance.

The three symbol namespaces must never be conflated:

  canonical:   EURUSD      (6-letter ISO pair, used internally everywhere)
  broker:      EURUSDm     (broker-native; XM uses "m" suffix on many pairs)
  Yahoo:       EURUSD=X    (Yahoo Finance format for spot FX data)

This module maintains a bi-directional mapping between these namespaces so
any entry point can resolve any form to the right namespace without ad-hoc
string manipulation scattered around the codebase.

The mapping is designed to be extended at runtime (e.g. at startup from a
broker-specific configuration file) via :meth:`ForexSymbolMap.register`.

Existing behaviour of :func:`~tradingagents.dataflows.symbol_utils.normalize_symbol`
is preserved — that function continues to handle the EURUSD → EURUSD=X
conversion for yfinance.  This module is the layer that also tracks broker
aliases and allows reverse lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradingagents.forex.domain import _ALL_PAIRS, ForexPair, strip_broker_suffix_simple

# ---------------------------------------------------------------------------
# Symbol map entry
# ---------------------------------------------------------------------------


@dataclass
class SymbolEntry:
    """Three-namespace record for one Forex instrument."""
    canonical: str        # EURUSD
    broker:    str        # EURUSDm  (may equal canonical when no suffix)
    yahoo:     str        # EURUSD=X
    pair:      ForexPair | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# ForexSymbolMap — the primary registry
# ---------------------------------------------------------------------------

# Common XM broker suffixes — extend this list as needed.
_XM_SUFFIX = "m"

# Yahoo Finance suffix for spot FX.
_YAHOO_FX_SUFFIX = "=X"


class ForexSymbolMap:
    """Bi-directional registry mapping canonical ↔ broker ↔ Yahoo symbols.

    A singleton instance :data:`DEFAULT_SYMBOL_MAP` is pre-populated with
    all pairs in :data:`~tradingagents.forex.domain._ALL_PAIRS`.  Additional
    broker-specific entries can be registered at startup.

    Thread-safety: this map is written once at startup and read many times
    concurrently; no lock is needed assuming registration completes before
    the first lookup.
    """

    def __init__(self) -> None:
        # Maps indexed by each namespace for O(1) lookup.
        self._by_canonical: dict[str, SymbolEntry] = {}
        self._by_broker: dict[str, SymbolEntry] = {}
        self._by_yahoo: dict[str, SymbolEntry] = {}

    def register(
        self,
        canonical: str,
        broker: str | None = None,
        yahoo: str | None = None,
        pair: ForexPair | None = None,
    ) -> ForexSymbolMap:
        """Add or update an entry.  Returns self for chaining.

        All lookups for this symbol (in any of the three namespaces) will
        resolve to the registered ``canonical`` symbol after this call.
        """
        c = canonical.strip().upper()
        # Preserve broker symbol case: XM uses EURUSDm (lowercase m), not EURUSDM.
        # The _by_broker dict is keyed by upper-case for case-insensitive lookup,
        # but the stored entry.broker retains the original case.
        b_raw = (broker or c).strip()
        b_key = b_raw.upper()
        y = yahoo or f"{c}{_YAHOO_FX_SUFFIX}"
        entry = SymbolEntry(canonical=c, broker=b_raw, yahoo=y, pair=pair)
        self._by_canonical[c] = entry
        self._by_broker[b_key] = entry
        self._by_yahoo[y.upper()] = entry
        return self

    def register_xm(self, canonical: str, pair: ForexPair | None = None) -> ForexSymbolMap:
        """Convenience: register with XM's standard ``m`` suffix convention."""
        return self.register(
            canonical=canonical,
            broker=f"{canonical}{_XM_SUFFIX}",
            pair=pair,
        )

    # -----------------------------------------------------------------
    # Lookup methods
    # -----------------------------------------------------------------

    def get_by_canonical(self, symbol: str) -> SymbolEntry | None:
        """Look up by canonical symbol (e.g. ``EURUSD``)."""
        return self._by_canonical.get(symbol.strip().upper())

    def get_by_broker(self, symbol: str) -> SymbolEntry | None:
        """Look up by broker symbol (e.g. ``EURUSDm``).

        Also tries stripping the broker suffix and looking up the result as
        canonical, so ``EURUSDm`` resolves even if only ``EURUSD`` was
        registered.
        """
        s = symbol.strip().upper()
        entry = self._by_broker.get(s)
        if entry:
            return entry
        # Strip suffix and try canonical fallback.
        c = strip_broker_suffix_simple(s)
        return self._by_canonical.get(c)

    def get_by_yahoo(self, symbol: str) -> SymbolEntry | None:
        """Look up by Yahoo symbol (e.g. ``EURUSD=X``)."""
        return self._by_yahoo.get(symbol.strip().upper())

    def resolve(self, symbol: str) -> SymbolEntry | None:
        """Resolve *symbol* from any namespace.

        Tries canonical, then broker, then Yahoo in order.
        """
        s = symbol.strip().upper()
        entry = (
            self._by_canonical.get(s)
            or self._by_broker.get(s)
            or self._by_yahoo.get(s)
        )
        if entry:
            return entry
        # Attempt stripping broker suffix.
        c = strip_broker_suffix_simple(s)
        if c != s:
            return self._by_canonical.get(c)
        return None

    def canonical_symbols(self) -> list[str]:
        """All registered canonical symbols, sorted."""
        return sorted(self._by_canonical.keys())


# ---------------------------------------------------------------------------
# Default map — pre-populated from domain catalogue + XM aliases
# ---------------------------------------------------------------------------

DEFAULT_SYMBOL_MAP: ForexSymbolMap = ForexSymbolMap()

for _sym, _pair_obj in _ALL_PAIRS.items():
    DEFAULT_SYMBOL_MAP.register(
        canonical=_sym,
        broker=f"{_sym}m",   # XM standard: EURUSDm
        yahoo=f"{_sym}=X",
        pair=_pair_obj,
    )

del _sym, _pair_obj



# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def canonical_to_yahoo(symbol: str) -> str:
    """Convert a canonical or broker symbol to its Yahoo Finance form.

    Examples::

        canonical_to_yahoo("EURUSD")   # → "EURUSD=X"
        canonical_to_yahoo("EURUSDm")  # → "EURUSD=X"
        canonical_to_yahoo("EURUSD=X") # → "EURUSD=X"
    """
    entry = DEFAULT_SYMBOL_MAP.resolve(symbol)
    if entry:
        return entry.yahoo
    # Fallback: strip =X, strip broker suffix, re-add =X.
    s = symbol.strip().upper()
    if s.endswith("=X"):
        return s
    c = strip_broker_suffix_simple(s)
    return f"{c}=X"


def canonical_to_broker(symbol: str, suffix: str = "m") -> str:
    """Return the broker form of a canonical symbol.

    If the symbol is already registered in the map with a broker alias, that
    is returned.  Otherwise appends ``suffix`` (default ``"m"`` for XM).

    Examples::

        canonical_to_broker("EURUSD")  # → "EURUSDm"  (from XM map)
    """
    entry = DEFAULT_SYMBOL_MAP.resolve(symbol)
    if entry:
        return entry.broker
    c = strip_broker_suffix_simple(symbol.strip().upper())
    return f"{c}{suffix}"


def broker_to_canonical(broker_symbol: str) -> str:
    """Convert a broker symbol to its canonical form.

    Examples::

        broker_to_canonical("EURUSDm")   # → "EURUSD"
        broker_to_canonical("GBPJPYpro") # → "GBPJPY"
    """
    entry = DEFAULT_SYMBOL_MAP.get_by_broker(broker_symbol)
    if entry:
        return entry.canonical
    return strip_broker_suffix_simple(broker_symbol.strip().upper())


def strip_broker_suffix(symbol: str) -> str:
    """Public alias for :func:`~tradingagents.forex.domain.strip_broker_suffix_simple`."""
    return strip_broker_suffix_simple(symbol)
