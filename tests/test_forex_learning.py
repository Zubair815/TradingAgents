"""Unit & Integration Tests for Forex Learning, Post-Trade Reflection & Lesson Retrieval (Phase 17)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
    open_rec = journal.record_trade_open(
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        take_profit=1.0950,
        lots=1.0,
    )

    # 2. Close trade (e.g. premature exit)
    journal.record_trade_close(
        trade_id=open_rec.trade_id,
        close_price=1.0825,
        exit_reason=TradeExitReason.MANUAL,
    )

    # 3. Provide candles showing massive runner excursion to 1.0960
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
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


