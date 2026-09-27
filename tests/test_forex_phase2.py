"""Phase 2 feed contracts; all providers are explicit deterministic test doubles."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from tradingagents.dataflows.config import config_scope
from tradingagents.dataflows.forex_archive import read_snapshots, save_snapshot
from tradingagents.dataflows.forex_data import (
    fetch_forex_candles,
    filter_candles_by_cutoff,
    resample_candles,
)
from tradingagents.dataflows.forex_news import ForexNewsArticle, fetch_forex_news
from tradingagents.dataflows.forex_quality import (
    DataInsufficientError,
    validate_candles,
    validate_quote,
)
from tradingagents.dataflows.trading_economics import TradingEconomicsCalendar, _number
from tradingagents.forex.calendar import EconomicEvent, EventImpact, evaluate_event_risk_regime
from tradingagents.forex.domain import Timeframe
from tradingagents.forex.sessions import TradingSession, is_market_open, session_window

UTC = timezone.utc
CUTOFF = datetime(2026, 3, 10, 12, tzinfo=UTC)


def candles():
    return pd.DataFrame({
        "Date": pd.date_range("2026-03-10T08:00Z", periods=5, freq="1h"),
        "Open": [1.08] * 5, "High": [1.09] * 5, "Low": [1.07] * 5,
        "Close": [1.085] * 5, "Volume": [100] * 5,
    })


def record(**changes):
    row = {"CalendarId": "42", "Country": "United States", "Event": "CPI",
           "Date": "2026-03-10T12:00:00", "DateSpan": 0, "LastUpdate": "2026-03-10T11:59:00",
           "Importance": 3, "Actual": "", "Forecast": "3.0%", "Previous": "2.9%",
           "Revised": "", "Unit": "%", "SourceURL": "https://www.bls.gov/cpi/"}
    row.update(changes)
    return row


def adapter(tmp_path, rows=None, now=CUTOFF):
    response = Mock(status_code=200)
    response.json.return_value = [record()] if rows is None else rows
    session = Mock()
    session.get.return_value = response
    client = TradingEconomicsCalendar(api_key="test-key", cache_dir=tmp_path, session=session, clock=lambda: now)
    return client


def test_cutoff_uses_close_not_open_and_resampling_drops_partial():
    selected = filter_candles_by_cutoff(candles(), CUTOFF, Timeframe.H1)
    assert list(selected.Date.dt.hour) == [8, 9, 10, 11]
    assert selected.is_closed.all()
    resampled = resample_candles(candles(), Timeframe.H4)
    assert len(resampled) == 1
    assert resampled.Date.iloc[0].hour == 8


@pytest.mark.parametrize("mutation,detail", [
    (lambda df: df.iloc[[1, 0, 2, 3, 4]], "ordering"),
    (lambda df: pd.concat([df, df.iloc[-1:]]), "duplicate"),
    (lambda df: df.drop(index=2), "gap"),
    (lambda df: df.assign(Open=0), "zero or negative"),
    (lambda df: df.assign(High=1.079), "geometry"),
    (lambda df: df.assign(Low=1.10), "High < Low"),
    (lambda df: df.assign(Close=float("inf")), "invalid Close"),
    (lambda df: df.assign(Volume=-1), "negative volume"),
    (lambda df: df.assign(spread_pips=99), "abnormal spread"),
    (lambda df: df.assign(Date=df.Date.dt.tz_localize(None)), "timezone"),
    (lambda df: df.assign(Date=df.Date + pd.Timedelta(minutes=5)), "alignment"),
    (lambda df: df.assign(Date=df.Date + pd.Timedelta(days=3650)), "future"),
])
def test_bad_candles_are_rejected(mutation, detail):
    with pytest.raises(DataInsufficientError, match=detail):
        validate_candles(mutation(candles()), Timeframe.H1, as_of=CUTOFF, max_spread_pips=5)


def test_weekend_gap_allowed_but_stale_weekend_data_rejected():
    frame = candles().iloc[:2].copy()
    frame.Date = pd.to_datetime(["2026-03-13T20:00Z", "2026-03-15T21:00Z"])
    validate_candles(frame, Timeframe.H1)
    with pytest.raises(DataInsufficientError, match="stale"):
        validate_candles(candles(), Timeframe.H1, as_of="2026-03-14T12:00Z", max_age_seconds=7200)


@pytest.mark.parametrize("bid,ask,stamp,detail", [
    (0, 1.08, CUTOFF, "prices"), (1.08, 1.07, CUTOFF, "prices"),
    (1.08, 1.081, CUTOFF, "spread"),
    (1.08, 1.0801, CUTOFF-timedelta(seconds=31), "stale"),
    (1.08, 1.0801, CUTOFF+timedelta(seconds=1), "future"),
])
def test_quote_quality(bid, ask, stamp, detail):
    with pytest.raises(DataInsufficientError, match=detail):
        validate_quote(bid, ask, stamp, now=CUTOFF)


def test_mt5_default_and_explicit_labeled_fallback(monkeypatch):
    from tradingagents.mt5.connection import MT5ConnectionError
    observer = Mock()
    observer.get_candles.side_effect = MT5ConnectionError("offline")
    fallback = Mock(return_value=candles())
    monkeypatch.setattr("tradingagents.dataflows.forex_data._fetch_yahoo_candles", fallback)
    with config_scope({}):
        with pytest.raises(DataInsufficientError, match="no fallback"):
            fetch_forex_candles("EURUSD", observer=observer, as_of=CUTOFF)
        fallback.assert_not_called()
        frame = fetch_forex_candles("EURUSD", observer=observer, as_of=CUTOFF, allow_fallback=True)
        assert frame.attrs["fallback"] is True
        assert frame.attrs["fallback_reason"] == "MT5ConnectionError"
        observer.get_candles.side_effect = DataInsufficientError("corrupt broker data")
        with pytest.raises(DataInsufficientError, match="corrupt"):
            fetch_forex_candles("EURUSD", observer=observer, as_of=CUTOFF, allow_fallback=True)
        assert fallback.call_count == 1


def test_required_timeframe_failure_propagates(monkeypatch):
    from tradingagents.dataflows.forex_data import fetch_multi_timeframe_data
    mock = Mock(side_effect=[candles(), DataInsufficientError("offline")])
    monkeypatch.setattr("tradingagents.dataflows.forex_data.fetch_forex_candles", mock)
    with pytest.raises(DataInsufficientError, match="required timeframe H4"):
        fetch_multi_timeframe_data("EURUSD", timeframes=("H1", "H4"), as_of=CUTOFF)


def test_calendar_live_request_archives_source_and_masks_unpublished(tmp_path):
    client = adapter(tmp_path)
    event = client.query("EURUSD", "2026-03-10", "2026-03-10")[0]
    assert event.actual is None and event.forecast == 3
    assert event.source == "Trading Economics"
    assert event.known_at_utc == CUTOFF
    assert event.source_url == "https://www.bls.gov/cpi/"
    assert "united%20states" in client.session.get.call_args.args[0]
    assert client.session.get.call_args.kwargs["params"]["c"] == "test-key"
    assert len(list(read_snapshots(tmp_path))) == 1
    assert "test-key" not in next(tmp_path.glob("*.json")).read_text()


def test_calendar_versions_use_observation_time_not_release_date(tmp_path):
    early = adapter(tmp_path)
    early.refresh("EURUSD", "2026-03-10", "2026-03-10")
    late_time = CUTOFF + timedelta(minutes=2)
    late = adapter(tmp_path, [record(Actual="3.2%", Forecast="3.1%", Previous="3.0%",
                                     Revised="2.9%", LastUpdate="2026-03-10T12:01:00")], now=late_time)
    late.refresh("EURUSD", "2026-03-10", "2026-03-10")
    early_event = late.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF)[0]
    assert early_event.actual is None and early_event.forecast == 3.0
    assert early_event.previous == 2.9
    event = late.query("EURUSD", "2026-03-10", "2026-03-10", as_of=late_time)[0]
    assert event.actual == 3.2 and event.previous == 3.0
    assert event.previous_before_revision == 2.9
    assert event.revision_at_utc == CUTOFF + timedelta(minutes=1)
    with pytest.raises(DataInsufficientError, match="snapshot"):
        late.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF-timedelta(seconds=1))


def test_missing_or_stale_calendar_never_reports_clear(tmp_path):
    client = adapter(tmp_path, [])
    with pytest.raises(DataInsufficientError):
        client.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF)
    client.session.get.assert_not_called()
    assert client.query("EURUSD", "2026-03-10", "2026-03-10") == []
    with pytest.raises(DataInsufficientError):
        client.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF+timedelta(minutes=16))
    with pytest.raises(DataInsufficientError):
        client.query("EURUSD", "2026-03-09", "2026-03-10", as_of=CUTOFF)


@pytest.mark.parametrize("changes", [{"LastUpdate": "2027-01-01"}, {"SourceURL": ""},
                                     {"Country": "unknown"}, {"Importance": 9}, {"Date": "broken"}])
def test_invalid_calendar_records_never_establish_coverage(tmp_path, changes):
    client = adapter(tmp_path, [record(**changes)])
    with pytest.raises(DataInsufficientError):
        client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    assert not list(tmp_path.glob("*.json"))


def test_duplicate_calendar_ids_and_archive_tampering(tmp_path):
    client = adapter(tmp_path, [record(), record()])
    with pytest.raises(DataInsufficientError, match="duplicate"):
        client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    client.session.get.return_value.json.return_value = [record()]
    client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    path = next(tmp_path.glob("*.json"))
    path.write_text(path.read_text().replace("CPI", "modified"))
    with pytest.raises(DataInsufficientError, match="snapshot"):
        client.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF)


def test_calendar_retry_and_credentials_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.dataflows.trading_economics.time.sleep", lambda _: None)
    client = adapter(tmp_path)
    valid = client.session.get.return_value
    client.session.get.side_effect = [Mock(status_code=429), Mock(status_code=503), valid]
    client.query("EURUSD", "2026-03-10", "2026-03-10")
    assert client.session.get.call_count == 3
    client.session.get.reset_mock()
    client.session.get.side_effect = requests.RequestException("secret credential url")
    with pytest.raises(DataInsufficientError) as exc:
        client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    assert "secret" not in str(exc.value)
    assert client.session.get.call_count == 3
    client.session.get.reset_mock()
    client.session.get.side_effect = None
    client.session.get.return_value = Mock(status_code=401)
    with pytest.raises(DataInsufficientError, match="401"):
        client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    assert client.session.get.call_count == 1


def test_numbers_have_consistent_units():
    assert _number("0.2M", "K") == 200
    assert _number("180K", "K") == 180
    assert _number("3.2%", "%") == 3.2
    assert _number("N/A", "%") is None


def test_publication_timestamp_required_and_midnight_is_conservative():
    event = EconomicEvent("x", "USD", "CPI", EventImpact.HIGH, "2026-03-10", actual=3.2)
    assert event.clamp_to_as_of("2026-03-11").actual is None
    from dataclasses import replace
    event = replace(event, published_at_utc=CUTOFF)
    assert event.clamp_to_as_of("2026-03-10").actual is None
    assert event.clamp_to_as_of(CUTOFF).actual == 3.2


def test_calendar_failure_rejects_risk_proposal(monkeypatch):
    from tests.test_forex_risk_engine import make_valid_long_proposal
    from tradingagents.risk.engine import ForexRiskEngine
    monkeypatch.setattr("tradingagents.risk.engine.evaluate_event_risk_regime",
                        Mock(side_effect=DataInsufficientError("calendar offline")))
    result = ForexRiskEngine().validate_proposal(make_valid_long_proposal(), curr_date="2026-03-10", curr_time_utc="12:00")
    assert result.approved_action.value == "NO_TRADE"
    assert any("DATA_INSUFFICIENT" in item for item in result.risk_violations)


def test_unknown_release_time_blocks_even_with_other_upcoming_event(monkeypatch):
    events = [EconomicEvent("1", "USD", "Unknown time", EventImpact.HIGH, "2026-03-10"),
              EconomicEvent("2", "USD", "Tomorrow", EventImpact.HIGH, "2026-03-11", "12:00")]
    monkeypatch.setattr("tradingagents.forex.calendar.get_calendar_events_for_pair", lambda **kw: events)
    risk = evaluate_event_risk_regime("EURUSD", "2026-03-10", "12:00")
    assert risk.timing_blackout is True and risk.hours_to_next_high_impact == 24


def test_news_dedup_and_observation_cutoff(tmp_path):
    original = ForexNewsArticle(headline="Fed holds rates!", publisher="Test", url="https://example.com/1",
        published_at_utc=CUTOFF-timedelta(hours=1), retrieved_at_utc=CUTOFF,
        currencies=("USD",), relevance=0.5)
    duplicate = original.model_copy(update={"url": "https://example.com/2", "headline": "Fed holds rates"})
    future = original.model_copy(update={"url": "https://example.com/3", "headline": "Later item",
                                          "retrieved_at_utc": CUTOFF+timedelta(hours=1)})
    save_snapshot(tmp_path, {"source": "Yahoo", "symbol": "EURUSD", "retrieved_at_utc": CUTOFF.isoformat(),
                            "records": [x.model_dump(mode="json") for x in (original, duplicate, future)]})
    with config_scope({"forex_news_archive_dir": str(tmp_path)}):
        result = fetch_forex_news("EURUSD", as_of=CUTOFF)
        assert len(result) == 1 and result[0].publisher == "Test"
        with pytest.raises(DataInsufficientError, match="coverage"):
            fetch_forex_news("EURUSD", as_of=CUTOFF-timedelta(seconds=1))


@pytest.mark.parametrize("day,ny,london,sydney", [
    (date(2026, 1, 15), 13, 8, 21), (date(2026, 7, 15), 12, 7, 22),
    (date(2026, 3, 10), 12, 8, 21), (date(2026, 10, 28), 12, 8, 21),
])
def test_sessions_follow_each_regions_dst(day, ny, london, sydney):
    assert session_window(TradingSession.NEW_YORK, day)[0].hour == ny
    assert session_window(TradingSession.LONDON, day)[0].hour == london
    assert session_window(TradingSession.SYDNEY, day)[0].hour == sydney


def test_market_weekend_dst_boundaries():
    assert is_market_open(datetime(2026, 3, 13, 20, 59, tzinfo=UTC))
    assert not is_market_open(datetime(2026, 3, 13, 21, tzinfo=UTC))
    assert is_market_open(datetime(2026, 1, 16, 21, 59, tzinfo=UTC))
    assert not is_market_open(datetime(2026, 1, 16, 22, tzinfo=UTC))


def test_shared_cutoff_pins_model_requests():
    from tradingagents.dataflows.forex_context import resolve_forex_cutoff
    assert resolve_forex_cutoff("2026-03-10", "2026-03-10", CUTOFF.isoformat()) == CUTOFF
    assert resolve_forex_cutoff("2026-03-11", "2026-03-10", CUTOFF.isoformat()) == CUTOFF
    assert resolve_forex_cutoff("2026-03-09", "2026-03-10", CUTOFF.isoformat()).hour == 0
    assert resolve_forex_cutoff(trade_date="2026-03-10").hour == 0


def test_tools_receive_same_intraday_cutoff(monkeypatch):
    from tradingagents.agents.utils.forex_news_tools import get_forex_economic_calendar
    from tradingagents.agents.utils.forex_tools import get_forex_candles_tool
    market = Mock(return_value=candles())
    calendar = Mock(return_value=[])
    monkeypatch.setattr("tradingagents.agents.utils.forex_tools.fetch_forex_candles", market)
    monkeypatch.setattr("tradingagents.agents.utils.forex_news_tools.get_calendar_events_for_pair", calendar)
    args = {"symbol": "EURUSD", "trade_date": "2026-03-10", "forex_as_of_utc": CUTOFF.isoformat()}
    get_forex_candles_tool.invoke(args)
    get_forex_economic_calendar.invoke({**args, "curr_date": "2026-03-10"})
    assert market.call_args.kwargs["as_of"] == calendar.call_args.kwargs["curr_date"] == CUTOFF


def test_live_context_requires_calendar_but_tolerates_optional_news(monkeypatch):
    from tradingagents.dataflows.forex_context import prepare_live_forex_context
    refresh = Mock()
    news = Mock(side_effect=DataInsufficientError("news offline"))
    monkeypatch.setattr(TradingEconomicsCalendar, "refresh", refresh)
    monkeypatch.setattr("tradingagents.dataflows.forex_news.fetch_forex_news", news)
    assert datetime.fromisoformat(prepare_live_forex_context("EURUSD")).tzinfo is not None
    refresh.side_effect = DataInsufficientError("calendar offline")
    with pytest.raises(DataInsufficientError, match="calendar offline"):
        prepare_live_forex_context("EURUSD")


def test_timestamped_archive_import(tmp_path):
    client = adapter(tmp_path / "source")
    client.refresh("EURUSD", "2026-03-10", "2026-03-10")
    archive = next((tmp_path / "source").glob("*.json"))
    destination = adapter(tmp_path / "destination")
    destination.import_snapshot(archive)
    result = destination.query("EURUSD", "2026-03-10", "2026-03-10", as_of=CUTOFF)
    assert result[0].known_at_utc == CUTOFF
    assert destination.session.get.call_count == 0
    future = adapter(tmp_path / "future", now=CUTOFF-timedelta(seconds=1))
    with pytest.raises(DataInsufficientError, match="archive import"):
        future.import_snapshot(archive)


def test_live_news_discards_undated_unattributed_and_irrelevant(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    raw = {"title": "Fed holds rates", "publisher": "Test publisher", "link": "https://example.com/news",
           "providerPublishTime": (now-timedelta(hours=1)).timestamp()}
    rows = [raw, {**raw, "title": "Fed holds rates!", "link": "https://example.com/duplicate"},
            {**raw, "publisher": "Unknown"}, {**raw, "providerPublishTime": None},
            {**raw, "providerPublishTime": (now+timedelta(days=1)).timestamp()},
            {**raw, "title": "Local sports team wins"}, None]
    ticker = Mock()
    ticker.get_news.return_value = rows
    monkeypatch.setattr("yfinance.Ticker", lambda _: ticker)
    with config_scope({"forex_news_archive_dir": str(tmp_path)}):
        items = fetch_forex_news("EURUSD")
        assert len(items) == 1
        assert items[0].currencies == ("USD",)
        with pytest.raises(DataInsufficientError):
            fetch_forex_news("EURUSD", as_of=now-timedelta(hours=2))


def test_mt5_metadata_suffix_and_point_precision():
    from tests.test_mt5 import MockMT5API, MockSymbolInfoData
    from tradingagents.mt5.connection import MT5ConnectionManager
    from tradingagents.mt5.observer import MT5Observer
    api = MockMT5API()
    original = api.symbol_info
    api.symbol_info = lambda symbol: MockSymbolInfoData(name=symbol, digits=4, point=0.0001) if symbol == "EURUSDm" else original(symbol)
    api.symbol_select = Mock(return_value=True)
    rates = api.copy_rates_from
    api.copy_rates_from = Mock(side_effect=rates)
    observer = MT5Observer(MT5ConnectionManager(mt5_api=api))
    with config_scope({"forex_broker_symbols": {"EURUSD": "EURUSDm"}}):
        bars = observer.get_candles("EURUSD", "H1", 3, CUTOFF)
    assert api.copy_rates_from.call_args.args[0] == "EURUSDm"
    assert all(bar.is_closed and bar.close_time <= CUTOFF for bar in bars)
    assert bars[0].spread_pips == 12  # Four-digit broker: one point is one pip.
    assert bars[0].data_source == "MT5" and bars[0].broker_symbol == "EURUSDm"


def test_bad_candles_propagate_through_indicator_pipeline():
    from tradingagents.dataflows.forex_data import MultiTimeframeData
    from tradingagents.forex.indicators import compute_multi_timeframe_indicators
    frame = pd.concat([candles(), candles()])
    bundle = MultiTimeframeData(symbol="EURUSD", candles={Timeframe.H1: frame})
    with pytest.raises(DataInsufficientError, match="duplicate"):
        compute_multi_timeframe_indicators(bundle)


def test_graph_preserves_intraday_cutoff_and_legacy_date_field():
    from types import SimpleNamespace

    from tradingagents.graph.forex_graph import ForexTradingAgentsGraph
    from tradingagents.graph.propagation import Propagator

    graph = SimpleNamespace(propagator=Propagator())
    state = ForexTradingAgentsGraph.create_run_state(graph, "EURUSD", "2026-03-10T17:00:00+05:00")
    assert state["trade_date"] == "2026-03-10"
    assert state["forex_as_of_utc"] == CUTOFF.isoformat()
    historical = ForexTradingAgentsGraph.create_run_state(graph, "EURUSD", "2026-03-10")
    assert historical["forex_as_of_utc"] == "2026-03-10T00:00:00+00:00"


def test_risk_graph_node_uses_observation_cutoff(monkeypatch):
    from tradingagents.agents.schemas_forex import ForexAction, ForexTraderProposal
    from tradingagents.graph.forex_graph import create_forex_risk_evaluator
    from tradingagents.risk.engine import ForexRiskEngine

    engine = ForexRiskEngine()
    validate = Mock(wraps=engine.validate_proposal)
    monkeypatch.setattr(engine, "validate_proposal", validate)
    proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
    node = create_forex_risk_evaluator(risk_engine=engine)
    node({"company_of_interest": "EURUSD", "trade_date": "2026-03-10",
          "forex_as_of_utc": CUTOFF.isoformat(), "forex_proposal": proposal.model_dump()})
    assert validate.call_args.kwargs["curr_date"] == CUTOFF.isoformat()
