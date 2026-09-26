"""Regression coverage for audited precision, routing, and historical data failures."""

from io import StringIO
from unittest.mock import Mock

import pandas as pd
import pytest
import requests
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows import (
    alpha_vantage_common,
    alpha_vantage_fundamentals,
    alpha_vantage_indicator,
    alpha_vantage_stock,
    date_window,
    interface,
    market_data_validator,
    sec_edgar,
    stockstats_utils,
    y_finance,
    yfinance_news,
)
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorDataUnavailableError,
    VendorRateLimitError,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("close", [0.00001255, 1.12345])
def test_price_tools_preserve_small_prices_and_forex_pips(monkeypatch, close):
    data = pd.DataFrame({
        "Date": pd.to_datetime(["2026-09-23", "2026-09-24"]),
        "Open": [close * 0.99, close * 0.99],
        "High": [close * 1.01, close * 1.01],
        "Low": [close * 0.98, close * 0.98],
        "Close": [close, close],
        "Volume": [1000, 2000],
    })
    monkeypatch.setattr(market_data_validator, "load_ohlcv", lambda *a, **k: data)
    snapshot = market_data_validator.build_verified_market_snapshot(
        "TEST-USD", "2026-09-24", indicators=["atr"]
    )
    rows = [line.split("|") for line in snapshot.splitlines() if line.startswith("|")]
    rendered_close = next(float(row[2]) for row in rows if row[1].strip() == "Close")
    rendered_atr = next(float(row[2]) for row in rows if row[1].strip() == "atr")
    assert rendered_close == pytest.approx(close, rel=1e-10)
    assert rendered_atr == pytest.approx(close * 0.03, rel=1e-10)

    monkeypatch.setattr(y_finance.yf, "Ticker", lambda s: Mock(history=lambda **k: data.set_index("Date")))
    report = y_finance.get_YFin_data_online("TEST-USD", "2026-09-23", "2026-09-24")
    csv = pd.read_csv(StringIO(report), comment="#")
    assert csv["Close"].iloc[-1] == pytest.approx(close, rel=1e-10)


@pytest.mark.parametrize("payload", [
    "",
    "time,RSI\n",
    "other,RSI\n2026-09-24,45\n",
    "time,other\n2026-09-24,45\n",
    "time,RSI\n2020-01-01,45\n",
    "time,RSI\n2026-09-24,NaN\n",
    "time,RSI\n2026-09-24,not-a-number\n",
])
def test_alpha_unusable_indicator_tries_configured_fallback(monkeypatch, payload):
    monkeypatch.setattr(alpha_vantage_indicator, "_make_api_request", lambda *a, **k: payload)
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "alpha_vantage,yfinance")
    fallback = Mock(return_value="usable RSI")
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_indicators", {
        "alpha_vantage": alpha_vantage_indicator.get_indicator,
        "yfinance": fallback,
    })

    assert interface.route_to_vendor("get_indicators", "AAPL", "rsi", "2026-09-24", 5) == "usable RSI"
    fallback.assert_called_once_with("AAPL", "rsi", "2026-09-24", 5)


def test_alpha_price_window_with_no_rows_tries_fallback(monkeypatch):
    monkeypatch.setattr(alpha_vantage_stock, "_make_api_request", lambda *a, **k: "timestamp,close\n2020-01-01,100\n")
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "alpha_vantage,yfinance")
    fallback = Mock(return_value="usable prices")
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_stock_data", {
        "alpha_vantage": alpha_vantage_stock.get_stock,
        "yfinance": fallback,
    })
    assert interface.route_to_vendor("get_stock_data", "AAPL", "2026-09-23", "2026-09-24") == "usable prices"
    fallback.assert_called_once()


@pytest.mark.parametrize("payload", [
    '{"Error Message":"Invalid API call"}',
    '{"Information":"Service temporarily unavailable"}',
    "[]",
])
def test_alpha_error_response_is_never_a_successful_data_result(monkeypatch, payload):
    monkeypatch.setattr(alpha_vantage_common, "get_api_key", lambda: "test")
    monkeypatch.setattr(alpha_vantage_common, "get_scrubbed", lambda *a, **k: Mock(text=payload))
    with pytest.raises(VendorDataUnavailableError):
        alpha_vantage_common._make_api_request("OVERVIEW", {"symbol": "AAPL"})


@pytest.mark.parametrize("error", [YFRateLimitError(), requests.Timeout("timeout"), OSError("offline")])
def test_yahoo_request_failure_remains_a_vendor_outage(error):
    with pytest.raises(VendorDataUnavailableError) as caught:
        stockstats_utils.yf_retry(Mock(side_effect=error), max_retries=0)
    assert not isinstance(caught.value, NoMarketDataError)


def test_yahoo_insider_unreachable_is_not_a_missing_symbol(monkeypatch):
    monkeypatch.setattr(y_finance.yf, "Ticker", lambda s: Mock(insider_transactions=pd.DataFrame()))
    monkeypatch.setattr(y_finance, "vendor_reachable", lambda *a: False)
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "yfinance")
    out = interface.route_to_vendor("get_insider_transactions", "AAPL", "2026-09-24")
    assert out.startswith("DATA_UNAVAILABLE:")
    assert "unreachable" in out
    assert "delisted" not in out


@pytest.mark.parametrize("func,args,target", [
    (yfinance_news.get_news_yfinance, ("AAPL", "2026-09-23", "2026-09-24"), "Ticker"),
    (yfinance_news.get_global_news_yfinance, ("2026-09-24",), "Search"),
])
def test_news_preserves_typed_vendor_outages(monkeypatch, func, args, target):
    monkeypatch.setattr(yfinance_news.yf, target, Mock(side_effect=VendorRateLimitError("throttled")))
    with pytest.raises(VendorRateLimitError, match="throttled"):
        func(*args)


@pytest.mark.parametrize("module", [y_finance, alpha_vantage_fundamentals])
@pytest.mark.parametrize("method", ["get_balance_sheet", "get_income_statement", "get_cashflow"])
def test_historical_statements_without_filing_vintage_are_never_fetched(monkeypatch, module, method):
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-25")
    fetch = Mock(side_effect=AssertionError("historical current-vintage data must not be fetched"))
    if module is y_finance:
        monkeypatch.setattr(module.yf, "Ticker", fetch)
    else:
        monkeypatch.setattr(module, "_make_api_request", fetch)
    with pytest.raises(VendorDataUnavailableError, match="historical filing dates or revision vintages"):
        getattr(module, method)("AAPL", "quarterly", "2024-04-01")
    fetch.assert_not_called()


@pytest.mark.parametrize("module", [y_finance, alpha_vantage_fundamentals])
@pytest.mark.parametrize("method,property_name", [
    ("get_balance_sheet", "quarterly_balance_sheet"),
    ("get_income_statement", "quarterly_income_stmt"),
    ("get_cashflow", "quarterly_cashflow"),
])
@pytest.mark.parametrize("as_of", [None, "2026-09-25"])
def test_live_statements_still_return_available_values(monkeypatch, module, method, property_name, as_of):
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-25")
    if module is y_finance:
        frame = pd.DataFrame({pd.Timestamp("2026-06-30"): [123456]}, index=["Example value"])
        monkeypatch.setattr(module.yf, "Ticker", lambda *a: Mock(**{property_name: frame}))
    else:
        monkeypatch.setattr(module, "_make_api_request", lambda *a: '{"quarterlyReports":[{"fiscalDateEnding":"2026-06-30","value":"123456"}]}')
    assert "123456" in getattr(module, method)("AAPL", "quarterly", as_of)


def test_historical_statement_uses_explicit_sec_fallback_at_original_filing_value(monkeypatch):
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-25")
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "yfinance,sec_edgar")
    monkeypatch.setattr(y_finance.yf, "Ticker", Mock(side_effect=AssertionError("no live snapshot")))
    monkeypatch.setattr(sec_edgar, "cik_for", lambda *a: "0000320193")
    monkeypatch.setattr(sec_edgar, "_cached_json", lambda *a: {
        "facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"end": "2023-12-31", "filed": "2024-02-01", "val": 100_000_000},
            {"end": "2023-12-31", "filed": "2025-02-01", "val": 200_000_000},
        ]}}}}
    })
    out = interface.route_to_vendor("get_balance_sheet", "AAPL", "annual", "2024-04-01")
    assert "Total Assets,100" in out and "Total Assets,200" not in out
    assert "filed on or before 2024-04-01" in out


def test_no_historical_vendor_reports_unavailable_without_unconfigured_fallback(monkeypatch):
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-25")
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "yfinance,alpha_vantage")
    sec_fetch = Mock(side_effect=AssertionError("SEC must be explicitly configured"))
    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "sec_edgar", sec_fetch)
    out = interface.route_to_vendor("get_balance_sheet", "AAPL", "annual", "2024-04-01")
    assert out.startswith("DATA_UNAVAILABLE:")
    assert "historical" in out and "sec_edgar" in out and "delisted" not in out
    sec_fetch.assert_not_called()


def test_missing_filer_cannot_mask_an_unsupported_historical_vintage(monkeypatch):
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-25")
    monkeypatch.setattr(interface, "get_vendor", lambda *a: "yfinance,sec_edgar")
    monkeypatch.setattr(sec_edgar, "cik_for", lambda *a: None)
    out = interface.route_to_vendor("get_balance_sheet", "0700.HK", "annual", "2024-04-01")
    assert out.startswith("DATA_UNAVAILABLE:")
    assert "historical" in out and "delisted" not in out
