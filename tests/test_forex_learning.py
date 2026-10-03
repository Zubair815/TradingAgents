"""Unit & Integration Tests for Forex Learning, Post-Trade Reflection & Lesson Retrieval (Phase 17)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason, TradeJournalRecord, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.learning.agent import ForexReflectionAgent
from tradingagents.learning.manager import ForexLearningManager
from tradingagents.learning.models import (
    ForexLesson,
    ReflectionContext,
    ReflectionRating,
    RetrievedLesson,
    TradeReflection,
)
from tradingagents.learning.retriever import LessonRetriever
from tradingagents.learning.store import ForexLessonStore
from tradingagents.metrics.models import (
    ExecutionQuality,
    SlippageType,
    TradeMfeMae,
    TradeOutcomeCategory,
    TradeOutcomeResult,
)
from web.forex_routes import router, set_forex_dependencies

# ===========================================================================
# 1. Model Instantiation & Serialization Tests
# ===========================================================================


def test_learning_models_and_enums():
    """Verify instantiation, serialization, and validation of learning domain models."""
    lesson = ForexLesson(
        pair="EURUSD",
        setup_type="TREND_CONTINUATION",
        outcome_category="PREMATURE_EXIT",
        rule_violated="Runner Discipline",
        observation="Closed at 0.5R; move reached 2.8R",
        root_cause="Impatience during consolidation",
        actionable_rule="Scale 50% at TP1, trail remaining volume.",
        confidence_score=0.95,
        tags=["premature_exit", "runner_discipline"],
    )
    assert lesson.pair == "EURUSD"
    assert lesson.confidence_score == 0.95
    assert "premature_exit" in lesson.tags

    reflection = TradeReflection(
        trade_id="trd_test_1",
        rating=ReflectionRating.EXCELLENT,
        summary="Optimal trade capture",
        what_went_well=["Target reached at liquidity crest"],
        lessons=[lesson],
        tags=["perfect_exit"],
    )
    assert reflection.rating == ReflectionRating.EXCELLENT
    assert len(reflection.lessons) == 1

    retrieved = RetrievedLesson(
        lesson=lesson,
        relevance_score=0.85,
        match_reasons=["Exact pair match", "Setup strategy match"],
    )
    assert retrieved.relevance_score == 0.85
    assert len(retrieved.match_reasons) == 2


# ===========================================================================
# 2. SQLite Lesson Store CRUD Tests
# ===========================================================================


def test_lesson_store_crud(tmp_path: Path):
    """Verify persistence, querying, filtering, and deletion in ForexLessonStore."""
    db_file = tmp_path / "test_lessons.db"
    store = ForexLessonStore(db_path=db_file)

    l1 = ForexLesson(
        pair="EURUSD",
        setup_type="TREND_CONTINUATION",
        outcome_category="PREMATURE_EXIT",
        observation="Left 100 pips on table",
        root_cause="Manual early liquidation",
        actionable_rule="Hold core position until structural CHoCH",
        tags=["runner", "early_exit"],
    )
    l2 = ForexLesson(
        pair="USDJPY",
        setup_type="REVERSAL",
        outcome_category="RUNAWAY_LOSS",
        observation="Slipped 15 pips past stop",
        root_cause="Entering during BoJ rate embargo",
        actionable_rule="Do not trade within 30m of central bank embargo",
        tags=["news_embargo", "stop_breach"],
    )

    id1 = store.save_lesson(l1)
    store.save_lesson(l2)

    assert id1 == l1.lesson_id
    assert store.count_lessons() == 2

    # Fetch by ID
    fetched1 = store.get_lesson(id1)
    assert fetched1 is not None
    assert fetched1.pair == "EURUSD"
    assert fetched1.actionable_rule == "Hold core position until structural CHoCH"
    assert "runner" in fetched1.tags

    # Filter by pair
    eur_lessons = store.list_lessons(pair="EURUSD")
    assert len(eur_lessons) == 1
    assert eur_lessons[0].pair == "EURUSD"

    # Filter by setup_type
    rev_lessons = store.list_lessons(setup_type="REVERSAL")
    assert len(rev_lessons) == 1
    assert rev_lessons[0].pair == "USDJPY"

    # Filter by tag
    tag_lessons = store.list_lessons(tag="news_embargo")
    assert len(tag_lessons) == 1
    assert tag_lessons[0].pair == "USDJPY"

    # Delete
    assert store.delete_lesson(id1) is True
    assert store.count_lessons() == 1
    assert store.get_lesson(id1) is None


# ===========================================================================
# 3. ForexReflectionAgent Behavioral Tests
# ===========================================================================


def test_reflection_agent_runaway_loss():
    """Verify reflection on a stop breach generates critical error rating and hard stop lesson."""
    trade = TradeJournalRecord(
        trade_id="trd_runaway",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0735,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=-1.30,
        pips_gained=-65.0,
        status=TradeStatus.CLOSED,
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_runaway",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0735,
        stop_loss=1.0750,
        mfe_price=1.0810,
        mae_price=1.0735,
        mfe_pips=10.0,
        mae_pips=65.0,
        mfe_r=0.2,
        mae_r=1.30,
        realized_pips=-65.0,
        realized_r=-1.30,
        runup_efficiency_pct=0.0,
        drawdown_efficiency_pct=0.0,
        exit_efficiency_pct=0.0,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_runaway",
        category=TradeOutcomeCategory.RUNAWAY_LOSS,
        efficiency_score=26.0,
        title="Runaway Loss",
    )

    agent = ForexReflectionAgent()
    context = ReflectionContext(trade=trade, mfe_mae=mfe_mae, outcome=outcome)
    reflection = agent.reflect(context)

    assert reflection.rating == ReflectionRating.CRITICAL_ERROR
    assert "stop_breach" in reflection.tags
    assert any("breaching intended 1.0R risk limit" in w for w in reflection.what_went_wrong)

    assert len(reflection.lessons) >= 1
    lsn = reflection.lessons[0]
    assert lsn.outcome_category == TradeOutcomeCategory.RUNAWAY_LOSS.value
    assert "Hard Stop Loss" in (lsn.rule_violated or "")
    assert "Never widen a stop loss" in lsn.actionable_rule


def test_reflection_agent_greedy_exit():
    """Verify reflection on an unprotected profit reversal extracts breakeven rule lesson."""
    trade = TradeJournalRecord(
        trade_id="trd_greedy",
        pair="GBPUSD",
        action=ForexAction.LONG,
        open_price=1.2600,
        close_price=1.2602,
        stop_loss=1.2550,
        lots=1.0,
        r_multiple=0.04,
        pips_gained=2.0,
        status=TradeStatus.CLOSED,
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_greedy",
        pair="GBPUSD",
        action=ForexAction.LONG,
        open_price=1.2600,
        close_price=1.2602,
        stop_loss=1.2550,
        mfe_price=1.2700,
        mae_price=1.2580,
        mfe_pips=100.0,
        mae_pips=20.0,
        mfe_r=2.0,
        mae_r=0.4,
        realized_pips=2.0,
        realized_r=0.04,
        runup_efficiency_pct=2.0,
        drawdown_efficiency_pct=60.0,
        exit_efficiency_pct=25.0,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_greedy",
        category=TradeOutcomeCategory.GREEDY_EXIT,
        efficiency_score=30.0,
    )

    agent = ForexReflectionAgent()
    context = ReflectionContext(trade=trade, mfe_mae=mfe_mae, outcome=outcome)
    reflection = agent.reflect(context)

    assert reflection.rating == ReflectionRating.POOR
    assert "greedy_exit" in reflection.tags

    assert len(reflection.lessons) >= 1
    lsn = reflection.lessons[0]
    assert "Breakeven Rule" in (lsn.rule_violated or "")
    assert "move stop loss to entry price" in lsn.actionable_rule


def test_reflection_agent_perfect_exit():
    """Verify reflection on an optimal move capture generates gold-standard benchmark lesson."""
    trade = TradeJournalRecord(
        trade_id="trd_perfect",
        pair="USDJPY",
        action=ForexAction.LONG,
        open_price=150.00,
        close_price=151.70,
        stop_loss=149.00,
        lots=1.0,
        r_multiple=1.70,
        pips_gained=170.0,
        status=TradeStatus.CLOSED,
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_perfect",
        pair="USDJPY",
        action=ForexAction.LONG,
        open_price=150.00,
        close_price=151.70,
        stop_loss=149.00,
        mfe_price=151.80,
        mae_price=149.80,
        mfe_pips=180.0,
        mae_pips=20.0,
        mfe_r=1.8,
        mae_r=0.2,
        realized_pips=170.0,
        realized_r=1.7,
        runup_efficiency_pct=94.4,
        drawdown_efficiency_pct=80.0,
        exit_efficiency_pct=95.0,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_perfect",
        category=TradeOutcomeCategory.PERFECT_EXIT,
        efficiency_score=98.0,
    )

    agent = ForexReflectionAgent()
    context = ReflectionContext(trade=trade, mfe_mae=mfe_mae, outcome=outcome)
    reflection = agent.reflect(context)

    assert reflection.rating == ReflectionRating.EXCELLENT
    assert "perfect_exit" in reflection.tags
    assert any("Exemplary capture" in s for s in reflection.what_went_well)

    assert len(reflection.lessons) >= 1
    lsn = reflection.lessons[0]
    assert lsn.outcome_category == TradeOutcomeCategory.PERFECT_EXIT.value
    assert "Replicate this setup pattern" in lsn.actionable_rule


def test_reflection_agent_execution_slippage_friction():
    """Verify reflection detects significant execution slippage and extracts liquidity timing directive."""
    trade = TradeJournalRecord(
        trade_id="trd_slip",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0850,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=1.0,
        pips_gained=50.0,
        status=TradeStatus.CLOSED,
    )
    eq = ExecutionQuality(
        trade_id="trd_slip",
        pair="EURUSD",
        action=ForexAction.LONG,
        requested_price=1.0800,
        fill_price=1.08015,
        slippage_pips=1.5,
        slippage_type=SlippageType.ADVERSE,
        slippage_cost_usd=15.0,
    )

    agent = ForexReflectionAgent()
    context = ReflectionContext(
        trade=trade,
        executions=[eq],
    )
    reflection = agent.reflect(context)

    assert "execution_slippage_drag" in reflection.tags
    assert any("adverse slippage" in w for w in reflection.what_went_wrong)

    slip_lessons = [les for les in reflection.lessons if les.outcome_category == "EXECUTION_SLIPPAGE"]
    assert len(slip_lessons) == 1
    assert "favor limit orders" in slip_lessons[0].actionable_rule



# ===========================================================================
# 4. Contextual Lesson Retrieval Engine Tests
# ===========================================================================


def test_lesson_retriever_multi_factor_scoring(tmp_path: Path):
    """Verify multi-factor relevance scoring prioritizes exact pair and setup matches."""
    store = ForexLessonStore(db_path=tmp_path / "test_retrieval.db")

    # 1. Exact pair + exact setup (EURUSD Trend Continuation)
    store.save_lesson(
        ForexLesson(
            pair="EURUSD",
            setup_type="TREND_CONTINUATION",
            outcome_category="PREMATURE_EXIT",
            observation="Closed prematurely before London close",
            actionable_rule="Hold EURUSD trend continuations into London/NY overlap",
            tags=["trend", "london_session"],
        )
    )

    # 2. Currency overlap + exact setup (GBPUSD Trend Continuation - shares USD)
    store.save_lesson(
        ForexLesson(
            pair="GBPUSD",
            setup_type="TREND_CONTINUATION",
            outcome_category="GREEDY_EXIT",
            observation="Did not lock breakeven on USD move",
            actionable_rule="Lock BE at +1.0R on USD pairs",
            tags=["trend", "usd_strength"],
        )
    )

    # 3. Disconnected pair & setup (AUDNZD Range Bound)
    store.save_lesson(
        ForexLesson(
            pair="AUDNZD",
            setup_type="RANGE_BOUND",
            outcome_category="STANDARD_WIN",
            observation="Clean fade at range resistance",
            actionable_rule="Fade boundary extremes with tight stop",
            tags=["range"],
        )
    )

    retriever = LessonRetriever(store=store)

    # Query for upcoming EURUSD Trend Continuation
    results = retriever.retrieve_lessons(
        pair="EURUSD",
        setup_type="TREND_CONTINUATION",
        tags=["london_session"],
        limit=2,
    )

    assert len(results) == 2
    # Top result must be EURUSD exact match
    top = results[0]
    assert top.lesson.pair == "EURUSD"
    assert top.lesson.setup_type == "TREND_CONTINUATION"
    assert top.relevance_score > results[1].relevance_score
    assert any("Exact pair match" in r for r in top.match_reasons)
    assert any("Setup strategy match" in r for r in top.match_reasons)

    # Second result should be GBPUSD (currency overlap + setup match)
    second = results[1]
    assert second.lesson.pair == "GBPUSD"
    assert any("Currency exposure overlap" in r for r in second.match_reasons)


def test_lesson_retriever_prompt_formatting(tmp_path: Path):
    """Verify retrieved lessons are formatted into an institutional markdown prompt injection."""
    store = ForexLessonStore(db_path=tmp_path / "test_formatting.db")
    store.save_lesson(
        ForexLesson(
            pair="EURUSD",
            setup_type="BREAKOUT",
            outcome_category="RUNAWAY_LOSS",
            rule_violated="News Embargo Breach",
            observation="Chased false breakout 5 minutes before US CPI release",
            actionable_rule="Never enter market orders within 15 minutes of tier-1 macroeconomic releases",
        )
    )

    retriever = LessonRetriever(store=store)
    results = retriever.retrieve_lessons(pair="EURUSD", setup_type="BREAKOUT")
    prompt_text = retriever.format_lessons_for_prompt(results)

    assert "### Historical Heuristics & Pitfalls" in prompt_text
    assert "EURUSD | BREAKOUT" in prompt_text
    assert "**Rule / Trap:** News Embargo Breach" in prompt_text
    assert "Never enter market orders within 15 minutes" in prompt_text


# ===========================================================================
# 5. ForexLearningManager Full End-to-End Integration Tests
# ===========================================================================


def test_forex_learning_manager_end_to_end(tmp_path: Path):
    """Verify integrated reflection lifecycle with ForexTradeJournal persistence and reporting."""
    db_file = tmp_path / "test_learning_integration.db"
    journal = ForexTradeJournal(db_path=db_file)
    manager = ForexLearningManager(journal=journal)

    # 1. Open trade in journal
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    open_rec = journal.record_trade_open(
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        take_profit=1.0950,
        lots=1.0,
        open_time_utc=t0.isoformat(),
    )

    # 2. Close trade (e.g. premature exit)
    journal.record_trade_close(
        trade_id=open_rec.trade_id,
        close_price=1.0825,
        exit_reason=TradeExitReason.MANUAL,
    )

    # 3. Provide candles showing massive runner excursion to 1.0960
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0960, low=1.0790, close=1.0825, volume=100.0),
    ]

    # 4. Trigger automated reflection lifecycle
    reflection = manager.reflect_on_trade(trade=open_rec.trade_id, candles=candles)
    assert reflection.rating == ReflectionRating.POOR
    assert "premature_exit" in reflection.tags
    assert len(reflection.lessons) >= 1

    # 5. Verify trade in journal has reflection attached
    updated_trade = journal.get_trade(open_rec.trade_id)
    assert updated_trade is not None
    assert "Rating: POOR" in updated_trade.reflection
    assert "premature_exit" in updated_trade.tags

    # 6. Verify timeline event was logged
    events = journal.get_events(trade_id=open_rec.trade_id)
    assert any(e["event_type"] == "NOTE_ADDED" and "ForexReflectionAgent" in e["actor"] for e in events)

    # 7. Verify lesson was persisted in lesson store
    stored_lessons = manager.store.list_lessons(pair="EURUSD")
    assert len(stored_lessons) >= 1
    assert stored_lessons[0].outcome_category == TradeOutcomeCategory.PREMATURE_EXIT.value

    # 8. Retrieve guidance for upcoming trade
    guidance = manager.retrieve_guidance_for_proposal(
        pair="EURUSD", setup_type="TREND_CONTINUATION"
    )
    assert "Historical Heuristics & Pitfalls" in guidance
    assert "EURUSD" in guidance

    # 9. Render markdown summary report
    report = manager.render_lessons_markdown_report()
    assert "# Institutional Trading Lessons & Prescriptive Directives" in report
    assert "EURUSD" in report


def test_reflection_agent_premature_exit_coverage():
    """Verify reflection on a premature exit where runner was cut short."""
    trade = TradeJournalRecord(
        trade_id="trd_prem",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0830,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=0.6,
        pips_gained=30.0,
        status=TradeStatus.CLOSED,
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_prem",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0830,
        stop_loss=1.0750,
        mfe_price=1.0950,
        mae_price=1.0780,
        mfe_pips=150.0,
        mae_pips=20.0,
        mfe_r=3.0,
        mae_r=0.4,
        realized_pips=30.0,
        realized_r=0.6,
        runup_efficiency_pct=20.0,
        drawdown_efficiency_pct=60.0,
        exit_efficiency_pct=29.4,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_prem",
        category=TradeOutcomeCategory.PREMATURE_EXIT,
        efficiency_score=55.0,
    )
    agent = ForexReflectionAgent()
    context = ReflectionContext(trade=trade, mfe_mae=mfe_mae, outcome=outcome)
    reflection = agent.reflect(context)

    assert reflection.rating == ReflectionRating.POOR
    assert "premature_exit" in reflection.tags
    assert any("Runner Execution Discipline" in (les.rule_violated or "") for les in reflection.lessons)



def test_reflection_agent_standard_win_and_loss_coverage():
    """Verify reflection ratings and lesson generation on standard win and loss."""
    agent = ForexReflectionAgent()

    # Standard win
    win_trade = TradeJournalRecord(
        trade_id="trd_win",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0870,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=1.4,
        pips_gained=70.0,
        status=TradeStatus.CLOSED,
    )
    win_context = ReflectionContext(
        trade=win_trade,
        outcome=TradeOutcomeResult(
            trade_id="trd_win",
            category=TradeOutcomeCategory.STANDARD_WIN,
            efficiency_score=85.0,
        ),
    )
    win_ref = agent.reflect(win_context)
    assert win_ref.rating == ReflectionRating.GOOD
    assert "standard_win" in win_ref.tags

    # Standard loss
    loss_trade = TradeJournalRecord(
        trade_id="trd_loss",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0750,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=-1.0,
        pips_gained=-50.0,
        status=TradeStatus.CLOSED,
    )
    loss_context = ReflectionContext(
        trade=loss_trade,
        outcome=TradeOutcomeResult(
            trade_id="trd_loss",
            category=TradeOutcomeCategory.STANDARD_LOSS,
            efficiency_score=60.0,
        ),
    )
    loss_ref = agent.reflect(loss_context)
    assert loss_ref.rating == ReflectionRating.GOOD
    assert "standard_loss" in loss_ref.tags
    assert any("acceptable variance" in les.actionable_rule for les in loss_ref.lessons)



def test_retriever_empty_and_threshold(tmp_path: Path):
    """Verify retriever handles empty store and thresholds gracefully."""
    store = ForexLessonStore(db_path=tmp_path / "test_empty.db")
    retriever = LessonRetriever(store=store)

    assert retriever.retrieve_lessons("EURUSD") == []
    assert retriever.format_lessons_for_prompt([]) == ""


def test_learning_manager_missing_trade_error(tmp_path: Path):
    """Verify learning manager raises ValueError on missing trade id."""
    journal = ForexTradeJournal(db_path=tmp_path / "test_err.db")
    manager = ForexLearningManager(journal=journal)

    with pytest.raises(ValueError, match="Trade with id 'non_existent' not found"):
        manager.reflect_on_trade("non_existent")


# ===========================================================================
# 8. Phase 11 Automated MFE/MAE History Acquisition & Provenance Tests
# ===========================================================================


def test_automatic_mfe_mae_history_acquisition_with_provider(tmp_path: Path):
    """Verify learning manager automatically fetches M1 bars from history provider when candles=None."""
    from tradingagents.learning.history_provider import InMemoryTradeHistoryProvider

    journal = ForexTradeJournal(db_path=tmp_path / "test_history_acq.db")
    t_open = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)
    t_close = datetime(2025, 1, 15, 10, 5, tzinfo=timezone.utc)

    open_rec = journal.record_trade_open(
        trade_id="trd_acq_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        lots=1.0,
        open_time_utc=t_open.isoformat(),
    )
    trade = journal.record_trade_close(
        trade_id=open_rec.trade_id,
        close_price=1.0850,
        close_time_utc=t_close.isoformat(),
        exit_reason=TradeExitReason.MANUAL,
    )

    # 5 M1 bars during the holding period
    bars = [
        ForexBar(timestamp=t_open + timedelta(minutes=1), open=1.0800, high=1.0820, low=1.0790, close=1.0815, volume=10.0),
        ForexBar(timestamp=t_open + timedelta(minutes=2), open=1.0815, high=1.0870, low=1.0810, close=1.0860, volume=15.0),
        ForexBar(timestamp=t_open + timedelta(minutes=3), open=1.0860, high=1.0890, low=1.0850, close=1.0880, volume=20.0),
        ForexBar(timestamp=t_open + timedelta(minutes=4), open=1.0880, high=1.0885, low=1.0830, close=1.0840, volume=12.0),
        ForexBar(timestamp=t_open + timedelta(minutes=5), open=1.0840, high=1.0855, low=1.0835, close=1.0850, volume=8.0),
    ]

    provider = InMemoryTradeHistoryProvider(
        candle_map={"EURUSD": bars},
        source="MT5",
        precision="BAR_APPROXIMATION",
    )
    manager = ForexLearningManager(journal=journal, history_provider=provider)

    # Reflect without passing manual candles
    reflection = manager.reflect_on_trade("trd_acq_1")

    # Verify that history was automatically acquired and excursion metrics calculated
    assert reflection is not None
    # Check trade record was updated in journal
    updated_trade = journal.get_trade("trd_acq_1")
    assert updated_trade.reflection is not None

    # Inspect direct mfe_mae calculation via calculate_trade_mfe_mae with provider
    history_res = provider.get_history("EURUSD", t_open, t_close, "M1")
    assert history_res.is_available is True
    assert history_res.precision == "BAR_APPROXIMATION"
    assert len(history_res.candles) == 5

    from tradingagents.metrics.mfe_mae import calculate_trade_mfe_mae

    res = calculate_trade_mfe_mae(
        trade=trade,
        candles=history_res.candles,
        source=history_res.source,
        resolution=history_res.resolution,
        precision=history_res.precision,
        retrieval_time_utc=history_res.retrieval_time_utc,
        is_available=history_res.is_available,
    )
    assert res.is_available is True
    assert res.source == "MT5"
    assert res.resolution == "M1"
    assert res.precision == "BAR_APPROXIMATION"
    assert res.mfe_price == 1.0890
    assert res.mae_price == 1.0790
    assert res.mfe_pips == 90.0  # (1.0890 - 1.0800) / 0.0001
    assert res.mae_pips == 10.0  # (1.0800 - 1.0790) / 0.0001


def test_mfe_mae_unavailable_when_no_history_provider_or_candles(tmp_path: Path):
    """Verify that when no candles can be fetched, excursion metrics are marked UNAVAILABLE."""
    from tradingagents.learning.history_provider import InMemoryTradeHistoryProvider
    from tradingagents.metrics.mfe_mae import calculate_trade_mfe_mae

    trade = TradeJournalRecord(
        trade_id="trd_empty_acq",
        pair="GBPUSD",
        action=ForexAction.SHORT,
        open_price=1.2500,
        close_price=1.2450,
        stop_loss=1.2550,
        lots=1.0,
        status=TradeStatus.CLOSED,
    )

    empty_provider = InMemoryTradeHistoryProvider(candle_map={})
    res_history = empty_provider.get_history("GBPUSD", datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert res_history.is_available is False
    assert res_history.precision == "UNAVAILABLE"
    assert "MFE_MAE_UNAVAILABLE" in res_history.unavailable_reason

    res = calculate_trade_mfe_mae(
        trade=trade,
        candles=[],
        source=res_history.source,
        resolution=res_history.resolution,
        precision=res_history.precision,
        retrieval_time_utc=res_history.retrieval_time_utc,
        is_available=res_history.is_available,
        unavailable_reason=res_history.unavailable_reason,
    )
    assert res.is_available is False
    assert res.precision == "UNAVAILABLE"
    assert "MFE_MAE_UNAVAILABLE" in res.unavailable_reason


def test_mt5_trade_history_provider_disconnected():
    """Verify MT5TradeHistoryProvider reports UNAVAILABLE when terminal is not connected."""
    from unittest.mock import MagicMock

    from tradingagents.learning.history_provider import MT5TradeHistoryProvider

    mock_obs = MagicMock()
    mock_obs.is_connected = False
    mock_obs.connection.is_connected = False
    mock_obs.connection.connect.side_effect = RuntimeError("Terminal connection refused")

    provider = MT5TradeHistoryProvider(observer=mock_obs)
    res = provider.get_history(
        pair="USDJPY",
        start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert res.is_available is False
    assert res.precision == "UNAVAILABLE"
    assert "MFE_MAE_UNAVAILABLE" in res.unavailable_reason


# ===========================================================================
# 9. Phase 14 Structured Post-Trade Reflection Tests
# ===========================================================================


def test_phase14_structured_reflection_dimensions():
    """Verify reflection context delivers all Phase 14 dimensions and agent outputs structured ratings."""
    from tradingagents.database.models import ProposalRecord
    from tradingagents.journal.models import EventType, TradeEvent

    trade = TradeJournalRecord(
        trade_id="trd_struct_1",
        proposal_id="prop_struct_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0875,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=1.5,
        pips_gained=75.0,
        commission=5.0,
        swap=1.2,
        status=TradeStatus.CLOSED,
    )

    prop = ProposalRecord(
        proposal_id="prop_struct_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
        risk_decision={"max_risk_pct": 1.0, "decision": "APPROVED"},
        metadata={"news_context": {"high_impact_events": []}},
    )

    mfe_mae = TradeMfeMae(
        trade_id="trd_struct_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0875,
        stop_loss=1.0750,
        mfe_price=1.0900,
        mae_price=1.0785,
        mfe_r=2.0,
        mae_r=0.3,
        realized_r=1.5,
        runup_efficiency_pct=75.0,
        exit_efficiency_pct=85.0,
    )

    outcome = TradeOutcomeResult(
        trade_id="trd_struct_1",
        category=TradeOutcomeCategory.STANDARD_WIN,
        efficiency_score=85.0,
    )

    events = [
        TradeEvent(
            event_id="evt_sl",
            event_type=EventType.SL_CHANGED,
            trade_id="trd_struct_1",
            description="Moved to Breakeven",
        ),
        TradeEvent(
            event_id="evt_part",
            event_type=EventType.PARTIAL_CLOSE,
            trade_id="trd_struct_1",
            description="Closed 50% lots",
        ),
    ]

    context = ReflectionContext(
        trade=trade,
        proposal=prop,
        mfe_mae=mfe_mae,
        outcome=outcome,
        events=events,
        risk_decision=prop.risk_decision,
        session="LONDON",
        setup="TREND_CONTINUATION",
    )

    # Verify context helper properties (Phase 14 requirements)
    assert context.original_proposal is not None
    assert context.risk_decision == {"max_risk_pct": 1.0, "decision": "APPROVED"}
    assert len(context.event_timeline) == 2
    assert len(context.sl_changes) == 1
    assert len(context.partial_closes) == 1
    assert context.commission == 5.0
    assert context.swap == 1.2
    assert context.realized_r == 1.5
    assert context.mfe is not None
    assert context.mfe.mfe_r == 2.0
    assert context.mae == 0.0  # mae_pips default is 0.0

    agent = ForexReflectionAgent()
    reflection = agent.reflect(context)

    # Verify structured dimensions (Phase 14)
    assert reflection.thesis_quality == "EXCELLENT"
    assert reflection.direction_quality == "EXCELLENT"
    assert reflection.entry_quality == "EXCELLENT"
    assert reflection.stop_quality == "EXCELLENT"
    assert reflection.target_quality == "EXCELLENT"
    assert reflection.management_quality == "EXCELLENT"
    assert reflection.main_success != ""
    assert reflection.main_failure != ""
    assert len(reflection.lessons) > 0

    # Verify metrics were NOT rewritten
    assert context.trade.r_multiple == 1.5
    assert context.mfe_mae.mfe_r == 2.0


def test_phase14_structured_reflection_stop_breach():
    """Verify structured reflection accurately diagnoses severe stop loss breaches."""
    trade = TradeJournalRecord(
        trade_id="trd_breach_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0735,
        stop_loss=1.0750,
        lots=1.0,
        r_multiple=-1.3,
        status=TradeStatus.CLOSED,
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_breach_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        close_price=1.0735,
        stop_loss=1.0750,
        mfe_price=1.0805,
        mae_price=1.0735,
        mfe_r=0.1,
        mae_r=1.3,
        realized_r=-1.3,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_breach_1",
        category=TradeOutcomeCategory.RUNAWAY_LOSS,
        efficiency_score=0.0,
    )

    context = ReflectionContext(trade=trade, mfe_mae=mfe_mae, outcome=outcome)
    agent = ForexReflectionAgent()
    reflection = agent.reflect(context)

    assert reflection.rating == ReflectionRating.CRITICAL_ERROR
    assert reflection.stop_quality == "CRITICAL_ERROR"
    assert reflection.thesis_quality == "CRITICAL_ERROR"
    assert "breaching intended 1.0R risk limit" in reflection.main_failure


# ===========================================================================
# Phase 15: Structured Forex Memory Tests
# ===========================================================================


def test_phase15_forex_lesson_schema_and_evidence_class():
    """Verify ForexLesson supports all Phase 15 schema fields and evidence classes."""
    from tradingagents.learning.models import (
        EvidenceClass,
        ForexLesson,
        classify_evidence,
    )

    lesson = ForexLesson(
        source_trade_id="trd_100",
        proposal_id="prop_100",
        pair="EURUSD",
        timeframe="M15",
        setup="TREND_CONTINUATION",
        direction="LONG",
        session="LONDON",
        market_regime="TRENDING_BULLISH",
        lesson_type="RISK_MANAGEMENT",
        observation="Controlled pullback entry with minimal adverse excursion.",
        root_cause="Clean market structure alignment.",
        actionable_rule="Wait for pullback to 50% discount zone before entry.",
        evidence_count=1,
        confidence=0.85,
        strategy_version="1.1",
        active=True,
    )

    # Required Phase 15 schema fields
    assert lesson.lesson_id.startswith("lsn_")
    assert lesson.source_trade_id == "trd_100"
    assert lesson.proposal_id == "prop_100"
    assert lesson.pair == "EURUSD"
    assert lesson.timeframe == "M15"
    assert lesson.setup == "TREND_CONTINUATION"
    assert lesson.direction == "LONG"
    assert lesson.session == "LONDON"
    assert lesson.market_regime == "TRENDING_BULLISH"
    assert lesson.lesson_type == "RISK_MANAGEMENT"
    assert lesson.observation != ""
    assert lesson.root_cause != ""
    assert lesson.actionable_rule != ""
    assert lesson.evidence_count == 1
    assert lesson.confidence == 0.85
    assert lesson.created_at != ""
    assert lesson.strategy_version == "1.1"
    assert lesson.active is True

    # Evidence classes
    assert lesson.evidence_class == EvidenceClass.ANECDOTAL
    assert classify_evidence(1) == EvidenceClass.ANECDOTAL
    assert classify_evidence(2) == EvidenceClass.EARLY
    assert classify_evidence(3) == EvidenceClass.MODERATE
    assert classify_evidence(5) == EvidenceClass.MODERATE
    assert classify_evidence(6) == EvidenceClass.STRONG

    # Backward compatibility aliases
    assert lesson.trade_id == "trd_100"
    assert lesson.setup_type == "TREND_CONTINUATION"
    assert lesson.confidence_score == 0.85
    assert lesson.created_at_utc == lesson.created_at

    # Legacy constructor compatibility
    legacy_lesson = ForexLesson(
        trade_id="trd_legacy",
        pair="GBPUSD",
        setup_type="BREAKOUT",
        confidence_score=0.9,
        created_at_utc="2026-09-01T12:00:00+00:00",
        actionable_rule="Tighten stop loss on range compression.",
    )
    assert legacy_lesson.source_trade_id == "trd_legacy"
    assert legacy_lesson.setup == "BREAKOUT"
    assert legacy_lesson.confidence == 0.9
    assert legacy_lesson.created_at == "2026-09-01T12:00:00+00:00"


def test_phase15_anti_overfitting_ban_prevention():
    """Verify that a single trade (anecdotal evidence) cannot create 'NEVER trade X again' rules."""
    from tradingagents.learning.models import ForexLesson

    # Single losing trade attempting to create categorical prohibition
    with pytest.raises(ValueError, match="NEVER trade"):
        ForexLesson(
            source_trade_id="trd_loss_1",
            pair="EURUSD",
            setup="PULLBACK",
            actionable_rule="NEVER trade EURUSD again due to sudden spread widening.",
            evidence_count=1,
        )

    with pytest.raises(ValueError, match="NEVER trade"):
        ForexLesson(
            pair="USDJPY",
            actionable_rule="Stop trading USDJPY breakouts permanently.",
            evidence_count=1,
        )

    # Multi-observation accumulated evidence can express strong restrictions
    strong_lesson = ForexLesson(
        pair="EURUSD",
        setup="PULLBACK",
        actionable_rule="Do not trade EURUSD during Friday NFP release.",
        evidence_count=6,
    )
    assert strong_lesson.evidence_count == 6


def test_phase15_lesson_store_merge_similar_lessons(tmp_path):
    """Verify ForexLessonStore merges similar heuristics, accumulating evidence count."""
    from tradingagents.learning.models import EvidenceClass, ForexLesson
    from tradingagents.learning.store import ForexLessonStore

    db_path = tmp_path / "lessons_test.db"
    store = ForexLessonStore(db_path=db_path)

    lesson1 = ForexLesson(
        source_trade_id="trd_1",
        pair="EURUSD",
        setup="BREAKOUT",
        lesson_type="EXECUTION_QUALITY",
        outcome_category="EXECUTION_SLIPPAGE",
        observation="Observed 1.2 pip slippage during London open.",
        actionable_rule="Use limit orders on EURUSD breakouts.",
        confidence=0.80,
        evidence_count=1,
        tags=["slippage", "london"],
    )
    store.save_lesson(lesson1)
    assert store.count_lessons() == 1

    # Second trade observes similar execution friction on same pair & setup
    lesson2 = ForexLesson(
        source_trade_id="trd_2",
        pair="EURUSD",
        setup="BREAKOUT",
        lesson_type="EXECUTION_QUALITY",
        outcome_category="EXECUTION_SLIPPAGE",
        observation="Observed 1.5 pip slippage during NY open.",
        actionable_rule="Use limit orders on EURUSD breakouts.",
        confidence=0.85,
        evidence_count=1,
        tags=["slippage", "new_york"],
    )
    merged = store.merge_or_save_lesson(lesson2)

    # Lesson should be merged, count incremented, confidence reinforced
    assert store.count_lessons() == 1
    assert merged.lesson_id == lesson1.lesson_id
    assert merged.evidence_count == 2
    assert merged.evidence_class == EvidenceClass.EARLY
    assert merged.confidence == pytest.approx(0.85)
    assert "Re-observed in trade trd_2" in merged.observation
    assert "new_york" in merged.tags
    assert merged.last_validated_at is not None


def test_phase15_lesson_store_weaken_contradicting_lessons(tmp_path):
    """Verify contradicting empirical evidence weakens confidence and deactivates invalid heuristics."""
    from tradingagents.learning.models import ForexLesson
    from tradingagents.learning.store import ForexLessonStore

    db_path = tmp_path / "lessons_weaken.db"
    store = ForexLessonStore(db_path=db_path)

    # Create a failure rule from a prior loss
    lesson = ForexLesson(
        source_trade_id="trd_loss_prev",
        pair="GBPUSD",
        setup="ASIAN_RANGE_BREAKOUT",
        lesson_type="SETUP_FAILURE",
        outcome_category="STANDARD_LOSS",
        observation="Asian breakout failed into false expansion.",
        actionable_rule="Require retest of Asian range before entering breakout.",
        confidence=0.60,
        evidence_count=1,
    )
    store.save_lesson(lesson)
    assert store.count_lessons(active_only=True) == 1

    # First contradictory success weakens lesson by 0.2
    weakened = store.weaken_contradicting_lessons(pair="GBPUSD", setup="ASIAN_RANGE_BREAKOUT", penalty=0.2)
    assert len(weakened) == 1
    assert weakened[0].confidence == pytest.approx(0.40)
    assert weakened[0].active is True

    # Second contradictory success further weakens lesson below 0.3 threshold -> deactivates
    weakened2 = store.weaken_contradicting_lessons(pair="GBPUSD", setup="ASIAN_RANGE_BREAKOUT", penalty=0.2)
    assert len(weakened2) == 1
    assert weakened2[0].confidence == pytest.approx(0.20)
    assert weakened2[0].active is False

    # Active count should now be 0, total count still 1
    assert store.count_lessons(active_only=True) == 0
    assert store.count_lessons(active_only=False) == 1


def test_phase15_reflection_agent_generates_phase15_lessons():
    """Verify reflection agent populates Phase 15 dimensional fields on all generated lessons."""
    from tradingagents.database.models import ProposalRecord
    from tradingagents.learning.agent import ForexReflectionAgent
    from tradingagents.learning.models import ReflectionContext

    trade = TradeJournalRecord(
        trade_id="trd_agent_15",
        proposal_id="prop_agent_15",
        pair="USDJPY",
        action=ForexAction.SHORT,
        open_price=150.00,
        close_price=150.80,
        stop_loss=150.60,
        lots=0.5,
        r_multiple=-1.33,
        status=TradeStatus.CLOSED,
        metadata={"session": "TOKYO", "market_regime": "RANGING", "timeframe": "H1"},
    )
    proposal = ProposalRecord(
        proposal_id="prop_agent_15",
        pair="USDJPY",
        action=ForexAction.SHORT,
        timeframe="H1",
        setup_type="RANGE_BOUND",
    )
    mfe_mae = TradeMfeMae(
        trade_id="trd_agent_15",
        pair="USDJPY",
        action=ForexAction.SHORT,
        open_price=150.00,
        close_price=150.80,
        stop_loss=150.60,
        mfe_price=149.95,
        mae_price=150.80,
        mae_r=1.33,
        mfe_r=0.1,
        realized_r=-1.33,
    )
    outcome = TradeOutcomeResult(
        trade_id="trd_agent_15",
        category=TradeOutcomeCategory.RUNAWAY_LOSS,
    )
    context = ReflectionContext(
        trade=trade,
        proposal=proposal,
        mfe_mae=mfe_mae,
        outcome=outcome,
        session="TOKYO",
        setup="RANGE_BOUND",
    )

    agent = ForexReflectionAgent()
    reflection = agent.reflect(context)

    assert len(reflection.lessons) > 0
    lesson = reflection.lessons[0]

    # Verify Phase 15 fields populated
    assert lesson.source_trade_id == "trd_agent_15"
    assert lesson.proposal_id == "prop_agent_15"
    assert lesson.pair == "USDJPY"
    assert lesson.timeframe == "H1"
    assert lesson.setup == "RANGE_BOUND"
    assert lesson.direction == "SHORT"
    assert lesson.session == "TOKYO"
    assert lesson.market_regime == "RANGING"
    assert lesson.lesson_type == "STOP_LOSS_DISCIPLINE"
    assert "NEVER trade" not in lesson.actionable_rule


# ===========================================================================
# Phase 16: Contextual Memory Retrieval Tests
# ===========================================================================


def test_phase16_contextual_retrieval_and_filtering(tmp_path):
    """Verify EURUSD M15 London Pullback lesson retrieves for future EURUSD M15 Pullback

    and does NOT automatically inject into unrelated USDJPY H4 Breakout.
    """
    from tradingagents.learning.models import ForexLesson
    from tradingagents.learning.retriever import LessonRetriever
    from tradingagents.learning.store import ForexLessonStore

    db_file = tmp_path / "phase16_retrieval.db"
    store = ForexLessonStore(db_path=db_file)

    # Store EURUSD M15 London Pullback lesson
    lesson = ForexLesson(
        lesson_id="lsn_eurusd_m15_pullback",
        source_trade_id="trd_pullback_1",
        pair="EURUSD",
        timeframe="M15",
        setup="PULLBACK",
        direction="LONG",
        session="LONDON",
        market_regime="TRENDING_BULLISH",
        actionable_rule="Wait for discount zone retest on M15 London pullbacks before executing long.",
        confidence=0.85,
        evidence_count=2,
    )
    store.save_lesson(lesson)

    retriever = LessonRetriever(store=store)

    # 1. Query for relevant future EURUSD M15 Pullback
    relevant_results = retriever.retrieve_lessons(
        pair="EURUSD",
        timeframe="M15",
        setup_type="PULLBACK",
        session="LONDON",
        limit=5,
        min_relevance=0.35,
    )
    assert len(relevant_results) == 1
    top = relevant_results[0]
    assert top.lesson.lesson_id == "lsn_eurusd_m15_pullback"
    assert top.relevance_score >= 0.50
    assert any("Exact pair match" in r for r in top.match_reasons)
    assert any("Timeframe match" in r for r in top.match_reasons)
    assert any("Setup strategy match" in r for r in top.match_reasons)

    prompt_text = retriever.format_lessons_for_prompt(relevant_results)
    assert "lsn_eurusd_m15_pullback" in prompt_text
    assert "EURUSD M15" in prompt_text

    # 2. Query for unrelated USDJPY H4 Breakout
    unrelated_results = retriever.retrieve_lessons(
        pair="USDJPY",
        timeframe="H4",
        setup_type="BREAKOUT",
        session="TOKYO",
        limit=5,
        min_relevance=0.35,
    )
    # Must NOT automatically inject into unrelated USDJPY H4 Breakout
    assert len(unrelated_results) == 0
    assert retriever.format_lessons_for_prompt(unrelated_results) == ""


def test_phase16_small_subset_constraint(tmp_path):
    """Verify retriever only retrieves a small relevant subset (3-10 lessons) and does not dump entire DB."""
    from tradingagents.learning.models import ForexLesson
    from tradingagents.learning.retriever import LessonRetriever
    from tradingagents.learning.store import ForexLessonStore

    db_file = tmp_path / "phase16_subset.db"
    store = ForexLessonStore(db_path=db_file)

    # Seed 25 lessons into DB
    for i in range(25):
        store.save_lesson(
            ForexLesson(
                lesson_id=f"lsn_{i:02d}",
                pair="EURUSD",
                timeframe="M15",
                setup="PULLBACK",
                actionable_rule=f"Directive rule #{i}",
                confidence=0.80,
            )
        )
    assert store.count_lessons() == 25

    retriever = LessonRetriever(store=store)
    # Request default limit (5)
    subset = retriever.retrieve_lessons(pair="EURUSD", timeframe="M15", setup_type="PULLBACK")
    assert len(subset) == 5
    assert len(subset) <= 10

    # Request upper bounded limit
    subset_max = retriever.retrieve_lessons(pair="EURUSD", timeframe="M15", setup_type="PULLBACK", limit=20)
    assert len(subset_max) == 10  # Clamped to max 10 to protect LLM context window


def test_phase16_report_applied_lesson_ids():
    """Verify applied lesson IDs are tracked in proposal model and rendered in markdown."""
    from tradingagents.agents.schemas_forex import (
        ForexAction,
        ForexTraderProposal,
        render_forex_trader_proposal,
    )

    prop = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit_1=1.0950,
        reasoning="Bullish market structure alignment.",
        applied_lesson_ids=["lsn_eurusd_m15_pullback", "lsn_slippage_safeguard"],
    )

    assert prop.applied_lesson_ids == ["lsn_eurusd_m15_pullback", "lsn_slippage_safeguard"]
    rendered = render_forex_trader_proposal(prop)
    assert "Applied Historical Lessons" in rendered
    assert "lsn_eurusd_m15_pullback" in rendered
    assert "lsn_slippage_safeguard" in rendered


def test_learning_api_filters_and_reports_missing_sources(tmp_path: Path):
    journal = ForexTradeJournal(db_path=tmp_path / "learning-ui.db")
    manager = ForexLearningManager(journal=journal)
    lesson = ForexLesson(
        lesson_id="lsn_ui_trace",
        source_trade_id="trd_missing",
        proposal_id="prop_missing",
        pair="EURUSD",
        timeframe="M15",
        setup="PULLBACK",
        direction="LONG",
        evidence_count=3,
        actionable_rule="Require a closed confirmation candle.",
    )
    manager.store.save_lesson(lesson)
    set_forex_dependencies(journal=journal, learning_manager=manager)
    app = FastAPI()
    app.include_router(router)
    from web.server import _SESSION_TOKEN, DASHBOARD_API_KEY

    client = TestClient(app, headers={"X-API-Key": DASHBOARD_API_KEY or _SESSION_TOKEN})
    response = client.get(
        "/api/forex/learning/lessons?pair=EURUSD&setup_type=PULLBACK"
        "&direction=LONG&timeframe=M15&min_evidence_count=3&active=true"
    )
    assert response.status_code == 200
    assert [item["lesson_id"] for item in response.json()["lessons"]] == ["lsn_ui_trace"]

    detail = client.get("/api/forex/learning/lessons/lsn_ui_trace")
    assert detail.status_code == 200
    assert detail.json()["sources"] == {
        "trade": {"id": "trd_missing", "available": False},
        "proposal": {"id": "prop_missing", "available": False},
    }
    missing = client.get("/api/forex/learning/lessons/not-present")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Run not found"


# ===========================================================================
# 10. Phase 7 Learning and Analytics Completion Tests (LEARN-006, LEARN-010, AN-007)
# ===========================================================================


class TestLearningAndAnalyticsCompletion:
    """Comprehensive verification for Phase 7 remediation requirements."""

    def test_min_support_threshold_boundary(self, tmp_path: Path):
        """Verify configurable min_support boundary filtering (LEARN-006)."""
        store = ForexLessonStore(db_path=tmp_path / "test_min_support.db")

        # 3 lessons with varying empirical evidence
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_sup_1",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                evidence_count=1,
                actionable_rule="Single observation rule",
            )
        )
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_sup_2",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                evidence_count=2,
                actionable_rule="Two observations rule",
            )
        )
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_sup_4",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                evidence_count=4,
                actionable_rule="Four observations rule",
            )
        )

        retriever = LessonRetriever(store=store)

        # min_support=1 -> all 3 returned
        res_1 = retriever.retrieve_lessons(pair="EURUSD", min_support=1)
        assert len(res_1) == 3

        # min_support=2 -> evidence_count=1 excluded
        res_2 = retriever.retrieve_lessons(pair="EURUSD", min_support=2)
        assert len(res_2) == 2
        ids_2 = {r.lesson.lesson_id for r in res_2}
        assert ids_2 == {"lsn_sup_2", "lsn_sup_4"}

        # min_support=3 -> evidence_count=1 and 2 excluded
        res_3 = retriever.retrieve_lessons(pair="EURUSD", min_support=3)
        assert len(res_3) == 1
        assert res_3[0].lesson.lesson_id == "lsn_sup_4"

        # min_support=5 -> none meet threshold
        res_5 = retriever.retrieve_lessons(pair="EURUSD", min_support=5)
        assert res_5 == []

    def test_insufficient_evidence_excluded(self, tmp_path: Path):
        """Verify that an exact match is strictly excluded when evidence is insufficient."""
        store = ForexLessonStore(db_path=tmp_path / "test_insufficient.db")
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_perfect_anecdote",
                pair="GBPUSD",
                setup_type="BREAKOUT",
                timeframe="M15",
                direction="LONG",
                confidence=1.0,
                evidence_count=1,
                actionable_rule="Anecdotal breakout rule",
            )
        )

        retriever = LessonRetriever(store=store)
        # Default min_support=1 returns it
        assert len(retriever.retrieve_lessons(pair="GBPUSD", setup_type="BREAKOUT")) == 1

        # Explicit min_support=2 excludes it
        res = retriever.retrieve_lessons(
            pair="GBPUSD", setup_type="BREAKOUT", min_support=2
        )
        assert res == []

    def test_configurable_age_decay_half_life(self, tmp_path: Path):
        """Verify continuous exponential age decay and half-life parameterization (LEARN-010)."""
        from tradingagents.learning.retriever import calculate_age_decay

        now = datetime.now(timezone.utc)
        lsn_fresh = ForexLesson(
            pair="EURUSD",
            created_at=now.isoformat(),
            actionable_rule="Fresh rule",
        )
        lsn_30d = ForexLesson(
            pair="EURUSD",
            created_at=(now - timedelta(days=30)).isoformat(),
            actionable_rule="30d old rule",
        )
        lsn_60d = ForexLesson(
            pair="EURUSD",
            created_at=(now - timedelta(days=60)).isoformat(),
            actionable_rule="60d old rule",
        )

        # Mathematical half-life verification:
        # At 0 days -> decay = 1.0
        days_0, decay_0 = calculate_age_decay(lsn_fresh, as_of=now, half_life_days=30.0)
        assert days_0 < 0.01
        assert pytest.approx(decay_0, 0.01) == 1.0

        # At 30 days with half_life=30 -> decay = 0.5
        days_30, decay_30 = calculate_age_decay(lsn_30d, as_of=now, half_life_days=30.0)
        assert pytest.approx(days_30, 0.1) == 30.0
        assert pytest.approx(decay_30, 0.02) == 0.5

        # At 60 days with half_life=30 -> decay = 0.25 (2 half-lives)
        days_60, decay_60 = calculate_age_decay(lsn_60d, as_of=now, half_life_days=30.0)
        assert pytest.approx(days_60, 0.1) == 60.0
        assert pytest.approx(decay_60, 0.02) == 0.25

        # Faster decay: half_life=7 days -> at 30 days decay = 2^(-30/7) ~= 0.051
        _, decay_fast = calculate_age_decay(lsn_30d, as_of=now, half_life_days=7.0)
        assert decay_fast < 0.06

        # Slower decay: half_life=365 days -> at 30 days decay = 2^(-30/365) ~= 0.944
        _, decay_slow = calculate_age_decay(lsn_30d, as_of=now, half_life_days=365.0)
        assert decay_slow > 0.93

        # Store-level retrieval scoring verification:
        store = ForexLessonStore(db_path=tmp_path / "test_decay_retrieval.db")
        store.save_lesson(lsn_fresh)
        store.save_lesson(lsn_60d)

        retriever = LessonRetriever(store=store)
        results = retriever.retrieve_lessons(pair="EURUSD", half_life_days=30.0)
        assert len(results) == 2
        # Fresh lesson must have higher relevance score due to age decay on older lesson
        assert results[0].lesson.lesson_id == lsn_fresh.lesson_id
        assert results[0].relevance_score > results[1].relevance_score

    def test_point_in_time_retrieval_cutoff(self, tmp_path: Path):
        """Verify strict point-in-time retrieval cutoff prevents future lesson lookahead."""
        store = ForexLessonStore(db_path=tmp_path / "test_pit_cutoff.db")

        # Lesson 1: Created Jan 1, 2025
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_jan_01",
                pair="EURUSD",
                created_at="2025-01-01T00:00:00Z",
                actionable_rule="Jan 1 rule",
            )
        )
        # Lesson 2: Created Jan 10, 2025
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_jan_10",
                pair="EURUSD",
                created_at="2025-01-10T00:00:00Z",
                actionable_rule="Jan 10 rule",
            )
        )
        # Lesson 3: Created Jan 25, 2025 (future relative to Jan 15 cutoff)
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_jan_25",
                pair="EURUSD",
                created_at="2025-01-25T00:00:00Z",
                actionable_rule="Jan 25 future rule",
            )
        )

        retriever = LessonRetriever(store=store)

        # Query as of Jan 15, 2025
        cutoff = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
        results = retriever.retrieve_lessons(pair="EURUSD", as_of=cutoff)

        retrieved_ids = [r.lesson.lesson_id for r in results]
        assert "lsn_jan_25" not in retrieved_ids, "Future lesson must be excluded by as_of cutoff"
        assert "lsn_jan_10" in retrieved_ids
        assert "lsn_jan_01" in retrieved_ids

        # Verify age decay was computed relative to Jan 15 cutoff:
        # lsn_jan_10 is exactly 5 days old as of Jan 15
        top = next(r for r in results if r.lesson.lesson_id == "lsn_jan_10")
        assert any("5.0d ago" in reason for reason in top.match_reasons)

    def test_learning_advisory_boundary_prompt_framing(self, tmp_path: Path):
        """Verify prompt formatting explicitly states lessons are advisory evidence."""
        store = ForexLessonStore(db_path=tmp_path / "test_advisory.db")
        store.save_lesson(
            ForexLesson(
                lesson_id="lsn_adv_1",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                actionable_rule="Advisory observation only",
            )
        )
        retriever = LessonRetriever(store=store)
        results = retriever.retrieve_lessons(pair="EURUSD")
        prompt = retriever.format_lessons_for_prompt(results)

        assert "ADVISORY EVIDENCE ONLY" in prompt
        assert "do not override deterministic risk rules" in prompt

    def test_analytics_stop_target_calibration_advisory_separation(self):
        """Verify AN-007 calibration is strictly marked as advisory empirical research."""
        from tradingagents.analytics.calibration import RiskStopCalibrator

        calibrator = RiskStopCalibrator(default_atr_pips=20.0)

        # Synthetic trade excursions
        synthetic_trades = [
            {"mae_pips": 8.0, "mfe_pips": 45.0, "mfe_r": 2.2, "mae_r": 0.4, "pips_gained": 40.0, "net_profit": 400.0},
            {"mae_pips": 12.0, "mfe_pips": 35.0, "mfe_r": 1.7, "mae_r": 0.6, "pips_gained": 30.0, "net_profit": 300.0},
            {"mae_pips": 25.0, "mfe_pips": 10.0, "mfe_r": 0.5, "mae_r": 1.25, "pips_gained": -25.0, "net_profit": -250.0},
        ]

        calibration = calibrator.calibrate(synthetic_trades, pair="EURUSD")

        # Verify strict advisory separation
        assert calibration.is_advisory_only is True
        assert calibration.applied_automatically is False
        assert "Descriptive empirical research" in calibration.evidence_note
        assert any("ADVISORY NOTICE" in r for r in calibration.recommendations)

        # Verify empty calibration fallback also preserves advisory boundary
        empty_calib = calibrator.calibrate([], pair="GBPUSD")
        assert empty_calib.is_advisory_only is True
        assert empty_calib.applied_automatically is False
        assert any("ADVISORY NOTICE" in r for r in empty_calib.recommendations)

    def test_learning_retrieval_route_with_support_and_decay(self, tmp_path: Path):
        """Verify web API /learning/retrieve accepts min_support, half_life_days, and as_of."""
        journal = ForexTradeJournal(db_path=tmp_path / "route_learning.db")
        manager = ForexLearningManager(journal=journal)

        manager.store.save_lesson(
            ForexLesson(
                lesson_id="lsn_api_1",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                evidence_count=1,
                created_at="2025-01-01T00:00:00Z",
                actionable_rule="Single evidence rule",
            )
        )
        manager.store.save_lesson(
            ForexLesson(
                lesson_id="lsn_api_3",
                pair="EURUSD",
                setup_type="TREND_CONTINUATION",
                evidence_count=3,
                created_at="2025-01-10T00:00:00Z",
                actionable_rule="Triple evidence rule",
            )
        )

        set_forex_dependencies(journal=journal, learning_manager=manager)
        app = FastAPI()
        app.include_router(router)
        from web.server import _SESSION_TOKEN, DASHBOARD_API_KEY

        client = TestClient(app, headers={"X-API-Key": DASHBOARD_API_KEY or _SESSION_TOKEN})

        # Query with min_support=2 -> should return only lsn_api_3
        res = client.get("/api/forex/learning/retrieve?pair=EURUSD&min_support=2")
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["applied_lesson_ids"] == ["lsn_api_3"]
        assert "ADVISORY EVIDENCE ONLY" in data["markdown_prompt"]

        # Query with as_of cutoff before Jan 10 -> should return lsn_api_1 (if min_support=1)
        res_cutoff = client.get(
            "/api/forex/learning/retrieve?pair=EURUSD&min_support=1&as_of=2025-01-05T00:00:00Z"
        )
        assert res_cutoff.status_code == 200
        data_cutoff = res_cutoff.json()
        assert data_cutoff["applied_lesson_ids"] == ["lsn_api_1"]


