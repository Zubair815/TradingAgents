"""Forex domain package.

Exports the core Forex domain objects that the rest of the system depends on.
Import from here rather than from sub-modules to keep the public surface stable
as the internal structure evolves.
"""

from tradingagents.forex.calendar import (
    EconomicEvent,
    EventImpact,
    EventRiskAssessment,
    EventRiskRegime,
    EventSurpriseDirection,
    PairEventBias,
    TradingActionRecommendation,
    calculate_pair_event_bias,
    evaluate_event_risk_regime,
    format_economic_calendar_table,
    get_calendar_events_for_pair,
)
from tradingagents.forex.domain import (
    EXOTIC_PAIRS,
    MAJOR_PAIRS,
    MINOR_PAIRS,
    AssetType,
    ForexPair,
    Timeframe,
    get_forex_pair,
    is_forex_pair,
    normalize_forex_pair,
)
from tradingagents.forex.indicators import (
    ForexIndicatorSnapshot,
    RSIRegime,
    TrendRegime,
    TrendStrength,
    build_forex_indicator_snapshot,
    calculate_adx,
    calculate_atr,
    calculate_atr_percent,
    calculate_atr_pips,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_spread_atr_ratio,
    calculate_stochastic,
    classify_adx_strength,
    classify_ema_trend,
    classify_rsi_regime,
    compute_forex_indicators,
    compute_multi_timeframe_indicators,
    format_indicator_snapshot,
    format_multi_timeframe_indicators_summary,
    is_spread_favorable,
)
from tradingagents.forex.market_structure import (
    AlignmentBias,
    BreakDirection,
    BreakType,
    FairValueGap,
    FVGType,
    MarketStructureSnapshot,
    MultiTimeframeStructureAlignment,
    OBType,
    OrderBlock,
    StructuralBreak,
    StructureTrend,
    SwingPoint,
    SwingTag,
    SwingType,
    analyze_multi_timeframe_structure,
    build_market_structure_snapshot,
    classify_structure_trend,
    detect_structural_breaks,
    find_fair_value_gaps,
    find_order_blocks,
    find_support_resistance_levels,
    find_swing_points,
    format_market_structure_snapshot,
    format_multi_timeframe_structure_summary,
)
from tradingagents.forex.pips import (
    lot_size_from_risk,
    pip_size_for,
    pip_value_in_account_currency,
    pips_between,
    pips_to_price,
    price_to_pips,
)
from tradingagents.forex.sessions import (
    OVERLAP_LONDON_NY,
    OVERLAP_LONDON_TOKYO,
    OVERLAP_TOKYO_SYDNEY,
    MarketRegime,
    SessionInfo,
    SessionOverlap,
    TradingSession,
    format_session_summary,
    get_active_sessions,
    get_active_sessions_for_pair,
    get_market_status,
    get_next_session_open,
    get_relevant_sessions_for_pair,
    get_session_overlaps,
    is_holiday,
    is_market_open,
    is_pair_in_prime_session,
    is_weekend,
    session_for_pair,
)
from tradingagents.forex.symbols import (
    ForexSymbolMap,
    broker_to_canonical,
    canonical_to_broker,
    canonical_to_yahoo,
    strip_broker_suffix,
)

# Lazy resolution for dataflows and agent schemas exports to avoid circular import when
# forex_data or schemas_forex imports from tradingagents.forex.domain
_DATAFLOWS_EXPORTS = {
    "ForexBar",
    "MultiTimeframeData",
    "fetch_forex_candles",
    "fetch_multi_timeframe_data",
    "resample_candles",
    "validate_forex_candles",
    "filter_candles_by_cutoff",
    "format_forex_candles_csv",
    "format_multi_timeframe_summary",
}

_SCHEMAS_EXPORTS = {
    "ForexAction",
    "OrderType",
    "SetupType",
    "ForexRiskDecisionAction",
    "ForexTraderProposal",
    "ForexRiskDecision",
    "render_forex_trader_proposal",
    "render_forex_risk_decision",
    "compute_pips_and_rr",
    "from_trader_proposal",
}

_RISK_EXPORTS = {
    "ForexRiskEngine",
    "ForexRiskLimits",
    "validate_forex_proposal",
}

_SIZING_EXPORTS = {
    "PositionSizingMethod",
    "ForexAccountProfile",
    "BrokerExecutionConstraints",
    "OpenPosition",
    "PositionSizingResult",
    "ForexPositionSizingEngine",
    "calculate_required_margin",
    "calculate_volatility_adjusted_risk_percent",
    "calculate_kelly_risk_percent",
    "calculate_portfolio_currency_exposure",
    "calculate_correlation_exposure_factor",
    "calculate_forex_position_size",
    "get_pair_correlation",
}

_DATABASE_EXPORTS = {
    "ForexTradeJournal",
    "ProposalStatus",
    "TradeStatus",
    "TradeExitReason",
    "ProposalRecord",
    "TradeJournalRecord",
    "OrderExecutionRecord",
    "StrategyVersionRecord",
    "run_migrations",
    "get_current_schema_version",
}

_GRAPH_EXPORTS = {
    "ForexTradingAgentsGraph",
    "ForexGraphSetup",
}

_MT5_EXPORTS = {
    "MT5ConnectionManager",
    "MT5ConnectionStatus",
    "MT5Observer",
    "MT5AccountInfo",
    "MT5SymbolInfo",
    "MT5Tick",
    "MT5Position",
    "MT5Order",
    "MT5Deal",
    "MT5Error",
    "MT5NotInstalledError",
    "MT5ConnectionError",
    "MT5TerminalNotFoundError",
    "MT5AuthorizationError",
    "MT5SymbolError",
    "MT5DataError",
}

_JOURNAL_EXPORTS = {
    "ForexJournalManager",
    "TradeLifecycleManager",
    "LifecycleState",
    "LifecycleError",
    "LifecycleTransitionError",
    "EventTimeline",
    "TradeEvent",
    "EventType",
    "ProposalMatcher",
    "MatchConfidence",
    "MatchResult",
    "PostTradeAnalytics",
    "PerformanceReport",
    "PairMetrics",
    "SetupMetrics",
    "SessionMetrics",
}

_METRICS_EXPORTS = {
    "ForexMetricsManager",
    "TradeMfeMae",
    "TradeOutcomeCategory",
    "TradeOutcomeResult",
    "ExecutionQuality",
    "SlippageType",
    "MetricsSummary",
    "calculate_mfe_mae",
    "calculate_trade_mfe_mae",
    "TradeOutcomeEngine",
    "ExecutionQualityAnalyzer",
    "calculate_execution_slippage",
    "calculate_slippage_cost",
    "calculate_spread_cost",
    "calculate_execution_quality_score",
}

_LEARNING_EXPORTS = {
    "ForexLearningManager",
    "ForexReflectionAgent",
    "LessonRetriever",
    "ForexLessonStore",
    "ForexLesson",
    "RetrievedLesson",
    "ReflectionRating",
    "TradeReflection",
    "ReflectionContext",
}

_BACKTEST_EXPORTS = {
    "ForexBacktestConfig",
    "BacktestTrade",
    "EquityPoint",
    "ForexBacktestResult",
    "ForexBacktestEngine",
    "run_forex_backtest",
}

_ANALYTICS_EXPORTS = {
    "MonteCarloSimulator",
    "MonteCarloConfig",
    "MonteCarloResult",
    "DrawdownPercentiles",
    "TerminalWealthPercentiles",
    "DeepMetrics",
    "DrawdownEpisode",
    "SQNRating",
    "calculate_deep_metrics",
    "RiskStopCalibrator",
    "StopTargetCalibration",
    "ForexAblationEngine",
    "AblationVariantResult",
    "AblationStudyResult",
    "ForexAnalyticsDashboard",
    "ExecutiveAnalyticsDashboard",
    "ForexAnalyticsManager",
}


def __getattr__(name: str):
    if name in _DATAFLOWS_EXPORTS:
        from tradingagents.dataflows import forex_data
        return getattr(forex_data, name)
    if name in _SCHEMAS_EXPORTS:
        from tradingagents.agents import schemas_forex
        return getattr(schemas_forex, name)
    if name in _RISK_EXPORTS:
        from tradingagents.risk import engine
        return getattr(engine, name)
    if name in _SIZING_EXPORTS:
        from tradingagents.risk import sizing
        return getattr(sizing, name)
    if name in _DATABASE_EXPORTS:
        from tradingagents import database
        return getattr(database, name)
    if name in _GRAPH_EXPORTS:
        from tradingagents.graph import forex_graph
        return getattr(forex_graph, name)
    if name in _MT5_EXPORTS:
        from tradingagents import mt5
        return getattr(mt5, name)
    if name in _JOURNAL_EXPORTS:
        from tradingagents import journal
        return getattr(journal, name)
    if name in _METRICS_EXPORTS:
        from tradingagents import metrics
        return getattr(metrics, name)
    if name in _LEARNING_EXPORTS:
        from tradingagents import learning
        return getattr(learning, name)
    if name in _BACKTEST_EXPORTS:
        from tradingagents.backtest import forex_engine
        return getattr(forex_engine, name)
    if name in _ANALYTICS_EXPORTS:
        from tradingagents import analytics
        return getattr(analytics, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(
        list(globals().keys())
        + list(_DATAFLOWS_EXPORTS)
        + list(_SCHEMAS_EXPORTS)
        + list(_RISK_EXPORTS)
        + list(_SIZING_EXPORTS)
        + list(_DATABASE_EXPORTS)
        + list(_GRAPH_EXPORTS)
        + list(_MT5_EXPORTS)
        + list(_JOURNAL_EXPORTS)
        + list(_METRICS_EXPORTS)
        + list(_LEARNING_EXPORTS)
        + list(_BACKTEST_EXPORTS)
        + list(_ANALYTICS_EXPORTS)
    )


__all__ = [
    # Domain objects
    "AssetType",
    "ForexPair",
    "Timeframe",
    "MAJOR_PAIRS",
    "MINOR_PAIRS",
    "EXOTIC_PAIRS",
    "is_forex_pair",
    "get_forex_pair",
    "normalize_forex_pair",
    # Pip calculations
    "pip_size_for",
    "pips_between",
    "pips_to_price",
    "price_to_pips",
    "lot_size_from_risk",
    "pip_value_in_account_currency",
    # Symbol mapping
    "ForexSymbolMap",
    "canonical_to_yahoo",
    "canonical_to_broker",
    "broker_to_canonical",
    "strip_broker_suffix",
    # Sessions and Market Hours
    "TradingSession",
    "SessionInfo",
    "SessionOverlap",
    "MarketRegime",
    "is_market_open",
    "is_weekend",
    "is_holiday",
    "get_active_sessions",
    "get_session_overlaps",
    "get_next_session_open",
    "get_relevant_sessions_for_pair",
    "session_for_pair",
    "is_pair_in_prime_session",
    "get_active_sessions_for_pair",
    "get_market_status",
    "format_session_summary",
    "OVERLAP_LONDON_NY",
    "OVERLAP_TOKYO_SYDNEY",
    "OVERLAP_LONDON_TOKYO",
    # Market Data & Multi-Timeframe Candles
    "ForexBar",
    "MultiTimeframeData",
    "fetch_forex_candles",
    "fetch_multi_timeframe_data",
    "resample_candles",
    "validate_forex_candles",
    "filter_candles_by_cutoff",
    "format_forex_candles_csv",
    "format_multi_timeframe_summary",
    # Quantitative Technical Indicators
    "TrendRegime",
    "RSIRegime",
    "TrendStrength",
    "ForexIndicatorSnapshot",
    "calculate_sma",
    "calculate_ema",
    "calculate_atr",
    "calculate_atr_pips",
    "calculate_atr_percent",
    "calculate_spread_atr_ratio",
    "is_spread_favorable",
    "calculate_rsi",
    "calculate_macd",
    "calculate_stochastic",
    "calculate_adx",
    "calculate_bollinger_bands",
    "classify_ema_trend",
    "classify_rsi_regime",
    "classify_adx_strength",
    "compute_forex_indicators",
    "build_forex_indicator_snapshot",
    "compute_multi_timeframe_indicators",
    "format_indicator_snapshot",
    "format_multi_timeframe_indicators_summary",
    # Market Structure Engine
    "SwingType",
    "SwingTag",
    "StructureTrend",
    "BreakType",
    "BreakDirection",
    "FVGType",
    "OBType",
    "AlignmentBias",
    "SwingPoint",
    "StructuralBreak",
    "FairValueGap",
    "OrderBlock",
    "MarketStructureSnapshot",
    "MultiTimeframeStructureAlignment",
    "find_swing_points",
    "detect_structural_breaks",
    "classify_structure_trend",
    "find_fair_value_gaps",
    "find_order_blocks",
    "find_support_resistance_levels",
    "build_market_structure_snapshot",
    "analyze_multi_timeframe_structure",
    "format_market_structure_snapshot",
    "format_multi_timeframe_structure_summary",
    # Economic Calendar & Events
    "EventImpact",
    "EventSurpriseDirection",
    "EventRiskRegime",
    "TradingActionRecommendation",
    "EconomicEvent",
    "EventRiskAssessment",
    "PairEventBias",
    "get_calendar_events_for_pair",
    "evaluate_event_risk_regime",
    "calculate_pair_event_bias",
    "format_economic_calendar_table",
    # Forex Proposals & Risk Decisions (Phase 9)
    "ForexAction",
    "OrderType",
    "SetupType",
    "ForexRiskDecisionAction",
    "ForexTraderProposal",
    "ForexRiskDecision",
    "render_forex_trader_proposal",
    "render_forex_risk_decision",
    "compute_pips_and_rr",
    "from_trader_proposal",
    # Forex Risk Engine (Phase 10)
    "ForexRiskEngine",
    "ForexRiskLimits",
    "validate_forex_proposal",
    # Forex Position Sizing & Margin Engine (Phase 11)
    "PositionSizingMethod",
    "ForexAccountProfile",
    "BrokerExecutionConstraints",
    "OpenPosition",
    "PositionSizingResult",
    "ForexPositionSizingEngine",
    "calculate_required_margin",
    "calculate_volatility_adjusted_risk_percent",
    "calculate_kelly_risk_percent",
    "calculate_portfolio_currency_exposure",
    "calculate_correlation_exposure_factor",
    "calculate_forex_position_size",
    "get_pair_correlation",
    # SQLite Trade Journal & Storage (Phase 12)
    "ForexTradeJournal",
    "ProposalStatus",
    "TradeStatus",
    "TradeExitReason",
    "ProposalRecord",
    "TradeJournalRecord",
    "OrderExecutionRecord",
    "StrategyVersionRecord",
    "run_migrations",
    "get_current_schema_version",
    # Forex LangGraph Workflow Orchestration (Phase 13)
    "ForexTradingAgentsGraph",
    "ForexGraphSetup",
    # MetaTrader 5 Adapter (Phase 14)
    "MT5ConnectionManager",
    "MT5ConnectionStatus",
    "MT5Observer",
    "MT5AccountInfo",
    "MT5SymbolInfo",
    "MT5Tick",
    "MT5Position",
    "MT5Order",
    "MT5Deal",
    "MT5Error",
    "MT5NotInstalledError",
    "MT5ConnectionError",
    "MT5TerminalNotFoundError",
    "MT5AuthorizationError",
    "MT5SymbolError",
    "MT5DataError",
    # Forex Journal, Lifecycle, Timeline & Analytics (Phase 15)
    "ForexJournalManager",
    "TradeLifecycleManager",
    "LifecycleState",
    "LifecycleError",
    "LifecycleTransitionError",
    "EventTimeline",
    "TradeEvent",
    "EventType",
    "ProposalMatcher",
    "MatchConfidence",
    "MatchResult",
    "PostTradeAnalytics",
    "PerformanceReport",
    "PairMetrics",
    "SetupMetrics",
    "SessionMetrics",
    # Forex Metrics, MFE/MAE, Outcome Engine & Execution Quality (Phase 16)
    "ForexMetricsManager",
    "TradeMfeMae",
    "TradeOutcomeCategory",
    "TradeOutcomeResult",
    "ExecutionQuality",
    "SlippageType",
    "MetricsSummary",
    "calculate_mfe_mae",
    "calculate_trade_mfe_mae",
    "TradeOutcomeEngine",
    "ExecutionQualityAnalyzer",
    "calculate_execution_slippage",
    "calculate_slippage_cost",
    "calculate_spread_cost",
    "calculate_execution_quality_score",
    # Forex Learning, Reflection Agent & Lesson Retrieval (Phase 17)
    "ForexLearningManager",
    "ForexReflectionAgent",
    "LessonRetriever",
    "ForexLessonStore",
    "ForexLesson",
    "RetrievedLesson",
    "ReflectionRating",
    "TradeReflection",
    "ReflectionContext",
    # Forex Point-in-Time Safe Backtesting Engine (Phase 18)
    "ForexBacktestConfig",
    "BacktestTrade",
    "EquityPoint",
    "ForexBacktestResult",
    "ForexBacktestEngine",
    "run_forex_backtest",
    # Forex Quantitative Analytics, Calibration & Ablation (Phase 19)
    "MonteCarloSimulator",
    "MonteCarloConfig",
    "MonteCarloResult",
    "DrawdownPercentiles",
    "TerminalWealthPercentiles",
    "DeepMetrics",
    "DrawdownEpisode",
    "SQNRating",
    "calculate_deep_metrics",
    "RiskStopCalibrator",
    "StopTargetCalibration",
    "ForexAblationEngine",
    "AblationVariantResult",
    "AblationStudyResult",
    "ForexAnalyticsDashboard",
    "ExecutiveAnalyticsDashboard",
    "ForexAnalyticsManager",
]




