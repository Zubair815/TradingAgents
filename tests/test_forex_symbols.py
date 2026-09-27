"""Unit tests for tradingagents.forex.symbols — symbol namespace mapping.

Phase 1 acceptance criteria verified:
✓ canonical_to_yahoo correctly maps EURUSD → EURUSD=X
✓ canonical_to_broker maps via registered XM alias
✓ broker_to_canonical strips m suffix correctly
✓ strip_broker_suffix wraps domain function correctly
✓ DEFAULT_SYMBOL_MAP resolves all three namespaces
✓ ForexSymbolMap.register() and resolve() work correctly
"""


from tradingagents.forex.symbols import (
    DEFAULT_SYMBOL_MAP,
    ForexSymbolMap,
    broker_to_canonical,
    canonical_to_broker,
    canonical_to_yahoo,
    strip_broker_suffix,
)

# ---------------------------------------------------------------------------
# DEFAULT_SYMBOL_MAP lookups
# ---------------------------------------------------------------------------

class TestDefaultSymbolMap:
    def test_canonical_lookup_eurusd(self):
        entry = DEFAULT_SYMBOL_MAP.get_by_canonical("EURUSD")
        assert entry is not None
        assert entry.canonical == "EURUSD"
        assert entry.yahoo == "EURUSD=X"

    def test_yahoo_lookup(self):
        entry = DEFAULT_SYMBOL_MAP.get_by_yahoo("EURUSD=X")
        assert entry is not None
        assert entry.canonical == "EURUSD"

    def test_broker_m_suffix_lookup(self):
        entry = DEFAULT_SYMBOL_MAP.get_by_broker("EURUSDm")
        assert entry is not None
        assert entry.canonical == "EURUSD"

    def test_resolve_canonical(self):
        e = DEFAULT_SYMBOL_MAP.resolve("GBPUSD")
        assert e is not None and e.canonical == "GBPUSD"

    def test_resolve_broker(self):
        e = DEFAULT_SYMBOL_MAP.resolve("GBPUSDm")
        assert e is not None and e.canonical == "GBPUSD"

    def test_resolve_yahoo(self):
        e = DEFAULT_SYMBOL_MAP.resolve("GBPUSD=X")
        assert e is not None and e.canonical == "GBPUSD"

    def test_resolve_unknown(self):
        assert DEFAULT_SYMBOL_MAP.resolve("FOOBAR") is None

    def test_canonical_symbols_sorted(self):
        syms = DEFAULT_SYMBOL_MAP.canonical_symbols()
        assert syms == sorted(syms)

    def test_covers_all_majors(self):
        from tradingagents.forex.domain import MAJOR_PAIRS
        for sym in MAJOR_PAIRS:
            entry = DEFAULT_SYMBOL_MAP.get_by_canonical(sym)
            assert entry is not None, f"{sym} missing from DEFAULT_SYMBOL_MAP"


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestCanonicalToYahoo:
    def test_eurusd_canonical(self):
        assert canonical_to_yahoo("EURUSD") == "EURUSD=X"

    def test_eurusd_broker(self):
        assert canonical_to_yahoo("EURUSDm") == "EURUSD=X"

    def test_already_yahoo(self):
        assert canonical_to_yahoo("EURUSD=X") == "EURUSD=X"

    def test_usdjpy(self):
        assert canonical_to_yahoo("USDJPY") == "USDJPY=X"

    def test_lowercase(self):
        assert canonical_to_yahoo("eurusd") == "EURUSD=X"


class TestCanonicalToBroker:
    def test_eurusd_default_m_suffix(self):
        result = canonical_to_broker("EURUSD")
        # Registered broker is EURUSDm (XM convention)
        assert result == "EURUSDm"

    def test_unknown_appends_suffix(self):
        result = canonical_to_broker("FOOBAR", suffix="m")
        assert result == "FOOBARm"


class TestBrokerToCanonical:
    def test_m_suffix_stripped(self):
        assert broker_to_canonical("EURUSDm") == "EURUSD"

    def test_pro_suffix_stripped(self):
        assert broker_to_canonical("GBPUSDpro") == "GBPUSD"

    def test_no_suffix(self):
        assert broker_to_canonical("EURUSD") == "EURUSD"

    def test_usdjpy_broker(self):
        assert broker_to_canonical("USDJPYm") == "USDJPY"


class TestStripBrokerSuffix:
    def test_m_suffix(self):
        assert strip_broker_suffix("EURUSDm") == "EURUSD"

    def test_no_suffix(self):
        assert strip_broker_suffix("EURUSD") == "EURUSD"


# ---------------------------------------------------------------------------
# ForexSymbolMap — custom registration
# ---------------------------------------------------------------------------

class TestForexSymbolMapCustom:
    def test_register_and_resolve(self):
        m = ForexSymbolMap()
        m.register("EURGBP", broker="EURGBPeco")
        entry = m.resolve("EURGBPeco")
        assert entry is not None
        assert entry.canonical == "EURGBP"

    def test_register_xm(self):
        m = ForexSymbolMap()
        m.register_xm("AUDCAD")
        entry = m.get_by_broker("AUDCADm")
        assert entry is not None
        assert entry.canonical == "AUDCAD"

    def test_canonical_symbols_empty_initially(self):
        m = ForexSymbolMap()
        assert m.canonical_symbols() == []

    def test_chaining(self):
        m = ForexSymbolMap()
        result = m.register("EURUSD").register("GBPUSD")
        assert result is m  # returns self

    def test_custom_yahoo_symbol(self):
        m = ForexSymbolMap()
        m.register("EURUSD", yahoo="EURUSD=X")
        entry = m.get_by_yahoo("EURUSD=X")
        assert entry is not None
