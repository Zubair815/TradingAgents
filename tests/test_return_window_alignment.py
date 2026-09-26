"""Settlement compares the same asset interval without using future benchmark bars."""

import pandas as pd
import pytest

import tradingagents.graph.trading_graph as tg

pytestmark = pytest.mark.unit


def _fetch(monkeypatch, stock, bench, trade_date="2026-01-05", holding_days=5):
    calls = []

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start, end):
            calls.append((self.symbol, start, end))
            return bench if self.symbol == "SPY" else stock

    monkeypatch.setattr(tg.yf, "Ticker", Ticker)
    monkeypatch.setattr(tg, "get_current_date", lambda: "2026-02-01")
    return tg.TradingAgentsGraph._fetch_returns(None, "BTC-USD", trade_date, holding_days, "SPY"), calls


def _prices(dates, values, tz=None):
    return pd.DataFrame({"Close": values}, index=pd.DatetimeIndex(dates).tz_localize(tz), dtype=float)


def test_crypto_weekend_end_uses_prior_benchmark_close(monkeypatch):
    stock = _prices(pd.date_range("2026-01-05", periods=6), [100, 101, 102, 103, 104, 105])
    bench = _prices(pd.bdate_range("2026-01-05", periods=6), [100, 101, 102, 103, 104, 130])
    (raw, alpha, days, resolved), _ = _fetch(monkeypatch, stock, bench)
    assert raw == pytest.approx(0.05)
    assert alpha == pytest.approx(0.01)
    assert (days, resolved) == (5, "2026-01-10")


def test_weekend_entry_fetches_and_uses_pre_entry_benchmark_price(monkeypatch):
    stock = _prices(pd.date_range("2026-01-10", periods=6), [100, 101, 102, 103, 104, 105])
    bench = _prices(["2026-01-09", "2026-01-12", "2026-01-15", "2026-01-16"], [100, 103, 104, 200])
    (raw, alpha, days, resolved), calls = _fetch(monkeypatch, stock, bench, "2026-01-10")
    assert raw == pytest.approx(0.05)
    assert alpha == pytest.approx(0.01)
    assert (days, resolved) == (5, "2026-01-15")
    assert calls[1][1] < "2026-01-09"


def test_different_holidays_and_timezones_keep_local_session_dates(monkeypatch):
    stock = _prices(["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"],
                    [100, 101, 102, 103, 104, 110], tz="Asia/Tokyo")
    bench = _prices(["2026-01-05", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-14"],
                    [100, 101, 102, 103, 105, 150], tz="America/New_York")
    (raw, alpha, days, resolved), _ = _fetch(monkeypatch, stock, bench)
    assert raw == pytest.approx(0.10)
    assert alpha == pytest.approx(0.05)
    assert (days, resolved) == (5, "2026-01-13")


@pytest.mark.parametrize("bad_price", [0, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("asset", ["stock", "benchmark"])
def test_invalid_endpoint_price_stays_pending(monkeypatch, bad_price, asset):
    stock = _prices(pd.date_range("2026-01-05", periods=6), [100] * 6)
    bench = stock.copy()
    (stock if asset == "stock" else bench).iloc[-1, 0] = bad_price
    result, _ = _fetch(monkeypatch, stock, bench)
    assert result == (None, None, None, None)


def test_benchmark_with_no_entry_price_or_stale_exit_stays_pending(monkeypatch):
    stock = _prices(pd.date_range("2026-01-05", periods=6), [100] * 6)
    for bench in [_prices(["2026-01-06", "2026-01-10"], [100, 105]),
                  _prices(["2025-12-31"], [100])]:
        result, _ = _fetch(monkeypatch, stock, bench)
        assert result == (None, None, None, None)


def test_unsorted_bars_are_sorted_before_counting_sessions(monkeypatch):
    prices = _prices(pd.date_range("2026-01-05", periods=6), [100, 101, 102, 103, 104, 105])
    (raw, alpha, days, resolved), _ = _fetch(monkeypatch, prices.iloc[::-1], prices.iloc[::-1])
    assert raw == pytest.approx(0.05)
    assert alpha == 0
    assert (days, resolved) == (5, "2026-01-10")
