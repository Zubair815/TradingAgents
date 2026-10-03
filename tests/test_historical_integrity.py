"""Phase 30: source boundaries, completed-bar timing and truthful claims."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
)
from tradingagents.backtest.agent_backtester import (
    AgentBacktestConfig,
    HistoricalForexAgentBacktester,
)
from tradingagents.backtest.forex_engine import ForexBacktestConfig
from tradingagents.backtest.historical_data import (
    HistoricalDataUnavailable,
    candle_frame,
    load_historical_candles,
)
from tradingagents.backtest.historical_pipeline import (
    HistoricalForexPipelineConfig,
    create_historical_forex_pipeline,
)
from tradingagents.dataflows.forex_context import historical_market_context, historical_market_scope
from tradingagents.dataflows.forex_data import ForexBar, fetch_forex_candles
from tradingagents.dataflows.forex_quality import DataInsufficientError, utc_timestamp
from tradingagents.forex.conversion import AvailabilityStatus, ForexConversionRate
from tradingagents.graph.forex_graph import create_forex_risk_evaluator
from tradingagents.risk.engine import ForexRiskLimits
from web.server import app


def sourced_bars(n=4, minutes=60):
    """Mock adapter records for boundary tests; never advertised as a live dataset."""
    start = datetime(2025, 1, 6, tzinfo=timezone.utc)
    meta = {"source": "MT5", "canonical_symbol": "EURUSD", "symbol": "EURUSD.a",
            "timeframe": "H1" if minutes == 60 else "M15", "execution_market": True,
            "retrieved_at_utc": "2025-02-01T00:00:00Z"}
    bars = [ForexBar(timestamp=start+timedelta(minutes=i*minutes), open=1.08,
                     high=1.082, low=1.079, close=1.081,
                     close_time=start+timedelta(minutes=(i+1)*minutes), is_closed=True,
                     data_source="MT5") for i in range(n)]
    return bars, meta


def historical_run(bars, meta, callback=lambda *args: None, **kwargs):
    config = AgentBacktestConfig(timeframe=meta.get("timeframe", "H1"))
    return HistoricalForexAgentBacktester(config=config).run(
        bars, market_data_provenance=meta, agent_pipeline_callable=callback, **kwargs)


@pytest.mark.parametrize("source", ["unknown", "synthetic", "demo", "generated", "random"])
def test_synthetic_or_unverified_candles_rejected(source):
    bars, meta = sourced_bars()
    bars = [replace(b, data_source=source) for b in bars]
    with pytest.raises(HistoricalDataUnavailable):
        historical_run(bars, meta)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "ordering", "geometry", "future", "incomplete", "timeframe", "missing", "pair"])
def test_invalid_historical_data_rejected(mutation):
    bars, meta = sourced_bars()
    if mutation == "empty":
        bars = []
    elif mutation == "duplicate":
        bars[1] = bars[0]
    elif mutation == "ordering":
        bars.reverse()
    elif mutation == "geometry":
        bars[0] = replace(bars[0], high=1.0)
    elif mutation == "future":
        bars[-1] = replace(bars[-1], timestamp=datetime.now(timezone.utc)+timedelta(days=2))
    elif mutation == "incomplete":
        bars[-1] = replace(bars[-1], is_closed=False)
    elif mutation == "timeframe":
        meta["timeframe"] = "M15"
    elif mutation == "missing":
        del bars[1]
    else:
        meta["canonical_symbol"] = "GBPUSD"
    with pytest.raises(DataInsufficientError):
        historical_run(bars, meta)


def test_missing_provenance_and_missing_pipeline_fail():
    bars, meta = sourced_bars()
    with pytest.raises(HistoricalDataUnavailable):
        historical_run(bars, {})
    with pytest.raises(HistoricalDataUnavailable, match="pipeline"):
        HistoricalForexAgentBacktester().run(bars, market_data_provenance=meta)


def test_analysis_receives_closed_bars_and_cannot_fetch_future():
    bars, meta = sourced_bars()
    seen = []
    def callback(pair, cutoff, history):
        assert all(b.close_time <= cutoff for b in history)
        frame = fetch_forex_candles(pair, "H1", as_of="2099-01-01T00:00:00Z")
        assert frame.close_time.max() <= cutoff
        assert len(frame) == len(history)
        seen.append(cutoff)
    with patch("tradingagents.dataflows.forex_data._fetch_yahoo_candles", side_effect=AssertionError("live fetch")):
        report = historical_run(bars, meta, callback)
    assert seen == [b.close_time for b in bars]
    assert historical_market_context() is None
    assert report.validated_strategy_performance is False
    assert report.result.validated_strategy_performance is False
    assert report.validation_status == "PARTIALLY_VALIDATED"
    assert report.market_data_provenance["actual_end"] == bars[-1].close_time.isoformat()


def test_historical_fills_at_observed_close_not_prior_open():
    bars, meta = sourced_bars()
    def callback(pair, cutoff, history):
        if len(history) == 1:
            return ForexTraderProposal(pair=pair, action=ForexAction.LONG, stop_loss=1.07,
                                       take_profit_1=1.10, suggested_lot_size=0.1, reasoning="Close-time execution test")
    report = historical_run(bars, meta, callback)
    trade = report.result.trades[0]
    assert trade.entry_time == bars[0].close_time
    assert trade.entry_price > bars[0].close  # spread and slippage
    assert trade.entry_price != bars[0].open


def test_real_yahoo_fixture_runs_historical_pipeline():
    fixture = json.loads((Path(__file__).parent / "fixtures/eurusd_yahoo_historical.json").read_text())
    bars = [ForexBar(timestamp=utc_timestamp(t), open=o, high=h, low=low, close=c,
                     close_time=utc_timestamp(t)+timedelta(days=1), is_closed=True, data_source="Yahoo")
            for t, o, h, low, c in fixture["rows"]]
    callback = MagicMock(return_value=None)
    report = historical_run(bars, fixture["provenance"], callback)
    assert callback.call_count == 4
    data = report.to_dict()
    assert data["market_data_source"] == "Yahoo"
    assert data["market_data_provenance"]["execution_market"] is False
    for key in ("analysis_cutoff_policy", "analysts", "provider", "quick_model", "deep_model",
                "strategy_version", "prompt_system_version", "execution_assumptions", "validation_reasons"):
        assert key in data
    assert "Validated:** `True`" not in report.markdown_report


def test_provider_failure_never_reaches_demo_generator():
    client = TestClient(app)
    client.get("/")
    with (patch("tradingagents.backtest.historical_data.fetch_forex_candles", side_effect=RuntimeError("secret provider URL")),
          patch("web.forex_routes.math.sin", side_effect=AssertionError("synthetic generation")),
          patch("web.forex_routes.ForexTradingAgentsGraph") as graph):
        response = client.post("/api/forex/backtest/run", json={
            "mode": "HISTORICAL_AGENT_BACKTEST", "demo_mode": True,
            "date_from": "2025-01-06", "date_to": "2025-01-07"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HISTORICAL_DATA_UNAVAILABLE"
    assert "secret" not in response.text
    graph.assert_not_called()


@pytest.mark.parametrize("candles", [[], [{"open": 1.0}]])
def test_historical_api_rejects_unverified_user_data(candles):
    client = TestClient(app)
    client.get("/")
    response = client.post("/api/forex/backtest/run", json={"mode": "HISTORICAL_AGENT_BACKTEST", "candles": candles})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HISTORICAL_DATA_UNAVAILABLE"


def test_demo_remains_illustrative():
    client = TestClient(app)
    client.get("/")
    response = client.post("/api/forex/backtest/run", json={"demo_mode": True, "count": 20})
    assert response.status_code == 200
    data = response.json()
    assert data["data_source"] == "synthetic"
    assert data["validation_status"] == "DEMO"
    assert data["validated_strategy_performance"] is False
    assert data["result"]["validated_strategy_performance"] is False


def test_loader_disables_fallback_and_requires_full_coverage():
    bars, meta = sourced_bars()
    frame = candle_frame(bars, meta)
    frame["close_time"] = [b.close_time for b in bars]
    frame["is_closed"] = True
    with patch("tradingagents.backtest.historical_data.fetch_forex_candles", return_value=frame) as fetch:
        loaded, provenance = load_historical_candles("EURUSD", "H1", "2025-01-06", "2025-01-06T04:00:00Z")
        assert len(loaded) == 4
        assert provenance["candle_count"] == 4
        assert fetch.call_args.kwargs["allow_fallback"] is False
        assert fetch.call_args.kwargs["count"] is None
        with pytest.raises(HistoricalDataUnavailable, match="not covered"):
            load_historical_candles("EURUSD", "H1", "2025-01-06", "2025-01-07")


def test_graph_errors_and_missing_calendar_are_not_successes():
    bars, meta = sourced_bars()
    graph = MagicMock()
    graph.run.side_effect = RuntimeError("pipeline failed")
    runner = HistoricalForexAgentBacktester(graph_factory=lambda: graph)
    with (patch("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", side_effect=DataInsufficientError("no archive")),
          pytest.raises(DataInsufficientError)):
        runner.run(bars, market_data_provenance=meta)
    graph.run.assert_not_called()
    with (patch("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", return_value=[]),
          pytest.raises(RuntimeError, match="pipeline failed")):
        runner.run(bars, market_data_provenance=meta)
    assert historical_market_context() is None


def test_unverified_macro_is_unavailable_without_fetching():
    from tradingagents.agents.utils.forex_macro_tools import _fetch_macro_safe
    bars, meta = sourced_bars()
    with (historical_market_scope("EURUSD", candle_frame(bars, meta), bars[0].close_time),
          patch("tradingagents.agents.utils.forex_macro_tools.route_to_vendor", side_effect=AssertionError("live macro"))):
        assert "MACRO_UNAVAILABLE" in _fetch_macro_safe("FEDFUNDS", "2025-01-06")


def test_news_observed_after_cutoff_cannot_be_requested_from_history(tmp_path):
    from tradingagents.dataflows.config import config_scope
    from tradingagents.dataflows.forex_archive import save_snapshot
    from tradingagents.dataflows.forex_news import ForexNewsArticle, fetch_forex_news
    bars, meta = sourced_bars()
    cutoff = bars[0].close_time
    article = ForexNewsArticle(headline="Later observed news", publisher="Fixture",
        published_at_utc=cutoff-timedelta(minutes=1), retrieved_at_utc=cutoff+timedelta(minutes=1),
        currencies=("USD",), relevance=1, url="https://example.com/news")
    save_snapshot(tmp_path, {"source": "Yahoo", "symbol": "EURUSD",
        "retrieved_at_utc": article.retrieved_at_utc.isoformat(), "records": [article.model_dump(mode="json")]})
    with (config_scope({"forex_news_archive_dir": str(tmp_path)}),
          historical_market_scope("EURUSD", candle_frame(bars, meta), cutoff)):
        for requested in (None, cutoff+timedelta(days=1)):
            with pytest.raises(DataInsufficientError, match="coverage"):
                fetch_forex_news("EURUSD", as_of=requested)


def test_calendar_later_revision_not_visible_even_if_future_requested(tmp_path):
    from tests.test_forex_phase2 import CUTOFF, adapter, record
    early = adapter(tmp_path)
    early.refresh("EURUSD", "2026-03-10", "2026-03-10")
    later = adapter(tmp_path, [record(Actual="9%", Previous="8%", Revised="2.9%",
                                     LastUpdate="2026-03-10T12:01:00")], now=CUTOFF+timedelta(minutes=2))
    later.refresh("EURUSD", "2026-03-10", "2026-03-10")
    bars, meta = sourced_bars()
    with historical_market_scope("EURUSD", candle_frame(bars, meta), CUTOFF):
        for requested in (None, CUTOFF+timedelta(minutes=3)):
            event = later.query("EURUSD", "2026-03-10", "2026-03-10", as_of=requested)[0]
            assert event.actual is None
            assert event.previous == 2.9


def test_graph_rejected_proposal_is_never_executed():
    bars, meta = sourced_bars()
    graph = MagicMock()
    graph.run.return_value = ({
        "forex_proposal": ForexTraderProposal(pair="EURUSD", action="LONG", reasoning="Test"),
        "forex_risk_decision": {"pair": "EURUSD", "decision": "REJECT", "original_action": "LONG",
                                "approved_action": "NO_TRADE", "approved_lot_size": 0,
                                "executive_rationale": "Historical risk gate"},
    }, "REJECT")
    with patch("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", return_value=[]):
        report = HistoricalForexAgentBacktester(graph_factory=lambda: graph).run(bars, market_data_provenance=meta)
    assert report.proposals_rejected == len(bars)
    assert report.result.total_trades == 0
    assert report.validated_strategy_performance is False


def test_historical_range_cannot_include_future_or_incomplete_bar():
    bars, meta = sourced_bars()
    config = AgentBacktestConfig(date_from="2025-01-06", date_to="2025-01-06T03:30:00Z")
    with pytest.raises(HistoricalDataUnavailable, match="cutoff"):
        HistoricalForexAgentBacktester(config).run(bars, market_data_provenance=meta, agent_pipeline_callable=lambda *args: None)


def test_verified_lower_timeframe_execution_remains_available():
    bars, meta = sourced_bars(2)
    lower, lower_meta = sourced_bars(8, minutes=15)
    with pytest.raises(HistoricalDataUnavailable, match="provenance"):
        historical_run(bars, meta, lower_tf_candles=lower)
    result = historical_run(bars, meta, lower_tf_candles=lower, lower_tf_provenance=lower_meta)
    assert result.analyses_performed == 2


def test_historical_graph_never_retrieves_future_learned_context():
    from tradingagents.graph.forex_graph import ForexTradingAgentsGraph
    from tradingagents.graph.propagation import Propagator
    graph = object.__new__(ForexTradingAgentsGraph)
    graph.config = {"historical_backtest": True}
    graph.learning_manager = MagicMock()
    graph.propagator = Propagator()
    state = graph.create_run_state("EURUSD", "2025-01-06T01:00:00Z")
    graph.learning_manager.retriever.retrieve_lessons.assert_not_called()
    assert state["applied_lesson_ids"] == []


def test_historical_memory_uses_only_lessons_known_by_cutoff():
    from tradingagents.graph.forex_graph import ForexTradingAgentsGraph
    from tradingagents.graph.propagation import Propagator

    graph = object.__new__(ForexTradingAgentsGraph)
    graph.config = {"historical_backtest": True, "historical_memory_enabled": True}
    graph.journal = None
    graph.learning_manager = MagicMock()
    graph.propagator = Propagator()
    past = SimpleNamespace(
        lesson=SimpleNamespace(lesson_id="lesson-a", created_at="2025-01-01T00:00:00Z")
    )
    future = SimpleNamespace(
        lesson=SimpleNamespace(lesson_id="lesson-b", created_at="2025-03-01T00:00:00Z")
    )
    graph.learning_manager.retriever.retrieve_lessons.return_value = [past, future]
    graph.learning_manager.retriever.format_lessons_for_prompt.return_value = "lesson-a"

    state = graph.create_run_state("EURUSD", "2025-02-01T00:00:00Z")

    assert state["applied_lesson_ids"] == ["lesson-a"]
    formatted = graph.learning_manager.retriever.format_lessons_for_prompt.call_args.args[0]
    assert formatted == [past]


def test_historical_pipeline_receives_evolving_account_snapshot():
    bars, meta = sourced_bars(3)

    class SnapshotAgent:
        def __init__(self):
            self.snapshots = []
            self.deterministic_snapshots = []
            self.calls = 0

        def set_account_snapshot(self, snapshot):
            self.snapshots.append(snapshot)

        def set_deterministic_snapshot(self, account, positions, conversions, cutoff):
            self.deterministic_snapshots.append((account, positions, conversions, cutoff))

        def __call__(self, pair, _cutoff, _history):
            self.calls += 1
            if self.calls == 1:
                return ForexTraderProposal(
                    pair=pair,
                    action="LONG",
                    order_type="MARKET",
                    stop_loss=1.0700,
                    take_profit_1=1.1000,
                    suggested_lot_size=1.0,
                    reasoning="Account evolution regression",
                )
            return None

    agent = SnapshotAgent()
    config = AgentBacktestConfig(
        timeframe="H1",
        backtest_config=ForexBacktestConfig(initial_balance=10000.0, leverage=500.0),
    )
    HistoricalForexAgentBacktester(config=config).run(
        bars,
        market_data_provenance=meta,
        agent_pipeline_callable=agent,
    )

    assert agent.snapshots[0].balance == 10000.0
    assert agent.snapshots[0].leverage == 500.0
    assert agent.snapshots[1].balance == agent.snapshots[0].balance
    assert agent.snapshots[1].equity < agent.snapshots[0].equity
    assert agent.snapshots[1].free_margin <= agent.snapshots[1].equity
    second_account, second_positions, _, second_cutoff = agent.deterministic_snapshots[1]
    assert second_account.used_margin > 0.0
    assert second_account.free_margin < second_account.equity
    assert len(second_positions) == 1
    assert second_positions[0].risk_amount > 0.0
    assert second_positions[0].position_id
    assert second_cutoff == bars[1].close_time


def test_historical_cross_trade_fails_when_conversion_is_unavailable():
    bars, meta = sourced_bars(2)
    meta.update(canonical_symbol="EURJPY", symbol="EURJPY.a")
    bars = [
        replace(bar, open=160.0, high=160.2, low=159.8, close=160.0)
        for bar in bars
    ]

    def proposal(pair, _cutoff, _history):
        return ForexTraderProposal(
            pair=pair,
            action="LONG",
            order_type="MARKET",
            stop_loss=159.0,
            take_profit_1=162.0,
            suggested_lot_size=0.1,
            reasoning="Missing conversion must fail closed",
        )

    config = AgentBacktestConfig(pair="EURJPY", timeframe="H1", max_analysis_points=1)
    with pytest.raises(HistoricalDataUnavailable, match="JPY->USD"):
        HistoricalForexAgentBacktester(config=config).run(
            bars,
            market_data_provenance=meta,
            agent_pipeline_callable=proposal,
        )


def test_historical_conversion_snapshot_excludes_future_observations():
    bars, meta = sourced_bars(2)
    meta.update(canonical_symbol="USDJPY", symbol="USDJPY.a")
    bars = [
        replace(bar, open=150.0, high=150.2, low=149.8, close=150.0)
        for bar in bars
    ]
    start = bars[0].timestamp
    past = ForexConversionRate(
        from_currency="USD",
        to_currency="JPY",
        status=AvailabilityStatus.AVAILABLE,
        rate=150.0,
        conversion_path=("USDJPY",),
        source="historical fixture",
        observed_at=start,
    )
    future = past.model_copy(update={"rate": 175.0, "observed_at": start + timedelta(hours=4)})

    class Observer:
        def __init__(self):
            self.snapshots = []

        def set_deterministic_snapshot(self, _account, _positions, conversions, cutoff):
            self.snapshots.append((conversions, cutoff))

        def __call__(self, *_args):
            return None

    observer = Observer()
    config = AgentBacktestConfig(
        pair="USDJPY",
        timeframe="H1",
        conversion_rates=(past, future),
    )
    HistoricalForexAgentBacktester(config=config).run(
        bars,
        market_data_provenance=meta,
        agent_pipeline_callable=observer,
    )

    assert observer.snapshots
    for conversions, cutoff in observer.snapshots:
        assert conversions
        assert all(item.observed_at <= cutoff for item in conversions)
        assert future not in conversions


def test_historical_context_uses_shared_live_risk_sizing_math():
    bars, meta = sourced_bars(2)
    meta.update(canonical_symbol="USDJPY", symbol="USDJPY.a")
    bars = [
        replace(
            bar,
            open=150.0,
            high=150.2,
            low=149.8,
            close=150.0,
            spread_pips=2.0,
            broker_symbol="USDJPY.a",
        )
        for bar in bars
    ]
    conversion = ForexConversionRate(
        from_currency="USD",
        to_currency="JPY",
        status=AvailabilityStatus.AVAILABLE,
        rate=150.0,
        conversion_path=("USDJPY.a",),
        source="historical fixture",
        observed_at=bars[0].timestamp,
    )
    captured = {}

    class Graph:
        def run(self, **_kwargs):
            proposal = ForexTraderProposal(
                pair="USDJPY",
                action=ForexAction.NO_TRADE,
                reasoning="Context capture",
            )
            decision = ForexRiskDecision(
                pair="USDJPY",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.NO_TRADE,
                approved_action=ForexAction.NO_TRADE,
                executive_rationale="No trade",
            )
            return {
                "forex_proposal": proposal.model_dump(),
                "forex_risk_decision": decision.model_dump(),
            }, "NO_TRADE"

    def graph_factory(_config, _account, _memory, risk_context):
        captured["historical"] = risk_context
        return Graph()

    pipeline = create_historical_forex_pipeline(
        HistoricalForexPipelineConfig(pair="USDJPY", timeframe="H1"),
        meta,
        graph_factory=graph_factory,
    )
    config = AgentBacktestConfig(
        pair="USDJPY",
        timeframe="H1",
        max_analysis_points=1,
        conversion_rates=(conversion,),
    )
    with patch(
        "tradingagents.backtest.historical_pipeline.TradingEconomicsCalendar.query",
        return_value=[],
    ):
        HistoricalForexAgentBacktester(config=config).run(
            bars,
            market_data_provenance=meta,
            agent_pipeline_callable=pipeline,
        )

    historical_context = captured["historical"]
    assert historical_context.as_of_utc == bars[0].close_time
    assert historical_context.conversions == (conversion,)
    assert historical_context.market.observed_at <= historical_context.as_of_utc

    live_equivalent = historical_context.model_copy(deep=True)
    proposal = ForexTraderProposal(
        pair="USDJPY",
        action=ForexAction.LONG,
        entry_price=150.0,
        stop_loss=149.5,
        take_profit_1=151.0,
        suggested_risk_percent=1.0,
        reasoning="Parity check",
    )
    results = []
    for context in (historical_context, live_equivalent):
        holder = {}
        evaluator = create_forex_risk_evaluator(
            risk_context=context,
            risk_limits=ForexRiskLimits(
                enforce_market_open=False,
                enforce_news_blackout=False,
                max_spread_pips=5.0,
            ),
            sizing_result_holder=holder,
        )
        evaluator(
            {
                "company_of_interest": "USDJPY",
                "trade_date": context.as_of_utc.isoformat(),
                "forex_proposal": proposal.model_dump(),
            }
        )
        results.append(holder["latest"])

    assert results[0] == results[1]
    assert results[0].pip_value_per_lot == pytest.approx(6.67, abs=0.01)


# ---------------------------------------------------------------------------
# Point-in-Time Integrity & Provenance Tagging Tests (Phase 5: TIME-006, DATA-008, NEWS-004)
# ---------------------------------------------------------------------------


class TestPointInTimeIntegrityAndProvenance:
    """Verifies that external objects carry immutable UTC provenance and never leak past cutoffs."""

    def test_forex_bar_provenance_tagging(self):
        opened = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
        closed = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
        retrieved = datetime(2026, 10, 2, 11, 0, 5, tzinfo=timezone.utc)
        bar = ForexBar(
            timestamp=opened,
            open=1.0850,
            high=1.0880,
            low=1.0840,
            close=1.0875,
            volume=1250.0,
            close_time=closed,
            is_closed=True,
            data_source="MT5",
            broker="XM-Demo",
            broker_symbol="EURUSD",
            retrieved_at_utc=retrieved,
        )
        assert bar.timestamp.tzinfo is not None
        assert bar.data_source == "MT5"
        assert bar.broker == "XM-Demo"
        assert bar.retrieved_at_utc == retrieved
        assert bar.close_time == closed

    def test_forex_news_article_provenance_and_pit(self):
        from tradingagents.dataflows.forex_news import ForexNewsArticle

        published = datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc)
        retrieved = datetime(2026, 10, 2, 8, 35, tzinfo=timezone.utc)
        article = ForexNewsArticle(
            headline="Eurozone CPI in line with forecasts",
            publisher="Reuters",
            published_at_utc=published,
            retrieved_at_utc=retrieved,
            currencies=("EUR", "USD"),
            relevance=1.0,
            url="https://example.com/news/1",
            source="Yahoo",
        )
        assert article.published_at_utc == published
        assert article.retrieved_at_utc == retrieved
        assert article.source == "Yahoo"
        assert article.published_at_utc <= article.retrieved_at_utc

    def test_broker_event_provenance_tagging(self):
        from tradingagents.research.contracts import BrokerEvent, canonical_json

        occurred = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
        observed = datetime(2026, 10, 2, 12, 0, 1, tzinfo=timezone.utc)
        event = BrokerEvent(
            broker="XM",
            account_ref="demo_12345",
            external_id="deal_9999",
            event_type="DEAL_ADD",
            occurred_at_utc=occurred,
            observed_at_utc=observed,
            payload_json=canonical_json({"ticket": 9999, "action": "BUY", "volume": 0.5}),
        )
        assert event.broker == "XM"
        assert event.occurred_at_utc == occurred
        assert event.observed_at_utc == observed
        assert event.occurred_at_utc <= event.observed_at_utc

    def test_cache_point_in_time_safety_filter(self):
        import pandas as pd

        from tradingagents.dataflows.forex_data import filter_candles_by_cutoff

        # 3 H1 candles: 10:00-11:00, 11:00-12:00, 12:00-13:00
        dates = [
            datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        ]
        df = pd.DataFrame({
            "Date": dates,
            "Open": [1.080, 1.082, 1.084],
            "High": [1.083, 1.085, 1.087],
            "Low": [1.079, 1.081, 1.083],
            "Close": [1.082, 1.084, 1.086],
            "Volume": [100, 150, 200],
        })

        # Cutoff at 11:30: only the 10:00-11:00 candle completed by 11:30
        cutoff_1130 = datetime(2026, 10, 2, 11, 30, tzinfo=timezone.utc)
        filtered = filter_candles_by_cutoff(df, as_of=cutoff_1130, timeframe="H1")
        assert len(filtered) == 1
        assert filtered.iloc[0]["Date"] == dates[0]
        assert bool(filtered.iloc[0]["is_closed"]) is True

        # Cutoff at 12:00: both 10:00 and 11:00 completed
        cutoff_1200 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        filtered_12 = filter_candles_by_cutoff(df, as_of=cutoff_1200, timeframe="H1")
        assert len(filtered_12) == 2

    def test_economic_event_publication_time_pit_safety(self):
        from tradingagents.forex.calendar import EconomicEvent, EventImpact

        # Event scheduled/known at 08:00 UTC, released at 14:00:05 UTC
        event = EconomicEvent(
            event_id="us_cpi_20261002",
            currency="USD",
            title="CPI m/m",
            impact=EventImpact.HIGH,
            date="2026-10-02",
            time_utc="14:00",
            actual=0.4,
            forecast=0.3,
            previous=0.2,
            published_at_utc=datetime(2026, 10, 2, 14, 0, 5, tzinfo=timezone.utc),
            known_at_utc=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
            revision_at_utc=datetime(2026, 10, 2, 14, 30, 0, tzinfo=timezone.utc),
            revised_previous=0.25,
            previous_before_revision=0.2,
        )

        # Before publication (14:00:00): not released
        assert not event.is_released_as_of("2026-10-02", "14:00:00")
        clamped_before = event.clamp_to_as_of("2026-10-02", "14:00:00")
        assert clamped_before.actual is None
        assert clamped_before.previous == 0.2

        # After publication (14:00:10): released, but revision hasn't occurred yet
        assert event.is_released_as_of("2026-10-02", "14:00:10")
        clamped_after = event.clamp_to_as_of("2026-10-02", "14:00:10")
        assert clamped_after.actual == 0.4
        assert clamped_after.revised_previous is None  # Revision at 14:30 masked
        assert clamped_after.previous == 0.2

        # After revision (14:35:00): revision is now visible
        clamped_revised = event.clamp_to_as_of("2026-10-02", "14:35:00")
        assert clamped_revised.actual == 0.4
        assert clamped_revised.revised_previous == 0.25
