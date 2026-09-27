# Forex Phase 2: sourced data and time integrity

## Implemented

| Area | Behavior |
| --- | --- |
| Market source | MT5 is the default. Yahoo requires explicit selection or explicit fallback permission. |
| Broker identity | Configured broker symbols, broker name, point/pip conversion, source and retrieval time accompany candles and quotes. |
| Candle timing | `Date` is the UTC open time. `close_time` and `is_closed` identify completed candles. Every requested timeframe shares one cutoff. Partial resampled buckets are excluded. |
| Quality checks | Reject invalid OHLC geometry, nonfinite/zero prices, duplicates, incorrect ordering, timezone omissions, future timestamps, unexpected gaps, stale candles/quotes and abnormal spreads. |
| Calendar | Trading Economics country/date API, bounded requests and retries, credential-safe errors, immutable source archives and validated timestamped imports. Synthetic calendar generation exists only in tests. |
| Historical availability | Actuals require publication time. Snapshot observation, publication and revision timestamps govern availability. A response downloaded today cannot establish what was known yesterday. |
| News | Structured publisher, URL, publication/retrieval timestamps, currencies, category and relevance; duplicate URLs/headlines removed; unknown/future timing excluded. |
| Sessions | IANA time zones for London, New York, Sydney and Tokyo, including weeks when regions change clocks on different dates. |
| Failure handling | Critical failures raise `DATA_INSUFFICIENT`; unavailable calendar coverage rejects directional proposals. Optional news gaps explicitly reduce confidence. |
| Graph integration | `forex_as_of_utc` pins candle, event and news tools and the risk evaluator to one observation time. Live runs collect calendar/news before freezing that time. |

## Configuration

Set `TRADING_ECONOMICS_API_KEY` in your environment or local `.env`, using a key
with economic-calendar access. `TE_API_KEY` is also accepted. Keys are never
stored in the calendar archives. Python scripts should load their `.env`
explicitly with `load_dotenv(".env")`; normal application startup already handles
its environment setup.

The MT5 terminal must be installed, connected to your broker, and have the
requested instrument available. This phase uses read-only observation methods.

```python
from tradingagents.dataflows.config import config_scope
from tradingagents.dataflows.forex_data import fetch_forex_candles

settings = {
    "forex_market_source": "mt5",
    "forex_allow_yahoo_fallback": False,
    "forex_broker_symbols": {"EURUSD": "EURUSDm"},  # Use your actual broker symbol.
    "forex_quote_max_age_seconds": 30,
    "forex_max_spread_pips": 5.0,
    "forex_calendar_max_age_seconds": 900,
    "forex_calendar_archive_dir": "cache/forex_calendar",
    "forex_news_archive_dir": "cache/forex_news",
}
with config_scope(settings):
    candles = fetch_forex_candles("EURUSD", "H1", count=100)
    print(candles.attrs)
```

The default candle age allowance is twice the requested timeframe. Override
`forex_candle_max_age_seconds` only when justified by the feed. Weekend closures
are permitted; data already stale during preceding open-market hours is rejected.
Four-hour MT5 bars retain the broker's UTC alignment, which can differ from
Yahoo's UTC resampling boundaries.

Yahoo can be selected with `source="yahoo"` or enabled as an availability
fallback with `allow_fallback=True`. Its metadata identifies `source="Yahoo"`
and `execution_market=False`. Corrupt MT5 data does not trigger fallback.

## Live and historical calendar usage

```python
from tradingagents.dataflows.trading_economics import TradingEconomicsCalendar

calendar = TradingEconomicsCalendar()
# Live: downloads when no recent, sufficiently broad snapshot exists.
events = calendar.query("EURUSD", "2026-09-27", "2026-10-04")

# Historical: only an archive actually observed by the cutoff can satisfy this.
events = calendar.query(
    "EURUSD", "2026-09-27", "2026-10-04",
    as_of="2026-09-27T12:30:00Z",
)
```

Use dates appropriate to the run. A graph call with no `trade_date` prepares live
calendar coverage from 31 days before today through 7 days ahead, collects
optional news, and freezes `forex_as_of_utc` after observation. An explicit
`trade_date="YYYY-MM-DD"` means **00:00 UTC**; an explicit ISO timestamp preserves
the intraday cutoff. Explicit historical runs do not fetch latest calendar/news
and relabel them as historical data.

The historical snapshot must cover every requested date and country and be no
older than the configured calendar freshness allowance at the cutoff. Missing
coverage is an error, including when no API key is configured. A validated empty
provider response can establish an empty calendar window; absence of a response
cannot.

### Timestamped local imports

`calendar.import_snapshot(path)` accepts the JSON envelope produced by
`refresh()`: `source`, `retrieved_at_utc`, `coverage_start`, `coverage_end`,
`countries`, and the original provider `records` list. It validates the provider,
coverage, event schema and observation time before saving a content-addressed copy.

Import only trusted collector archives. Preserve the collector's original UTC
observation time; do not backdate a new download. Hash checking detects modified
stored files; it does not authenticate a supplied historical timestamp. Latest
Trading Economics responses are not a substitute for a historical vintage archive.

## Verification and external requirements

Final local verification on 2026-09-27:

- Phase 2 regression tests: **50 passed**.
- Full repository suite: **1,714 passed, 5 skipped, 88 subtests passed**.
- The five skips cover Windows/POSIX mode differences, an optional AWS package,
  and an unavailable live DeepSeek key. The suite emitted 20 existing model warnings.
- New Phase 2 modules and tests pass Ruff. A comparison against the pre-change
  files found **zero introduced lint findings**; 188 pre-existing findings remain
  across the changed Python files.
- `python -m pip check`: **No broken requirements found**.

Regression coverage is in `tests/test_forex_phase2.py` plus the existing Forex
market-data, MT5, calendar, session, graph and risk suites. Provider tests use
explicit test doubles, including outage, retry, revision and historical-cutoff
scenarios. Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_forex_phase2.py
.venv\Scripts\python.exe -m pytest -q
```

The local readiness check found no Trading Economics key. The MT5 package is
installed, but its read-only terminal probe timed out with `MT5ConnectionError`
code `-10005`. Live verification therefore remains blocked on provider access
and a working MT5 terminal connection.

Live calendar verification requires the user's Trading Economics credentials.
Historical runs require archives collected at the relevant time or trusted
imports. Yahoo news coverage is partial and does not prove an absence of news.
Broker-specific holidays and daily/weekly candle boundaries should be confirmed
against the connected broker before relying on historical research results.

The web route now runs the real graph; failed or incomplete results remain failed.
Phase 0's demo-only web backtesting restriction remains in effect.
This phase does not claim completion of the later indicator, decision-authority,
execution, backtesting, or dashboard phases.

## Provider references

- [MT5 UTC bar-open semantics](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py)
- [MT5 current unfinished bar](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py)
- [Trading Economics calendar fields, LastUpdate and Revised](https://docs.tradingeconomics.com/economic_calendar/schema/)
- [Trading Economics country/date requests](https://docs.tradingeconomics.com/economic_calendar/country/)
