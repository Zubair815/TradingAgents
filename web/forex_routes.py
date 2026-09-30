"""FastAPI REST and SSE Endpoints for Forex Trading Platform (Phase 20).

Provides:
1. Journal & Trade Lifecycle:
   - Querying trade records, executions, timeline audit logs, and performance reports.
   - Recording open positions, closing trades, SL/TP modifications, partial scale-outs, and reflections.
2. Read-Only MetaTrader 5 Adapter:
   - Terminal connection status, broker credentials inspection, and live account metrics.
   - Market tick streaming, contract specifications, open positions, pending orders, and historical deals.
3. Proposals, Risk Decisions & Sizing:
   - Proposal submission, querying, status updating, and reconciliation with MT5 positions.
   - Deterministic risk engine validation and institutional margin position sizing.
4. Forex Agent Analysis & SSE Streaming:
   - Real graph execution with SSE progress; failures never produce successful reports.
5. Demonstration Backtesting:
   - Explicit demo opt-in, isolated simulation results, and labeled reports.
6. Quantitative Analytics & Diagnostics:
   - Executive dashboard, deep metrics (Calmar, Ulcer, SQN), Monte Carlo simulation,
     stop/target calibration, and multi-agent ablation studies.
7. Post-Trade Learning:
   - Heuristic extraction, memory store queries, and contextual prompt retrieval.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

# Forex Domain, Journal, Risk, Backtest, Analytics, MT5, Learning imports
from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.analytics.manager import ForexAnalyticsManager
from tradingagents.analytics.metrics import calculate_deep_metrics
from tradingagents.backtest.agent_backtester import (
    AgentBacktestConfig,
    HistoricalForexAgentBacktester,
    estimate_agent_analyses,
)
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestEngine,
)
from tradingagents.backtest.walk_forward import ForexWalkForwardValidator
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalStatus,
    TradeExitReason,
    TradeStatus,
)
from tradingagents.dataflows.config import (
    get_config as get_runtime_config,
    reset_runtime_settings,
    save_runtime_settings,
)
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex import ForexTradingAgentsGraph
from tradingagents.forex.domain import (
    MAJOR_PAIRS,
    normalize_forex_pair,
)
from tradingagents.forex.pips import pip_size_for
from tradingagents.journal.lifecycle import LifecycleError, LifecycleTransitionError
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.models import LifecycleState
from tradingagents.learning.manager import ForexLearningManager
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.mt5.errors import MT5Error
from tradingagents.mt5.observer import MT5Observer
from tradingagents.research.contracts import AnalysisRequest as ForexAnalysisRequest
from tradingagents.risk.engine import ForexRiskEngine, ForexRiskLimits
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    ForexPositionSizingEngine,
    PositionSizingMethod,
)

logger = logging.getLogger("tradingagents.web.forex")

from web.forex_security import ForexRoute, verify_forex_auth  # noqa: E402
from web.retention import (  # noqa: E402
    DEFAULT_RETENTION_POLICY,
    TERMINAL_STATUSES,
    append_bounded,
    oldest_excess_ids,
    prune_tombstones,
    record_timestamp,
    timestamp,
)

router = APIRouter(prefix="/api/forex", tags=["forex"],
                   dependencies=[Depends(verify_forex_auth)], route_class=ForexRoute)

# ---------------------------------------------------------------------------
# Global State & Singletons (with thread-safe overrides for tests)
# ---------------------------------------------------------------------------
_lock = threading.RLock()
_journal: ForexTradeJournal | None = None
_journal_mgr: ForexJournalManager | None = None
_analytics_mgr: ForexAnalyticsManager | None = None
_metrics_mgr: ForexMetricsManager | None = None
_learning_mgr: ForexLearningManager | None = None
_mt5_observer: MT5Observer | None = None
_forex_runtime = None
_forex_analysis_threads: set[threading.Thread] = set()

# These stores are deliberately non-authoritative. Trades, proposals, broker
# deals, timeline events, lessons, and metrics evidence remain in SQLite.
_forex_runs: dict[str, dict[str, Any]] = {}  # TRANSIENT UI run metadata
_forex_run_events: dict[str, list[dict[str, Any]]] = {}  # TRANSIENT SSE buffers
_forex_completed_reports: dict[str, dict[str, Any]] = {}  # CACHE/transient report copies
_backtest_runs: dict[str, dict[str, Any]] = {}  # TRANSIENT result cache
_expired_forex_runs: dict[str, dict[str, Any]] = {}
_expired_backtests: dict[str, dict[str, Any]] = {}
_FOREX_RETENTION_POLICY = DEFAULT_RETENTION_POLICY
_last_forex_prune_at: float | None = None


def _prune_expired_forex_runs(*, now: float | None = None) -> list[str]:
    """Bound transient Forex analyses and backtests without touching journal truth."""
    global _last_forex_prune_at
    current = time.time() if now is None else now
    with _lock:
        expired: set[str] = set()
        terminal_ids: list[str] = []
        for run_id, run in list(_forex_runs.items()):
            status = str(run.get("status", "")).lower()
            is_terminal = status in TERMINAL_STATUSES
            stamp = (
                record_timestamp(run, terminal=True)
                if is_terminal
                else timestamp(run.get("last_activity_at")) or record_timestamp(run)
            )
            if is_terminal:
                terminal_ids.append(run_id)
                if stamp is None or current - stamp > _FOREX_RETENTION_POLICY.terminal_max_age_seconds:
                    expired.add(run_id)
            elif stamp is None or current - stamp > _FOREX_RETENTION_POLICY.stale_active_seconds:
                expired.add(run_id)
        expired.update(oldest_excess_ids(
            _forex_runs,
            [item_id for item_id in terminal_ids if item_id not in expired],
            _FOREX_RETENTION_POLICY.terminal_max_count,
            terminal=True,
        ))
        for run_id in sorted(expired):
            run = _forex_runs.pop(run_id, None)
            _forex_run_events.pop(run_id, None)
            if run is None:
                continue
            _expired_forex_runs[run_id] = {
                "expired_at": current,
                "status": run.get("status", "expired"),
            }
            aliases = [
                key for key, report in _forex_completed_reports.items()
                if key == run_id or report.get("run_id") == run_id
            ]
            for key in aliases:
                _forex_completed_reports.pop(key, None)

        backtest_expired = {
            item_id for item_id, item in _backtest_runs.items()
            if (record_timestamp(item, terminal=True) is None)
            or current - (record_timestamp(item, terminal=True) or current)
            > _FOREX_RETENTION_POLICY.backtest_max_age_seconds
        }
        backtest_expired.update(oldest_excess_ids(
            _backtest_runs,
            [item_id for item_id in _backtest_runs if item_id not in backtest_expired],
            _FOREX_RETENTION_POLICY.backtest_max_count,
            terminal=True,
        ))
        for item_id in sorted(backtest_expired):
            if _backtest_runs.pop(item_id, None) is not None:
                _expired_backtests[item_id] = {"expired_at": current, "status": "expired"}
        prune_tombstones(_expired_forex_runs, _FOREX_RETENTION_POLICY, current)
        prune_tombstones(_expired_backtests, _FOREX_RETENTION_POLICY, current)
        _last_forex_prune_at = current
        return sorted(expired)


def _forex_run_not_found(run_id: str) -> None:
    if run_id in _expired_forex_runs:
        raise HTTPException(status_code=410, detail="Run expired from transient cache")
    raise HTTPException(status_code=404, detail="Run not found")


def _backtest_not_found(backtest_id: str) -> None:
    if backtest_id in _expired_backtests:
        raise HTTPException(status_code=410, detail="Backtest expired from transient cache")
    raise HTTPException(status_code=404, detail="Backtest run not found")


def get_retention_diagnostics() -> dict[str, Any]:
    """Return safe aggregate counts for local runtime observability."""
    _prune_expired_forex_runs()
    with _lock:
        active = sum(
            str(run.get("status", "")).lower() not in TERMINAL_STATUSES
            for run in _forex_runs.values()
        )
        terminal = len(_forex_runs) - active
        return {
            "active_run_count": active,
            "cached_completed_run_count": terminal,
            "backtest_cache_count": len(_backtest_runs),
            "last_prune_at": _last_forex_prune_at,
        }


def get_journal() -> ForexTradeJournal:
    """Provide active ForexTradeJournal instance."""
    global _journal
    with _lock:
        if _journal is None:
            _journal = ForexTradeJournal(auto_migrate=True)
        return _journal


def get_journal_manager() -> ForexJournalManager:
    """Provide active ForexJournalManager instance."""
    global _journal_mgr
    with _lock:
        if _journal_mgr is None:
            _journal_mgr = ForexJournalManager(journal=get_journal())
        return _journal_mgr


def get_analytics_manager() -> ForexAnalyticsManager:
    """Provide active ForexAnalyticsManager instance."""
    global _analytics_mgr
    with _lock:
        if _analytics_mgr is None:
            _analytics_mgr = ForexAnalyticsManager(journal=get_journal())
        return _analytics_mgr


def get_metrics_manager() -> ForexMetricsManager:
    """Provide active ForexMetricsManager instance."""
    global _metrics_mgr
    with _lock:
        if _metrics_mgr is None:
            _metrics_mgr = ForexMetricsManager(journal=get_journal())
        return _metrics_mgr


def get_learning_manager() -> ForexLearningManager:
    """Provide active ForexLearningManager instance."""
    global _learning_mgr
    with _lock:
        if _learning_mgr is None:
            from tradingagents.learning.history_provider import MT5TradeHistoryProvider
            _learning_mgr = ForexLearningManager(journal=get_journal(), history_provider=MT5TradeHistoryProvider(get_mt5_observer()))
        return _learning_mgr


def get_mt5_observer() -> MT5Observer:
    """Provide active MT5Observer instance."""
    global _mt5_observer
    with _lock:
        if _mt5_observer is None:
            _mt5_observer = MT5Observer(auto_connect=False)
        return _mt5_observer


def get_forex_runtime():
    """Authoritative dependency chain for both API requests and application lifespan."""
    global _forex_runtime
    with _lock:
        if _forex_runtime is not None and _forex_runtime.closed:
            reset_forex_state()
        if _forex_runtime is None:
            from web.forex_runtime import ForexRuntime
            _forex_runtime = ForexRuntime(get_mt5_observer(), get_journal_manager(), get_learning_manager())
        return _forex_runtime


def set_forex_dependencies(
    journal: ForexTradeJournal | None = None,
    journal_manager: ForexJournalManager | None = None,
    analytics_manager: ForexAnalyticsManager | None = None,
    metrics_manager: ForexMetricsManager | None = None,
    learning_manager: ForexLearningManager | None = None,
    mt5_observer: MT5Observer | None = None,
) -> None:
    """Inject dependencies for testing or configuration."""
    if _forex_runtime is not None:
        reset_forex_state()
        if _forex_runtime is not None:
            raise RuntimeError("Previous MT5 runtime is still stopping")
    if journal_manager is not None:
        if journal is not None and journal_manager.journal is not journal:
            raise ValueError("Journal manager must use the shared journal")
        journal = journal_manager.journal
    global _journal, _journal_mgr, _analytics_mgr, _metrics_mgr, _learning_mgr, _mt5_observer
    with _lock:
        _journal = journal
        _journal_mgr = journal_manager
        _analytics_mgr = analytics_manager
        _metrics_mgr = metrics_manager
        _learning_mgr = learning_manager
        _mt5_observer = mt5_observer


def reset_forex_state() -> None:
    """Reset all in-memory runs, events, backtests, and dependency singletons."""
    global _journal, _journal_mgr, _analytics_mgr, _metrics_mgr, _learning_mgr, _mt5_observer, _forex_runtime
    with _lock:
        analysis_running = any(thread.is_alive() for thread in _forex_analysis_threads)
    if analysis_running:
        if _forex_runtime is not None:
            _forex_runtime.disconnect(close_journal=False)
        return  # Python threads cannot be killed safely; retain their journal dependencies.
    if _forex_runtime is not None:
        if not _forex_runtime.close():
            return  # Keep the authoritative runtime while a native call drains.
        _forex_runtime = None
    with _lock:
        _journal = None
        _journal_mgr = None
        _analytics_mgr = None
        _metrics_mgr = None
        _learning_mgr = None
        _mt5_observer = None
        _forex_runs.clear()
        _forex_run_events.clear()
        _forex_completed_reports.clear()
        _backtest_runs.clear()
        _expired_forex_runs.clear()
        _expired_backtests.clear()
        _forex_analysis_threads.clear()


_SETTINGS_KEYS = (
    "llm_provider",
    "quick_think_llm",
    "deep_think_llm",
    "backend_url",
    "forex_default_pair",
    "forex_default_execution_timeframe",
    "forex_default_context_timeframes",
    "forex_default_risk_percent",
    "forex_min_rr",
    "forex_market_source",
    "forex_max_spread_pips",
    "forex_news_blackout_minutes",
    "mt5_poll_interval_seconds",
)

_RESTART_REQUIRED_KEYS = frozenset({"mt5_poll_interval_seconds"})


def _serialize_runtime_settings() -> dict[str, Any]:
    cfg = get_runtime_config()
    return {key: cfg.get(key) for key in _SETTINGS_KEYS}


@router.get("/settings")
def get_runtime_settings():
    """Return safe local runtime settings without exposing secret material."""
    settings = _serialize_runtime_settings()
    return {
        "settings": settings,
        "secret_status": {
            "api_key": "Configured" if (os.environ.get("TRADINGAGENTS_DASHBOARD_API_KEY") or os.environ.get("DASHBOARD_API_KEY")) else "Missing",
            "llm_provider_secrets": "Configured" if _provider_secret_is_configured(settings.get("llm_provider")) else "Missing",
        },
        "execution_policy": "manual_only",
        "restart_required": False,
    }


def _provider_secret_is_configured(provider: str | None) -> bool:
    env_names = {
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "ollama": (),
    }
    return provider == "ollama" or any(os.environ.get(name) for name in env_names.get(provider or "", ()))


@router.patch("/settings")
def patch_runtime_settings(payload: dict[str, Any]):
    """Persist non-secret runtime settings to the local settings file."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": "Settings payload must be an object"})
    unknown = sorted(set(payload) - set(_SETTINGS_KEYS))
    if unknown:
        raise HTTPException(status_code=400, detail={"error": f"Unsupported runtime setting(s): {unknown}"})
    before = _serialize_runtime_settings()
    try:
        saved = save_runtime_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    restart_required = _forex_runtime is not None and any(
        key in _RESTART_REQUIRED_KEYS and saved.get(key) != before.get(key) for key in payload
    )
    return {
        "settings": {key: saved.get(key) for key in _SETTINGS_KEYS},
        "saved": True,
        "execution_policy": "manual_only",
        "restart_required": restart_required,
    }


@router.post("/settings/reset")
def reset_runtime_settings_route():
    """Restore the project's default config and drop local runtime overrides."""
    before = _serialize_runtime_settings()
    saved = reset_runtime_settings()
    restart_required = _forex_runtime is not None and any(
        saved.get(key) != before.get(key) for key in _RESTART_REQUIRED_KEYS
    )
    return {
        "settings": {key: saved.get(key) for key in _SETTINGS_KEYS},
        "reset": True,
        "execution_policy": "manual_only",
        "restart_required": restart_required,
    }


# Backward-compatible alias
verify_optional_auth = verify_forex_auth


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class ManualTradeOpenRequest(BaseModel):
    pair: str = Field(..., description="Canonical currency pair (e.g. EURUSD)")
    action: str = Field(..., description="Trade direction: LONG or SHORT")
    entry_price: float = Field(..., gt=0, description="Fill entry price")
    lots: float = Field(..., gt=0, description="Position volume in lots")
    stop_loss: float | None = Field(default=None, description="Stop loss price level")
    take_profit: float | None = Field(default=None, description="Take profit target level")
    proposal_id: str | None = Field(default=None, description="Associated proposal ID if any")
    ticket: int | str | None = Field(default=None, description="Broker ticket number if any")
    open_time: str | None = Field(default=None, description="UTC ISO open timestamp")
    actor: str = Field(default="WebAPI", description="Entity initiating trade")


class TradeCloseRequest(BaseModel):
    close_price: float = Field(..., gt=0, description="Exit price")
    exit_reason: str = Field(default="TAKE_PROFIT", description="Exit reason enum string")
    close_time: str | None = Field(default=None, description="UTC ISO close timestamp")
    gross_profit: float | None = Field(default=None, description="Optional realized gross profit")
    commission: float = Field(default=0.0, ge=0, description="Broker commission fees")
    swap: float = Field(default=0.0, description="Overnight financing charges")
    actor: str = Field(default="WebAPI", description="Entity closing trade")


class ModifyStopLossRequest(BaseModel):
    new_stop_loss: float = Field(..., gt=0, description="New stop loss price")
    reason: str = Field(default="", description="Reason for modification")
    actor: str = Field(default="WebAPI", description="Entity modifying SL")


class ModifyTakeProfitRequest(BaseModel):
    new_take_profit: float = Field(..., gt=0, description="New take profit price")
    reason: str = Field(default="", description="Reason for modification")
    actor: str = Field(default="WebAPI", description="Entity modifying TP")


class PartialCloseRequest(BaseModel):
    lots_to_close: float = Field(..., gt=0, description="Lots to partially close")
    close_price: float = Field(..., gt=0, description="Execution close price")
    exit_reason: str = Field(default="TAKE_PROFIT", description="Reason for partial close")
    actor: str = Field(default="WebAPI", description="Entity executing partial close")


class TradeReflectionRequest(BaseModel):
    reflection_text: str = Field(..., description="Post-trade qualitative reflection")
    category_tag: str | None = Field(default=None, description="Categorization tag")
    execution_quality: str | None = Field(default=None, description="Rating string")


class ProposalCreateRequest(BaseModel):
    pair: str = Field(..., description="Currency pair (e.g. EURUSD)")
    action: str = Field(..., description="Action: LONG, SHORT, NO_TRADE")
    order_type: str = Field(default="MARKET", description="Order type")
    setup_type: str = Field(default="BREAKOUT", description="Strategy setup type")
    timeframe: str = Field(default="H1", description="Trading timeframe")
    entry_price: float = Field(..., gt=0, description="Target entry price")
    stop_loss: float = Field(..., gt=0, description="Stop loss price")
    take_profit: float = Field(..., gt=0, description="Take profit 1 price")
    take_profit_2: float | None = Field(default=None, description="Take profit 2 price")
    suggested_risk_percent: float = Field(default=1.0, gt=0, le=5.0)
    confluence_factors: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="")
    invalidation_condition: str = Field(default="")


class EvaluateRiskRequest(BaseModel):
    proposal_id: str | None = Field(default=None, description="Proposal ID if loading from journal")
    proposal: dict[str, Any] | None = Field(default=None, description="Raw proposal dict if evaluating ad-hoc")
    account_balance: float = Field(default=100000.0, gt=0)
    spread_pips: float = Field(default=1.2, ge=0)
    current_time: str | None = Field(default=None, description="Optional UTC ISO timestamp")


class PositionSizeRequest(BaseModel):
    proposal_id: str | None = Field(default=None, description="Proposal ID if loading from journal")
    proposal: dict[str, Any] | None = Field(default=None, description="Raw proposal dict if sizing ad-hoc")
    method: str = Field(default="FIXED_RISK_PERCENT", description="Position sizing model")
    account_balance: float = Field(default=100000.0, gt=0)
    account_currency: str = Field(default="USD")
    leverage: float = Field(default=100.0, gt=0)
    risk_percent: float = Field(default=1.0, gt=0, le=10.0)


class UpdateProposalStatusRequest(BaseModel):
    status: str = Field(..., description="New ProposalStatus or user action: EXECUTED, SKIPPED, WAIT, APPROVED, REJECTED, CANCELLED, EXPIRED")
    reason: str = Field(default="", description="Optional rationale or context for status transition")


class ReconcilePositionsRequest(BaseModel):
    auto_reconcile: bool = Field(default=True, description="Automatically link matched positions")


class MT5ConnectRequest(BaseModel):
    path: str | None = Field(default=None, description="Terminal executable path")
    login: int | None = Field(default=None, description="Account login")
    password: str | None = Field(default=None, repr=False, description="Broker account password")
    server: str | None = Field(default=None, description="Broker server name")


class ForexBacktestRequest(BaseModel):
    demo_mode: bool = Field(
        default=False,
        description="Explicitly opt into the demonstration strategy; results are not validated strategy performance",
    )
    mode: str | None = Field(
        default=None,
        description="Backtest mode: 'DEMO' or 'HISTORICAL_AGENT_BACKTEST'",
    )
    pair: str = Field(default="EURUSD", description="Currency pair")
    timeframe: str = Field(default="M15", description="Execution timeframe")
    date_from: str | None = Field(default=None, description="Start date YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="End date YYYY-MM-DD")
    initial_balance: float = Field(default=100000.0, gt=0)
    account_currency: str = Field(default="USD")
    leverage: float = Field(default=100.0, gt=0)
    spread_pips: float = Field(default=1.2, ge=0)
    slippage_pips: float = Field(default=0.3, ge=0)
    commission_per_lot_usd: float = Field(default=5.0, ge=0)
    swap_per_day_usd: float = Field(default=0.0, ge=0)
    conservative_stops: bool = Field(default=True)
    max_open_trades: int = Field(default=5, ge=1)
    sampling_interval: int = Field(default=1, ge=1, description="Interval in bars between agent analysis runs")
    max_analysis_points: int | None = Field(default=None, ge=1, description="Max AI evaluations to run")
    analyst_selection: list[str] | None = Field(default=None, description="Active analysts for backtesting")
    provider: str | None = Field(default=None, description="LLM provider: openai, google, anthropic")
    quick_model: str | None = Field(default=None, description="Quick thinking model")
    deep_model: str | None = Field(default=None, description="Deep reasoning model")
    token_limits: int | None = Field(default=None, ge=1, description="Max token limit per call")
    research_depth: Literal["standard", "deep"] = Field(default="standard", description="Research depth: standard or deep")
    candles: list[dict[str, Any]] | None = Field(
        default=None, description="Optional custom OHLCV candle records"
    )
    count: int = Field(default=300, ge=20, le=5000, description="Generated candle count for the synthetic demo")


class ForexBacktestEstimateRequest(BaseModel):
    pair: str = Field(default="EURUSD")
    timeframe: str = Field(default="H1")
    count: int = Field(default=300, ge=1)
    sampling_interval: int = Field(default=1, ge=1)
    max_analysis_points: int | None = Field(default=None, ge=1)
    analyst_count: int = Field(default=3, ge=1)


BacktestMode = Literal["DEMO", "HISTORICAL_AGENT_BACKTEST", "WALK_FORWARD"]


class BacktestRunSummary(BaseModel):
    """Canonical list representation shared by every persisted validation run."""

    model_config = ConfigDict(extra="forbid")

    backtest_id: str
    run_type: Literal["FOREX_BACKTEST"] = "FOREX_BACKTEST"
    mode: BacktestMode
    status: str
    pair: str
    timeframe: str
    created_at: str
    data_source: str
    validation_status: str
    validated_strategy_performance: bool = False
    notice: str
    total_trades: int | None = None
    win_rate_pct: float | None = None
    profit_factor: float | None = None
    net_profit: float | None = None
    split_count: int | None = None


class BacktestRunDetail(BaseModel):
    """Canonical detail envelope; mode-specific evidence remains structured."""

    model_config = ConfigDict(extra="allow")

    backtest_id: str
    run_type: Literal["FOREX_BACKTEST"] = "FOREX_BACKTEST"
    mode: BacktestMode
    status: str
    pair: str
    timeframe: str
    created_at: str
    data_source: str
    validation_status: str
    validated_strategy_performance: bool = False
    notice: str
    validation_reasons: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None


class BacktestRunListResponse(BaseModel):
    runs: list[BacktestRunSummary]
    count: int


class MonteCarloRequest(BaseModel):
    trials: int = Field(default=1000, ge=100, le=10000)
    initial_capital: float = Field(default=100000.0, gt=0)
    seed: int | None = Field(default=42)


class CalibrationRequest(BaseModel):
    pair: str = Field(default="GLOBAL")
    atr_pips: float | None = Field(default=None, gt=0)


class RetrieveLessonsRequest(BaseModel):
    pair: str = Field(..., description="Target currency pair")
    setup_type: str | None = Field(default=None, description="Strategy setup")
    limit: int = Field(default=5, ge=1, le=20)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit_fx_event(run_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Record an SSE event for an active Forex analysis run."""
    evt = {"type": event_type, "data": data, "ts": time.time()}
    with _lock:
        if run_id in _forex_runs:
            _forex_runs[run_id]["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        events = _forex_run_events.setdefault(run_id, [])
        evt["_seq"] = int(events[-1].get("_seq", -1)) + 1 if events else 0
        append_bounded(events, evt, _FOREX_RETENTION_POLICY.event_max_count)


def _safe_model_dump(obj: Any) -> Any:
    """Recursively convert Pydantic models, dataclasses, and datetimes to json-serializable dicts."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _safe_model_dump(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _safe_model_dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_safe_model_dump(item) for item in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _normalize_proposal_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Map common aliases like take_profit -> take_profit_1 for ForexTraderProposal."""
    d = dict(data)
    if "take_profit" in d and "take_profit_1" not in d:
        d["take_profit_1"] = d.pop("take_profit")
    d.setdefault("reasoning", "Evaluated proposal")
    return d


def mask_account_login(login: Any) -> str:
    """Mask account login for security, showing only the first 3 characters."""
    if not login:
        return "Not Set"
    s = str(login).strip()
    if len(s) <= 3:
        return "***"
    return s[:3] + "*" * (len(s) - 3)


# ---------------------------------------------------------------------------
# 1. Journal & Trade Lifecycle Endpoints
# ---------------------------------------------------------------------------

@router.get("/journal/trades")
async def list_trades(
    pair: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Query stored trades with optional pair and status filters."""
    trade_status = None
    if status:
        try:
            trade_status = TradeStatus(status.upper())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid trade status: {status}") from exc


    trades = journal.list_trades(pair=pair, status=trade_status, limit=limit + offset)
    sliced_trades = trades[offset : offset + limit]
    return {"trades": [_safe_model_dump(t) for t in sliced_trades], "count": len(sliced_trades)}


@router.get("/journal/trades/{trade_id}")
async def get_trade(
    trade_id: str,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Retrieve full details of a specific trade, executions, and timeline events."""
    trade = journal.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")

    executions = journal.list_executions_for_trade(trade_id)
    # SQLite LIMIT -1 retrieves the complete audit, not the default first 100.
    raw_events = journal.get_events(trade_id=trade_id, limit=-1)
    proposal = journal.get_proposal(trade.proposal_id) if trade.proposal_id else None
    if proposal:
        # Include the proposal/risk prelude, but never another trade's events.
        raw_events += [event for event in journal.get_events(proposal_id=trade.proposal_id, limit=-1)
                       if event.get("trade_id") in (None, trade_id)]
    unique_events = {event["event_id"]: event for event in raw_events}

    def event_order(event):
        try:
            stamp = datetime.fromisoformat(event["timestamp_utc"].replace("Z", "+00:00"))
            return (stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp(), event["event_id"])
        except (ValueError, TypeError, KeyError):
            return (float("inf"), event["event_id"])

    events = sorted(unique_events.values(), key=event_order)
    reflection_event = next((event for event in reversed(events)
                             if event.get("actor") == "ForexReflectionAgent" and
                             event.get("payload", {}).get("rating")), None)

    return {
        "trade": _safe_model_dump(trade),
        "proposal": _safe_model_dump(proposal),
        "executions": [_safe_model_dump(e) for e in executions],
        "events": [_safe_model_dump(ev) for ev in events],
        "original_proposal": _safe_model_dump(proposal.proposal_payload) if proposal and proposal.proposal_payload else None,
        "risk_decision": _safe_model_dump(proposal.risk_decision) if proposal else None,
        "metrics": _safe_model_dump(trade.metadata.get("mfe_mae")),
        "execution_comparison": _safe_model_dump(trade.metadata.get("execution_quality")),
        "reflection": {
            "summary": trade.reflection or None,
            "rating": reflection_event["payload"]["rating"] if reflection_event else None,
            "tags": trade.tags,
        },
        "lessons": journal.list_lessons(trade_id=trade_id, limit=-1),
    }


@router.post("/journal/trades/manual-open")
async def manual_open_trade(
    req: ManualTradeOpenRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Record an open trade directly or from a proposal."""
    try:
        norm_pair = normalize_forex_pair(req.pair)
        action_enum = ForexAction(req.action.upper())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Validation failed: {exc}") from exc


    if req.proposal_id:
        trade_id = journal_mgr.open_trade(
            proposal_id=req.proposal_id,
            open_price=req.entry_price,
            lots=req.lots,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            ticket=req.ticket,
            actor=req.actor,
        )
    else:
        # Record direct trade in journal
        meta = {"broker_ticket": str(req.ticket)} if req.ticket else {}
        trade_rec = journal_mgr.journal.record_trade_open(
            pair=norm_pair,
            action=action_enum,
            open_price=req.entry_price,
            stop_loss=req.stop_loss or 0.0,
            lots=req.lots,
            take_profit=req.take_profit,
            metadata=meta,
            notes=f"Manual open via {req.actor}",
        )
        trade_id = trade_rec.trade_id

    return {"trade_id": trade_id, "status": "OPEN", "message": "Trade opened successfully"}


@router.post("/journal/trades/{trade_id}/close")
async def close_trade(
    trade_id: str,
    req: TradeCloseRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Close and settle an open trade."""
    try:
        reason_enum = TradeExitReason(req.exit_reason.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid exit_reason: {req.exit_reason}") from exc


    try:
        settled = journal_mgr.close_trade(
            trade_id=trade_id,
            close_price=req.close_price,
            exit_reason=reason_enum,
            close_time=req.close_time,
            gross_profit=req.gross_profit,
            commission=req.commission,
            swap=req.swap,
            actor=req.actor,
        )
        return {"trade": _safe_model_dump(settled), "status": "CLOSED"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/journal/trades/{trade_id}/modify-sl")
async def modify_stop_loss(
    trade_id: str,
    req: ModifyStopLossRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Modify stop loss level and automatically detect breakeven."""
    try:
        success = journal_mgr.modify_stop_loss(
            trade_id=trade_id,
            new_stop_loss=req.new_stop_loss,
            reason=req.reason,
            actor=req.actor,
        )
        return {"success": success, "trade_id": trade_id, "new_stop_loss": req.new_stop_loss}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/journal/trades/{trade_id}/modify-tp")
async def modify_take_profit(
    trade_id: str,
    req: ModifyTakeProfitRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Modify take profit target price."""
    try:
        journal_mgr.modify_take_profit(
            trade_id=trade_id,
            new_take_profit=req.new_take_profit,
            reason=req.reason,
            actor=req.actor,
        )
        return {"success": True, "trade_id": trade_id, "new_take_profit": req.new_take_profit}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/journal/trades/{trade_id}/partial-close")
async def partial_close_trade(
    trade_id: str,
    req: PartialCloseRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Scale out of an open trade partially."""
    try:
        reason_enum = TradeExitReason(req.exit_reason.upper())
        result = journal_mgr.partial_close(
            trade_id=trade_id,
            lots_to_close=req.lots_to_close,
            close_price=req.close_price,
            exit_reason=reason_enum,
            actor=req.actor,
        )
        return {"result": _safe_model_dump(result), "status": "PARTIALLY_CLOSED"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/journal/trades/{trade_id}/reflection")
async def update_trade_reflection(
    trade_id: str,
    req: TradeReflectionRequest,
    request: Request,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Update qualitative reflection notes on a settled trade."""
    try:
        success = journal.update_trade_reflection(
            trade_id=trade_id,
            reflection=req.reflection_text,
            tags=[req.category_tag] if req.category_tag else None,
            notes=req.execution_quality,
        )
        if not success:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        trade = journal.get_trade(trade_id)
        return {"trade": _safe_model_dump(trade), "status": "UPDATED"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.get("/journal/summary")
async def get_journal_summary(
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Return high-level institutional KPI statistics across all recorded trades."""
    summary = journal.get_journal_summary()
    return {"summary": _safe_model_dump(summary)}


@router.get("/journal/timeline")
async def get_journal_timeline(
    trade_id: str | None = None,
    proposal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    render_markdown: bool = False,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Retrieve chronological audit timeline events."""
    events = journal_mgr.get_timeline(trade_id=trade_id, proposal_id=proposal_id, limit=limit)
    response: dict[str, Any] = {"events": [_safe_model_dump(ev) for ev in events]}
    if render_markdown:
        response["markdown"] = journal_mgr.render_timeline(trade_id=trade_id, proposal_id=proposal_id)
    return response


@router.get("/journal/performance")
async def get_journal_performance(
    initial_capital: float = Query(default=100000.0, gt=0),
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Generate institutional performance analytics across all closed trades."""
    perf = journal_mgr.compute_performance(initial_capital=initial_capital)
    markdown_card = journal_mgr.render_performance_dashboard(initial_capital=initial_capital)
    return {
        "performance": _safe_model_dump(perf),
        "markdown_dashboard": markdown_card,
    }


# ---------------------------------------------------------------------------
# 2. Proposal Endpoints
# ---------------------------------------------------------------------------

@router.get("/proposals")
async def list_proposals(
    pair: str | None = None,
    status: str | None = None,
    date: str | None = None,
    action: str | None = None,
    setup: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Query proposals from SQLite store with multi-field filtering."""
    prop_status = None
    if status:
        try:
            prop_status = ProposalStatus(status.upper())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid proposal status: {status}") from exc

    start_date = f"{date}T00:00:00" if date else None
    end_date = f"{date}T23:59:59" if date else None

    fetch_limit = 1000 if (action or setup or timeframe) else (limit + offset)
    proposals = journal.list_proposals(
        pair=pair,
        status=prop_status,
        start_date=start_date,
        end_date=end_date,
        limit=fetch_limit,
    )

    if action:
        act_val = action.strip().upper()
        proposals = [
            p for p in proposals
            if (p.action.value if hasattr(p.action, "value") else str(p.action)).upper() == act_val
        ]
    if setup:
        set_val = setup.strip().upper()
        proposals = [
            p for p in proposals
            if (p.setup_type.value if hasattr(p.setup_type, "value") else str(p.setup_type)).upper() == set_val
        ]
    if timeframe:
        tf_val = timeframe.strip().upper()
        proposals = [
            p for p in proposals
            if (p.timeframe or "").strip().upper() == tf_val
        ]

    sliced_proposals = proposals[offset : offset + limit]
    return {"proposals": [_safe_model_dump(p) for p in sliced_proposals], "count": len(sliced_proposals)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Retrieve specific proposal with immutable original evidence, risk review, matched execution, outcome, and lessons."""
    proposal = journal.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")

    prop_dict = _safe_model_dump(proposal)
    matched_trade = journal.get_trade_by_proposal_id(proposal_id)
    matched_dict = _safe_model_dump(matched_trade) if matched_trade else None

    # Retrieve lessons linked to matched trade or pair setup
    lessons = []
    try:
        if matched_trade:
            lessons = journal.list_lessons(trade_id=matched_trade.trade_id)
        if not lessons:
            setup_val = proposal.setup_type.value if hasattr(proposal.setup_type, "value") else str(proposal.setup_type)
            lessons = journal.list_lessons(pair=proposal.pair, setup_type=setup_val)
    except Exception:
        lessons = []

    # Derive final outcome
    final_outcome = None
    status_str = proposal.status.value if hasattr(proposal.status, "value") else str(proposal.status)
    if matched_trade and getattr(matched_trade.status, "value", str(matched_trade.status)) == "CLOSED":
        final_outcome = {
            "status": "CLOSED",
            "pips_gained": matched_trade.pips_gained,
            "r_multiple": matched_trade.r_multiple,
            "net_profit": matched_trade.net_profit,
            "exit_reason": matched_trade.exit_reason.value if hasattr(matched_trade.exit_reason, "value") else str(matched_trade.exit_reason),
            "close_time": matched_trade.close_time_utc,
            "reflection": matched_trade.reflection,
        }
    elif matched_trade:
        final_outcome = {
            "status": "OPEN",
            "current_action": "Live broker position active",
            "open_price": matched_trade.open_price,
            "lots": matched_trade.lots,
        }
    elif status_str in ("EXPIRED", "SKIPPED", "SKIPPED_BY_USER", "CANCELLED", "REJECTED"):
        final_outcome = {
            "status": status_str,
            "reason": (proposal.metadata or {}).get("status_reason") or f"Proposal marked as {status_str}",
        }

    return {
        "proposal": prop_dict,
        "original_proposal": proposal.proposal_payload or prop_dict,
        "risk_review": proposal.risk_decision or {},
        "user_decision": {
            "status": status_str,
            "action": (proposal.metadata or {}).get("user_action") or status_str,
            "updated_at": (proposal.metadata or {}).get("user_action_at") or proposal.created_at_utc,
        },
        "matched_execution": matched_dict,
        "final_outcome": final_outcome,
        "lessons": lessons,
    }



@router.post("/proposals")
async def create_proposal(
    req: ProposalCreateRequest,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Submit and persist a new ForexTraderProposal."""
    try:
        norm_pair = normalize_forex_pair(req.pair)
        action_enum = ForexAction.from_str(req.action)
        order_enum = OrderType.from_str(req.order_type)
        setup_enum = SetupType.from_str(req.setup_type)

        proposal = ForexTraderProposal(
            pair=norm_pair,
            action=action_enum,
            order_type=order_enum,
            setup_type=setup_enum,
            timeframe=req.timeframe,
            entry_price=req.entry_price,
            stop_loss=req.stop_loss,
            take_profit_1=req.take_profit,
            take_profit_2=req.take_profit_2,
            suggested_risk_percent=req.suggested_risk_percent,
            confluence_factors=req.confluence_factors,
            reasoning=req.reasoning,
            invalidation_condition=req.invalidation_condition,
        )
        prop_id = journal.save_proposal(proposal)
        saved = journal.get_proposal(prop_id)
        return {"proposal": _safe_model_dump(saved), "proposal_id": prop_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/proposals/evaluate-risk")
async def evaluate_risk(
    req: EvaluateRiskRequest,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Run deterministic ForexRiskEngine validation on a proposal."""
    try:
        proposal_obj: ForexTraderProposal | None = None
        if req.proposal_id:
            record = journal.get_proposal(req.proposal_id)
            if not record:
                raise HTTPException(status_code=404, detail=f"Proposal {req.proposal_id} not found")
            proposal_obj = record.to_forex_trader_proposal()
        elif req.proposal:
            norm_prop = _normalize_proposal_dict(req.proposal)
            proposal_obj = ForexTraderProposal(**norm_prop)
        else:
            raise HTTPException(status_code=400, detail="Must provide proposal_id or proposal payload")

        engine = ForexRiskEngine()
        curr_d = None
        curr_t = None
        if req.current_time:
            dt = datetime.fromisoformat(req.current_time)
            curr_d = dt.strftime("%Y-%m-%d")
            curr_t = dt.strftime("%H:%M")

        decision = engine.validate_proposal(
            proposal=proposal_obj,
            account_balance=req.account_balance,
            current_spread_pips=req.spread_pips,
            curr_date=curr_d,
            curr_time_utc=curr_t,
        )
        return {
            "decision": _safe_model_dump(decision),
            "is_approved": decision.decision == ForexRiskDecisionAction.APPROVE,
            "action": decision.decision.value,
            "reasons": decision.risk_violations or decision.modifications_required or decision.risk_checks_passed,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/proposals/size")
async def size_proposal(
    req: PositionSizeRequest,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Calculate institutional position sizing and margin requirements."""
    try:
        proposal_obj: ForexTraderProposal | None = None
        if req.proposal_id:
            record = journal.get_proposal(req.proposal_id)
            if not record:
                raise HTTPException(status_code=404, detail=f"Proposal {req.proposal_id} not found")
            proposal_obj = record.to_forex_trader_proposal()
        elif req.proposal:
            norm_prop = _normalize_proposal_dict(req.proposal)
            proposal_obj = ForexTraderProposal(**norm_prop)
        else:
            raise HTTPException(status_code=400, detail="Must provide proposal_id or proposal payload")

        account_profile = ForexAccountProfile(
            balance=req.account_balance,
            equity=req.account_balance,
            free_margin=req.account_balance,
            currency=req.account_currency,
            leverage=req.leverage,
        )
        constraints = BrokerExecutionConstraints()
        sizing_engine = ForexPositionSizingEngine(default_account=account_profile, default_constraints=constraints)

        method_enum = PositionSizingMethod(req.method.upper())
        if req.risk_percent is not None:
            proposal_obj.suggested_risk_percent = req.risk_percent
        result = sizing_engine.size_proposal(
            proposal=proposal_obj,
            sizing_method=method_enum,
        )
        res_dict = _safe_model_dump(result)
        res_dict["lots"] = result.recommended_lot_size
        return {"sizing": res_dict}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/proposals/{proposal_id}/status")
async def update_proposal_status(
    proposal_id: str,
    req: UpdateProposalStatusRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Update status of a proposal or record user decision (EXECUTED, SKIPPED, WAIT)."""
    journal = journal_mgr.journal
    clean_status = req.status.strip().upper()

    if clean_status in ("EXECUTED", "EXECUTE", "SKIPPED", "SKIP", "SKIPPED_BY_USER", "WAIT", "WAITING", "WAITING_USER"):
        try:
            next_status = journal_mgr.record_user_action(
                proposal_id=proposal_id,
                action=clean_status,
                reason=req.reason,
            )
            saved = journal.get_proposal(proposal_id)
            return {"proposal": _safe_model_dump(saved), "status": next_status.value}
        except LifecycleTransitionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LifecycleError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        new_status = ProposalStatus.from_str(clean_status)
        existing = journal.get_proposal(proposal_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
        curr_state = LifecycleState(existing.status.value)
        next_state = LifecycleState(new_status.value)
        journal_mgr.lifecycle.validate_transition(curr_state, next_state)
        journal.update_proposal_status(proposal_id=proposal_id, status=new_status)
        saved = journal.get_proposal(proposal_id)
        return {"proposal": _safe_model_dump(saved), "status": new_status.value}
    except (LifecycleTransitionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid transition/status: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@router.post("/proposals/reconcile")
async def reconcile_proposals(
    req: ReconcilePositionsRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Reconcile broker executions with active approved proposals."""
    try:
        positions = mt5.get_open_positions()
    except Exception as exc:
        logger.info("MT5 observer positions unavailable for reconciliation (%s)", type(exc).__name__)
        positions = []

    matches = journal_mgr.reconcile_broker_positions(
        positions=positions, auto_reconcile=req.auto_reconcile
    )
    return {"matches": [_safe_model_dump(m) for m in matches], "count": len(matches)}


# ---------------------------------------------------------------------------
# 3. Read-Only MetaTrader 5 Adapter Endpoints
# ---------------------------------------------------------------------------

@router.get("/mt5/status")
async def get_mt5_status(
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Return MT5 connection status and terminal diagnostics."""
    conn = mt5.connection
    is_conn = conn.is_connected()
    status_str = "CONNECTED" if is_conn else "DISCONNECTED"
    login_val = getattr(conn, "login", None)
    return {
        "status": status_str,
        "is_connected": is_conn,
        "connected": is_conn,
        "terminal_path": str(getattr(conn, "path", None) or ""),
        "server": str(conn.server or ""),
        "login": mask_account_login(login_val),
        "account_login": mask_account_login(login_val),
        "masked_login": mask_account_login(login_val),
        **get_forex_runtime().service.status(),
    }


@router.post("/mt5/connect")
async def connect_mt5(
    req: MT5ConnectRequest,
    request: Request,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Attempt connection to MetaTrader 5 terminal."""
    try:
        runtime = get_forex_runtime()
        connected = await asyncio.to_thread(runtime.connect,
            path=req.path,
            login=req.login,
            password=req.password,
            server=req.server,
        )
        return {"connected": connected, "status": "CONNECTED" if connected else "DISCONNECTED",
                **runtime.service.status()}
    except Exception as exc:
        raise HTTPException(503, detail={"code": "MT5_CONNECTION_FAILED"}) from exc



@router.post("/mt5/disconnect")
async def disconnect_mt5(
    request: Request,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Disconnect from MetaTrader 5 terminal."""
    try:
        stopped = await asyncio.to_thread(get_forex_runtime().disconnect)
        return {"status": "DISCONNECTED" if stopped else "STOPPING", "is_connected": False,
                "service_running": get_forex_runtime().service.is_running}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@router.post("/journal/trades/{trade_id}/post-close/retry")
async def retry_post_close(trade_id: str, request: Request):
    """Retry failed post-close analytics without changing broker state."""
    runtime = get_forex_runtime()
    trade = runtime.journal.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != TradeStatus.CLOSED:
        raise HTTPException(status_code=409, detail="Trade is not closed")
    result = await asyncio.to_thread(runtime.processor.process_closed_trade, trade_id)
    return _safe_model_dump(result)


@router.get("/mt5/account")
async def get_mt5_account(
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Fetch live MT5 account balance, equity, leverage, and margins."""
    try:
        info = mt5.get_account_info()
        acc = _safe_model_dump(info)
        res = {"account": acc}
        res.update(acc)
        return res
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@router.get("/mt5/symbols")
async def get_mt5_symbols(
    group: str | None = None,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """List available broker symbols and specs."""
    try:
        symbols = mt5.get_symbols(group=group)
        return {"symbols": [_safe_model_dump(s) for s in symbols], "count": len(symbols)}
    except MT5Error:
        # Fallback to major pairs if offline
        return {
            "symbols": [{"name": p.broker_symbol, "canonical_symbol": p.symbol} for p in MAJOR_PAIRS.values()],
            "count": len(MAJOR_PAIRS),
            "offline": True,
            "error": {"code": "MT5_DISCONNECTED", "message": "MetaTrader 5 is not connected.", "details": {}},
        }


@router.get("/mt5/symbol/{symbol}")
async def get_mt5_symbol_info(
    symbol: str,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Fetch detailed contract specifications and pricing for a symbol."""
    try:
        info = mt5.get_symbol_info(symbol)
        res = {"symbol_info": _safe_model_dump(info)}
        try:
            tick = mt5.get_current_tick(symbol)
            res["tick"] = _safe_model_dump(tick)
        except Exception:
            pass
        return res
    except MT5Error as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@router.get("/mt5/tick/{symbol}")
async def get_mt5_tick(
    symbol: str,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Fetch live market tick (bid, ask, spread) for a symbol."""
    try:
        tick = mt5.get_current_tick(symbol)
        return {"tick": _safe_model_dump(tick)}
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.get("/mt5/positions")
async def get_mt5_positions(
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Query live open broker positions."""
    try:
        positions = mt5.get_open_positions()
        return {"positions": [_safe_model_dump(p) for p in positions], "count": len(positions)}
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.get("/mt5/orders")
async def get_mt5_orders(
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Query pending orders from MT5."""
    try:
        orders = mt5.get_pending_orders()
        return {"orders": [_safe_model_dump(o) for o in orders], "count": len(orders)}
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.get("/mt5/deals")
async def get_mt5_deals(
    date_from: str | None = None,
    date_to: str | None = None,
    position: int | None = None,
    count: int | None = None,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Query execution deals history from MT5."""
    try:
        d_from = datetime.fromisoformat(date_from) if date_from else None
        d_to = datetime.fromisoformat(date_to) if date_to else None
        deals = mt5.get_deals(date_from=d_from, date_to=d_to, position=position, count=count)
        return {"deals": [_safe_model_dump(d) for d in deals], "count": len(deals)}
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



# ---------------------------------------------------------------------------
# 4. Forex Agent Analysis & SSE Streaming
# ---------------------------------------------------------------------------

def _run_forex_analysis(run_id: str, req: ForexAnalysisRequest) -> None:
    """Execute Forex agent analysis pipeline in a background worker thread."""
    try:
        with _lock:
            if run_id in _forex_runs:
                _forex_runs[run_id]["status"] = "running"

        _emit_fx_event(
            run_id,
            "preparing_data",
            {
                "run_id": run_id,
                "pair": req.pair,
                "timeframe": req.timeframe,
                "date": req.date,
                "message": f"Acquiring market data and preparing live context for {req.pair} ({req.timeframe})...",
            },
        )

        config: dict[str, Any] = {}
        if req.provider:
            config["llm_provider"] = req.provider
        if req.quick_model:
            config["quick_think_llm"] = req.quick_model
        if req.deep_model:
            config["deep_think_llm"] = req.deep_model
        if getattr(req, "research_depth", None):
            config["research_depth"] = req.research_depth

        eff_balance = float(req.account_balance)
        eff_equity = float(req.account_balance)
        eff_currency = getattr(req, "account_currency", "USD") or "USD"
        eff_leverage = 100.0

        if getattr(req, "account_source", "mt5") == "mt5":
            mt5_obs = get_mt5_observer()
            if mt5_obs and getattr(mt5_obs, "connection", None) and hasattr(mt5_obs.connection, "is_connected") and mt5_obs.connection.is_connected():
                try:
                    acc = mt5_obs.connection.get_account_info()
                    if acc and getattr(acc, "balance", None):
                        eff_balance = float(acc.balance)
                        eff_equity = float(getattr(acc, "equity", eff_balance) or eff_balance)
                        eff_currency = str(getattr(acc, "currency", eff_currency) or eff_currency)
                        eff_leverage = float(getattr(acc, "leverage", 100.0) or 100.0)
                except Exception:
                    pass

        account = ForexAccountProfile(
            balance=eff_balance,
            equity=eff_equity,
            currency=eff_currency,
            leverage=eff_leverage,
        )

        risk_kwargs: dict[str, Any] = {
            "max_risk_percent": req.risk_percent,
            "default_risk_percent": req.risk_percent,
            "enforce_news_blackout": getattr(req, "economic_blackout", True),
            "blackout_lookahead_hours": get_runtime_config()["forex_news_blackout_minutes"] / 60.0,
        }
        if getattr(req, "min_rr", None) is not None:
            risk_kwargs["min_risk_reward_ratio"] = float(req.min_rr)
        if getattr(req, "max_spread_pips", None) is not None:
            risk_kwargs["max_spread_pips"] = float(req.max_spread_pips)

        risk_limits = ForexRiskLimits(**risk_kwargs)
        journal = get_journal()

        analysts = tuple(req.analysts) if req.analysts else ("forex_technical", "forex_macro", "forex_news")
        graph = ForexTradingAgentsGraph(
            selected_analysts=analysts,
            config=config,
            risk_limits=risk_limits,
            sizing_account=account,
            journal=journal,
            debug=True,
        )

        seen_stages: set[str] = set()

        exec_tf = req.execution_timeframe or req.timeframe or "H1"
        ctx_tfs = req.context_timeframes or req.higher_timeframes or ("H4", "D1")

        for chunk in graph.stream(
            req.pair,
            trade_date=req.date,
            execution_timeframe=exec_tf,
            context_timeframes=ctx_tfs,
        ):
            if not isinstance(chunk, dict):
                continue

            if chunk.get("forex_technical_report") and "technical_analyst" not in seen_stages:
                seen_stages.add("technical_analyst")
                _emit_fx_event(
                    run_id,
                    "technical_analyst",
                    {
                        "stage": "technical_analyst",
                        "pair": req.pair,
                        "message": "Forex Technical Analyst completed market structure analysis.",
                    },
                )

            if chunk.get("forex_macro_report") and "macro_analyst" not in seen_stages:
                seen_stages.add("macro_analyst")
                _emit_fx_event(
                    run_id,
                    "macro_analyst",
                    {
                        "stage": "macro_analyst",
                        "pair": req.pair,
                        "message": "Currency Macro Analyst completed macroeconomic & policy analysis.",
                    },
                )

            if chunk.get("forex_news_report") and "news_analyst" not in seen_stages:
                seen_stages.add("news_analyst")
                _emit_fx_event(
                    run_id,
                    "news_analyst",
                    {
                        "stage": "news_analyst",
                        "pair": req.pair,
                        "message": "Forex News Analyst completed economic calendar risk assessment.",
                    },
                )

            debate = chunk.get("investment_debate_state")
            if debate and isinstance(debate, dict):
                if (debate.get("bull_history") or debate.get("bear_history")) and "bull_bear_debate" not in seen_stages:
                    seen_stages.add("bull_bear_debate")
                    _emit_fx_event(
                        run_id,
                        "bull_bear_debate",
                        {
                            "stage": "bull_bear_debate",
                            "pair": req.pair,
                            "message": "Bull/Bear Researchers conducting thesis debate.",
                        },
                    )

                if debate.get("judge_decision") and "research_manager" not in seen_stages:
                    seen_stages.add("research_manager")
                    _emit_fx_event(
                        run_id,
                        "research_manager",
                        {
                            "stage": "research_manager",
                            "pair": req.pair,
                            "message": "Research Manager synthesized strategic directional consensus.",
                        },
                    )

            if (chunk.get("trader_investment_plan") or chunk.get("forex_proposal")) and "trader" not in seen_stages:
                seen_stages.add("trader")
                _emit_fx_event(
                    run_id,
                    "trader",
                    {
                        "stage": "trader",
                        "pair": req.pair,
                        "message": "Forex Trader formulated order proposal with entry/SL/TP levels.",
                    },
                )

            if chunk.get("forex_risk_decision") and "risk_evaluator" not in seen_stages:
                seen_stages.add("risk_evaluator")
                _emit_fx_event(
                    run_id,
                    "risk_evaluator",
                    {
                        "stage": "risk_evaluator",
                        "pair": req.pair,
                        "message": "Deterministic Risk Evaluator & Sizing Engine audited trade limits.",
                    },
                )

        final_state = graph.get_state() or {}
        signal = graph.process_signal(final_state)
        proposal = graph.get_last_proposal()
        risk_decision = graph.get_last_risk_decision()
        sizing_result = graph.get_last_sizing_result()

        if (
            not final_state.get("final_trade_decision")
            or not isinstance(proposal, ForexTraderProposal)
            or not isinstance(risk_decision, ForexRiskDecision)
        ):
            raise RuntimeError("Forex analysis returned incomplete proposal or risk decision evidence")

        report_path = None
        try:
            report_path = graph.save_reports(final_state, req.pair, trade_date=req.date)
        except Exception as rep_exc:
            logger.warning("Could not write markdown report tree for %s (%s)", run_id, type(rep_exc).__name__)

        report_id = report_path.parent.name if report_path else None
        proposal_id = final_state.get("forex_proposal_id") or getattr(proposal, "proposal_id", None)

        debate_state = final_state.get("investment_debate_state")
        if not isinstance(debate_state, dict):
            debate_state = {}

        applied_lessons = getattr(proposal, "applied_lesson_ids", None) or []
        if not applied_lessons and isinstance(final_state.get("historical_lessons"), list):
            applied_lessons = final_state.get("historical_lessons")

        report_payload = {
            "run_id": run_id,
            "pair": req.pair,
            "timeframe": exec_tf,
            "execution_timeframe": exec_tf,
            "context_timeframes": list(ctx_tfs),
            "analysts": list(req.analysts),
            "date": req.date,
            "signal": signal,
            "proposal_id": proposal_id,
            "proposal": proposal.model_dump() if proposal else None,
            "risk_decision": risk_decision.model_dump() if risk_decision else None,
            "sizing": sizing_result.model_dump() if sizing_result else None,
            "technical_report": final_state.get("forex_technical_report", ""),
            "macro_report": final_state.get("forex_macro_report", ""),
            "news_report": final_state.get("forex_news_report", ""),
            "investment_debate": debate_state,
            "trader_plan": final_state.get("trader_investment_plan", ""),
            "final_decision": final_state.get("final_trade_decision", ""),
            "report": final_state.get("final_trade_decision", ""),
            "report_path": str(report_path) if report_path else None,
            "context": {
                "execution_timeframe": exec_tf,
                "context_timeframes": list(ctx_tfs),
                "session": final_state.get("market_session") or "Active Market Session",
                "spread_pips": float(getattr(risk_decision, "spread_pips", None) or 1.5),
                "volatility_atr": float(getattr(risk_decision, "atr_pips", None) or 45.0),
                "news_risk": "CLEARED" if getattr(req, "economic_blackout", True) else "UNCHECKED",
            },
            "research": {
                "technical": final_state.get("forex_technical_report", ""),
                "macro": final_state.get("forex_macro_report", ""),
                "news": final_state.get("forex_news_report", ""),
                "bull_case": debate_state.get("bull_history", ""),
                "bear_case": debate_state.get("bear_history", ""),
                "manager_synthesis": debate_state.get("judge_decision", ""),
            },
            "memory": {
                "historical_lessons": applied_lessons,
            },
            "provenance": {
                "sources": ["Forex Market Feed (OHLCV)", "Economic Calendar", "Central Bank Intelligence"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "analysis_cutoff": req.date or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            },
        }

        with _lock:
            _forex_completed_reports[run_id] = report_payload
            if report_id:
                _forex_completed_reports[report_id] = report_payload

            if run_id in _forex_runs:
                _forex_runs[run_id].update(
                    status="completed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    signal=signal,
                    proposal_id=proposal_id,
                    report_id=report_id,
                    report_path=str(report_path) if report_path else None,
                )

        _emit_fx_event(
            run_id,
            "complete",
            {
                "run_id": run_id,
                "pair": req.pair,
                "signal": signal,
                "proposal_id": proposal_id,
                "report_id": report_id,
                "message": f"Forex analysis complete: {signal}",
            },
        )

    except Exception as exc:
        error_msg = "The Forex analysis provider failed. Please retry."
        with _lock:
            if run_id in _forex_runs:
                _forex_runs[run_id].update(
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=error_msg,
                    error_code="PROVIDER_ERROR",
                )

        _emit_fx_event(
            run_id,
            "error",
            {
                "run_id": run_id,
                "error": error_msg,
                "code": "PROVIDER_ERROR",
                "message": f"Forex analysis failed: {error_msg}",
            },
        )
        logger.error("Forex analysis failed for run %s (%s)", run_id, type(exc).__name__)


def _forex_worker_entry(run_id: str, req: ForexAnalysisRequest) -> None:
    """Track worker lifetime so shutdown never closes its journal underneath it."""
    try:
        _run_forex_analysis(run_id, req)
    finally:
        with _lock:
            _forex_analysis_threads.discard(threading.current_thread())


@router.post("/analyze")
async def start_forex_analysis(
    req: ForexAnalysisRequest,
    request: Request,
):
    """Start asynchronous Forex multi-agent analysis with live agent execution and SSE updates."""

    if not req.analysts:
        raise HTTPException(status_code=422, detail="At least one Forex analyst must be selected")
    invalid = set(req.analysts) - {"forex_technical", "forex_macro", "forex_news"}
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unsupported analyst(s): {sorted(invalid)}")

    exec_tf = req.execution_timeframe or req.timeframe or "H1"
    ctx_tfs = req.context_timeframes or req.higher_timeframes or ("H4", "D1")
    ctx_tfs_list = list(ctx_tfs)

    run_id = f"fx_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    run_entry = {
        "run_id": run_id,
        "run_type": "forex",
        "asset_type": "forex",
        "pair": req.pair,
        "timeframe": exec_tf,
        "execution_timeframe": exec_tf,
        "context_timeframes": ctx_tfs_list,
        "date": req.date,
        "analysts": list(req.analysts),
        "status": "queued",
        "provider": req.provider or "",
        "quick_model": req.quick_model or "",
        "deep_model": req.deep_model or "",
        "account_balance": req.account_balance,
        "risk_percent": req.risk_percent,
        "research_depth": getattr(req, "research_depth", "deep"),
        "min_rr": getattr(req, "min_rr", None),
        "max_spread_pips": getattr(req, "max_spread_pips", None),
        "economic_blackout": getattr(req, "economic_blackout", True),
        "account_source": getattr(req, "account_source", "mt5"),
        "started_at": now_iso,
        "last_activity_at": now_iso,
        "finished_at": None,
        "error": None,
        "signal": None,
        "proposal_id": None,
        "report_id": None,
        "report_path": None,
    }

    with _lock:
        _forex_runs[run_id] = run_entry
        _forex_run_events[run_id] = []
        _prune_expired_forex_runs()

    worker = threading.Thread(
        target=_forex_worker_entry,
        args=(run_id, req),
        daemon=True,
    )
    with _lock:
        _forex_analysis_threads.add(worker)
    worker.start()

    return {
        "run_id": run_id,
        "status": "queued",
        "pair": req.pair,
        "timeframe": exec_tf,
        "execution_timeframe": exec_tf,
        "context_timeframes": ctx_tfs_list,
        "analysts": list(req.analysts),
        "run_type": "forex",
        "asset_type": "forex",
        "message": f"Forex analysis started for {req.pair}",
    }


@router.get("/runs")
async def list_forex_runs():
    """List all Forex agent analysis runs."""
    _prune_expired_forex_runs()
    def _sort_key(run: dict[str, Any]) -> tuple[float, str]:
        started = run.get("started_at")
        try:
            ts = datetime.fromisoformat(str(started)).timestamp() if started else 0.0
        except ValueError:
            ts = 0.0
        return (float(ts), str(run.get("run_id", "")))

    with _lock:
        runs = sorted((dict(run) for run in _forex_runs.values()), key=_sort_key, reverse=True)
    return {"runs": runs, "count": len(runs)}


@router.post("/runs/{run_id}/cancel")
async def cancel_forex_run(run_id: str):
    """Mark a queued or running Forex job as cancelled so browser clients can stop waiting."""
    with _lock:
        if run_id not in _forex_runs:
            _forex_run_not_found(run_id)
        entry = _forex_runs[run_id]
        status = str(entry.get("status", "")).lower()
        if status in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": entry.get("status", "cancelled")}
        entry.update(
            status="cancelled",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error="Cancelled by user",
        )
    _emit_fx_event(run_id, "cancelled", {
        "run_id": run_id,
        "status": "cancelled",
        "message": "Forex analysis cancelled by user",
    })
    return {"run_id": run_id, "status": "cancelled"}


@router.get("/runs/{run_id}")
async def get_forex_run(run_id: str):
    """Get metadata and report for a specific Forex analysis run."""
    _prune_expired_forex_runs()
    with _lock:
        if run_id not in _forex_runs:
            _forex_run_not_found(run_id)
        return {"run": dict(_forex_runs[run_id]), "report": _forex_completed_reports.get(run_id)}


@router.get("/runs/{run_id}/events")
async def stream_forex_events(run_id: str, request: Request):
    """Server-Sent Events (SSE) streaming endpoint for live agent node progress."""
    _prune_expired_forex_runs()
    with _lock:
        if run_id not in _forex_runs:
            _forex_run_not_found(run_id)

    async def event_generator():
        last_seq = -1
        while True:
            if await request.is_disconnected():
                break

            _prune_expired_forex_runs()
            with _lock:
                run = _forex_runs.get(run_id)
                events = list(_forex_run_events.get(run_id, []))
            if run is None:
                yield 'event: expired\ndata: {"status":"expired"}\n\n'
                return
            for index, evt in enumerate(events):
                sequence = int(evt.get("_seq", index))
                if sequence <= last_seq:
                    continue
                yield f"event: {evt['type']}\ndata: {json.dumps(evt['data'])}\n\n"
                last_seq = sequence

                if evt["type"] in ("complete", "error", "cancelled"):
                    return

            if str(run.get("status", "")).lower() in TERMINAL_STATUSES:
                return

            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Compatibility Aliases for Analysis Routes
# ---------------------------------------------------------------------------

@router.get("/analyze/{run_id}/stream")
async def stream_forex_events_alias(run_id: str, request: Request):
    """Compatibility alias for /runs/{run_id}/events."""
    return await stream_forex_events(run_id, request)


@router.get("/analyze/{run_id}/report")
async def get_forex_report_alias(run_id: str):
    """Compatibility alias for /runs/{run_id} report retrieval."""
    _prune_expired_forex_runs()
    with _lock:
        if run_id not in _forex_runs:
            _forex_run_not_found(run_id)
        report = _forex_completed_reports.get(run_id)
        status = _forex_runs[run_id].get("status", "running")
    if report is None:
        return {"run_id": run_id, "status": status, "report": None}
    return report


@router.get("/analyze/{run_id}/status")
async def get_forex_status_alias(run_id: str):
    """Compatibility alias for run status inspection."""
    _prune_expired_forex_runs()
    with _lock:
        if run_id not in _forex_runs:
            _forex_run_not_found(run_id)
        return dict(_forex_runs[run_id])


# ---------------------------------------------------------------------------
# 5. Demonstration Backtesting Endpoints
# ---------------------------------------------------------------------------

@router.post("/backtest/estimate")
async def estimate_backtest_costs(
    req: ForexBacktestEstimateRequest,
    request: Request,
):
    """Estimate expected AI invocations and token usage before launching an agent backtest."""
    estimate = estimate_agent_analyses(
        total_bars=req.count,
        sampling_interval=req.sampling_interval,
        max_analysis_points=req.max_analysis_points,
        analyst_count=req.analyst_count,
    )
    return estimate.to_dict()


@router.post("/backtest/run")
async def run_backtest(
    req: ForexBacktestRequest,
    request: Request,
):
    """Run an explicitly requested demonstration or real historical agent backtest."""
    effective_mode = req.mode.upper() if req.mode else ("DEMO" if req.demo_mode else None)
    if effective_mode is not None and effective_mode not in ("DEMO", "HISTORICAL_AGENT_BACKTEST"):
        raise HTTPException(status_code=422, detail={"code": "INVALID_BACKTEST_MODE"})

    if effective_mode is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FOREX_BACKTEST_DEMO_ONLY",
                "message": "Production Forex backtesting is unavailable. Set demo_mode=true to run the demonstration strategy or mode='HISTORICAL_AGENT_BACKTEST' for historical agent backtesting.",
            },
        )
    try:
        norm_pair = normalize_forex_pair(req.pair)
        if effective_mode == "HISTORICAL_AGENT_BACKTEST":
            from tradingagents.backtest.historical_data import (
                HistoricalDataUnavailable,
                load_historical_candles,
            )
            if req.candles is not None:
                raise HistoricalDataUnavailable("user-supplied candles have no verified provider provenance")
            candle_objs, provenance = load_historical_candles(
                norm_pair, req.timeframe, req.date_from, req.date_to,
            )
            bt_cfg = ForexBacktestConfig(
                initial_balance=req.initial_balance,
                account_currency=req.account_currency,
                leverage=req.leverage,
                default_spread_pips=req.spread_pips,
                default_slippage_pips=req.slippage_pips,
                commission_per_lot_usd=req.commission_per_lot_usd,
                swap_per_day_usd=req.swap_per_day_usd,
                conservative_stops=req.conservative_stops,
                max_open_trades=req.max_open_trades,
                execution_timeframe=req.timeframe,
            )
            agent_cfg = AgentBacktestConfig(
                pair=norm_pair,
                timeframe=req.timeframe,
                date_from=req.date_from,
                date_to=req.date_to,
                sampling_interval=req.sampling_interval,
                max_analysis_points=req.max_analysis_points,
                analyst_selection=req.analyst_selection or ["forex_technical", "forex_macro", "forex_news"],
                provider=req.provider or "openai",
                quick_model=req.quick_model or "gpt-4.1-mini",
                deep_model=req.deep_model or "gpt-4.1",
                token_limits=req.token_limits,
                research_depth=req.research_depth,
                backtest_config=bt_cfg,
            )

            def graph_f():
                return ForexTradingAgentsGraph(
                    config={
                        "llm_provider": agent_cfg.provider,
                        "quick_think_llm": agent_cfg.quick_model,
                        "deep_think_llm": agent_cfg.deep_model,
                        "max_tokens": agent_cfg.token_limits,
                        "max_debate_rounds": 3 if agent_cfg.research_depth == "deep" else 1,
                        "historical_backtest": True,
                    },
                    selected_analysts=agent_cfg.analyst_selection,
                    auto_record_trades=False,
                )

            agent_backtester = HistoricalForexAgentBacktester(
                config=agent_cfg,
                graph_factory=graph_f,
            )
            report = agent_backtester.run(candles=candle_objs, market_data_provenance=provenance)
            backtest_id = report.backtest_id
            report_dict = _safe_model_dump(report.to_dict())

            backtest_entry = {
                **report_dict,
                "run_type": "FOREX_BACKTEST",
                "status": "completed",
                "demo_mode": False,
                "mode": "HISTORICAL_AGENT_BACKTEST",
                "data_source": provenance["source"],
                "strategy": "historical_multi_agent",
                "notice": "Historical simulation with sourced candles. Strategy performance is not statistically validated.",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            with _lock:
                _backtest_runs[backtest_id] = backtest_entry
                _prune_expired_forex_runs()
            return backtest_entry


        data_source = "user_supplied" if req.candles else "synthetic"
        demo_notice = (
            "DEMO ONLY: Demonstration strategy using "
            + ("generated synthetic candles" if data_source == "synthetic" else "user-supplied candles of unverified provenance")
            + ". This does not evaluate the Forex agent pipeline. "
            "Execution timing and point-in-time correctness are not validated; "
            "results must not be treated as validated strategy performance."
        )
        demo_metadata = {
            "demo_mode": True,
            "data_source": data_source,
            "strategy": "demo_trend_continuation",
            "mode": "DEMO",
            "validation_status": "DEMO",
            "validation_reasons": ["Illustrative/demo only; no strategy validation."],
            "validated_strategy_performance": False,
            "notice": demo_notice,
        }

        pip_sz = pip_size_for(norm_pair)
        base_price = 1.0800 if "JPY" not in norm_pair else 150.00

        # Construct candles
        candle_objs: list[ForexBar] = []
        if req.candles:
            for c in req.candles:
                t_val = c.get("time") or c.get("timestamp") or datetime.now(timezone.utc)
                if isinstance(t_val, str):
                    t_val = datetime.fromisoformat(t_val.replace("Z", "+00:00"))
                candle_objs.append(ForexBar(
                    timestamp=t_val,
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=float(c.get("volume", 100.0)),
                ))
        else:
            # Generate deterministic synthetic trending/oscillating series
            n = req.count
            start_ts = int(time.time()) - (n * 900)
            curr = base_price
            for i in range(n):
                ts = datetime.fromtimestamp(start_ts + (i * 900), tz=timezone.utc)
                delta = math.sin(i / 10.0) * (3.0 * pip_sz) + (0.2 * pip_sz)
                o = curr
                c = curr + delta
                h = max(o, c) + (1.5 * pip_sz)
                low_val = min(o, c) - (1.5 * pip_sz)
                curr = c
                candle_objs.append(ForexBar(timestamp=ts, open=o, high=h, low=low_val, close=c, volume=100.0))



        config = ForexBacktestConfig(
            initial_balance=req.initial_balance,
            account_currency=req.account_currency,
            leverage=req.leverage,
            default_spread_pips=req.spread_pips,
            default_slippage_pips=req.slippage_pips,
            commission_per_lot_usd=req.commission_per_lot_usd,
            conservative_stops=req.conservative_stops,
            max_open_trades=req.max_open_trades,
            execution_timeframe=req.timeframe,
        )

        engine = ForexBacktestEngine(config=config)

        # Simple crossover strategy generator for backtest execution
        def strategy_cb(ts: datetime, pit_candles: list[ForexBar]) -> list[ForexTraderProposal]:
            if len(pit_candles) < 5:
                return []
            last = pit_candles[-1]
            prev = pit_candles[-2]
            if last.close > last.open and prev.close > prev.open and (len(pit_candles) % 15 == 0):
                sl = round(last.close - (15.0 * pip_sz), 5)
                tp = round(last.close + (30.0 * pip_sz), 5)
                return [
                    ForexTraderProposal(
                        pair=norm_pair,
                        action=ForexAction.LONG,
                        order_type=OrderType.MARKET,
                        setup_type=SetupType.TREND_CONTINUATION,
                        timeframe=req.timeframe,
                        entry_price=last.close,
                        stop_loss=sl,
                        take_profit_1=tp,
                        suggested_risk_percent=1.0,
                        reasoning="Backtest trend continuation strategy",
                    )
                ]
            return []

        result = engine.run_candles(
            pair=norm_pair,
            candles=candle_objs,
            strategy_callback=strategy_cb,
        )
        backtest_id = f"bt_{uuid.uuid4().hex[:8]}"

        result_dict = _safe_model_dump(result)
        markdown_rep = f"> {demo_notice}\n\n{result.render_markdown_report()}"

        backtest_entry = {
            **demo_metadata,
            "backtest_id": backtest_id,
            "run_type": "FOREX_BACKTEST",
            "status": "completed",
            "pair": norm_pair,
            "timeframe": req.timeframe,
            "result": result_dict,
            "markdown_report": markdown_rep,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with _lock:
            _backtest_runs[backtest_id] = backtest_entry
            _prune_expired_forex_runs()
        return backtest_entry
    except DataInsufficientError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "HISTORICAL_DATA_UNAVAILABLE", "message": str(exc),
        }) from exc
    except Exception as exc:
        logger.error("Forex backtest failed (%s)", type(exc).__name__)
        if effective_mode == "HISTORICAL_AGENT_BACKTEST":
            raise HTTPException(status_code=400, detail={
                "code": "HISTORICAL_ANALYSIS_FAILED",
                "message": "Historical evaluation did not complete; no performance result was saved.",
            }) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.get("/backtest/runs", response_model=BacktestRunListResponse)
async def list_backtest_runs():
    """List retained transient backtest and validation runs using one contract."""
    _prune_expired_forex_runs()
    with _lock:
        items = [(run_id, dict(run)) for run_id, run in _backtest_runs.items()]
    runs = [_backtest_run_summary(run_id, run).model_dump() for run_id, run in items]
    return {"runs": runs, "count": len(runs)}


def _backtest_run_summary(backtest_id: str, run: dict[str, Any]) -> BacktestRunSummary:
    result = run.get("result") or {}
    validation_report = run.get("validation_report") or {}
    splits = validation_report.get("splits") or []
    return BacktestRunSummary(
        backtest_id=backtest_id,
        run_type=run.get("run_type", "FOREX_BACKTEST"),
        mode=run.get("mode", "DEMO" if run.get("demo_mode") else "HISTORICAL_AGENT_BACKTEST"),
        status=run.get("status", "completed"),
        pair=run.get("pair", ""),
        timeframe=run.get("timeframe", ""),
        created_at=run.get("created_at", ""),
        data_source=run.get("data_source", "unavailable"),
        validation_status=run.get("validation_status", "UNAVAILABLE"),
        validated_strategy_performance=bool(run.get("validated_strategy_performance", False)),
        notice=run.get("notice", ""),
        total_trades=result.get("total_trades"),
        win_rate_pct=result.get("win_rate_pct"),
        profit_factor=result.get("profit_factor"),
        net_profit=result.get("total_net_profit"),
        split_count=len(splits) if run.get("mode") == "WALK_FORWARD" else None,
    )


@router.post("/backtest/walkforward")
async def run_walk_forward(
    req: ForexBacktestRequest,
    n_splits: int = Query(default=1, ge=1, le=10),
):
    """Run a walk-forward validation using historical candles or supplied candles."""
    try:
        norm_pair = normalize_forex_pair(req.pair)
        # Load candles either from user-supplied payload or historical provider
        if req.candles and not req.demo_mode:
            raise DataInsufficientError(
                "Caller-supplied candles have no verified historical provider provenance; "
                "set demo_mode=true for an explicitly unvalidated walk-forward demonstration."
            )
        if req.candles:
            candle_objs = []
            for c in req.candles:
                t_val = c.get("time") or c.get("timestamp") or datetime.now(timezone.utc)
                if isinstance(t_val, str):
                    t_val = datetime.fromisoformat(t_val.replace("Z", "+00:00"))
                candle_objs.append(ForexBar(
                    timestamp=t_val,
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=float(c.get("volume", 100.0)),
                ))
            provenance = {"source": "user_supplied_unverified", "execution_market": False}
        else:
            from tradingagents.backtest.historical_data import load_historical_candles
            candle_objs, provenance = load_historical_candles(norm_pair, req.timeframe, req.date_from, req.date_to)

        validator = ForexWalkForwardValidator(
            strategy_version="v1.0.0",
            config_snapshot={"timeframe": req.timeframe, "provider": req.provider},
            strict_oos_guard=True,
        )
        report = validator.validate(
            candles=candle_objs,
            pair=norm_pair,
            timeframe=req.timeframe,
            backtest_config=ForexBacktestConfig(
                initial_balance=req.initial_balance,
                account_currency=req.account_currency,
                leverage=req.leverage,
                default_spread_pips=req.spread_pips,
                default_slippage_pips=req.slippage_pips,
                commission_per_lot_usd=req.commission_per_lot_usd,
                conservative_stops=req.conservative_stops,
                max_open_trades=req.max_open_trades,
                execution_timeframe=req.timeframe,
            ),
            agent_pipeline_callable=None,
            n_splits=n_splits,
            include_forward_demo=True,
        )

        val_id = report.validation_id
        report_dict = _safe_model_dump(report.to_dict())
        # Store a summary into _backtest_runs for UI listing
        backtest_entry = {
            "backtest_id": val_id,
            "run_type": "FOREX_BACKTEST",
            "status": "completed",
            "mode": "WALK_FORWARD",
            "demo_mode": bool(req.demo_mode),
            "data_source": provenance.get("source", "historical"),
            "strategy": "walk_forward_validation",
            "validated_strategy_performance": False,
            "validation_status": report_dict.get("validation_status", "DEMO"),
            "validation_reasons": report_dict.get("validation_reasons", []),
            "notice": "Walk-forward validation report (descriptive; not statistical validation).",
            "pair": norm_pair,
            "timeframe": req.timeframe,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "market_data_provenance": provenance,
            "markdown_report": report_dict.get("markdown_summary") or report_dict.get("markdown_summary", ""),
            "validation_report": report_dict,
        }
        with _lock:
            _backtest_runs[val_id] = backtest_entry
            _prune_expired_forex_runs()

        return report_dict
    except DataInsufficientError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "HISTORICAL_DATA_UNAVAILABLE", "message": str(exc),
        }) from exc
    except Exception as exc:
        logger.error("Walk-forward validation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=400, detail={
            "code": "WALK_FORWARD_FAILED",
            "message": "Walk-forward validation did not complete; no result was saved.",
        }) from exc


@router.get("/backtest/{backtest_id}", response_model=BacktestRunDetail)
async def get_backtest(backtest_id: str):
    """Retrieve full result scorecard, equity curve, and markdown report for a backtest."""
    _prune_expired_forex_runs()
    with _lock:
        if backtest_id not in _backtest_runs:
            _backtest_not_found(backtest_id)
        run = dict(_backtest_runs[backtest_id])
    return BacktestRunDetail.model_validate(run).model_dump()


# ---------------------------------------------------------------------------
# 6. Quantitative Analytics & Diagnostics Endpoints
# ---------------------------------------------------------------------------

@router.get("/dashboard/overview")
async def get_dashboard_overview(
    journal: ForexTradeJournal = Depends(get_journal),
    mt5: MT5Observer = Depends(get_mt5_observer),
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Consolidated institutional dashboard overview metrics (Phase 25).

    Returns real data only:
    - MT5 connection status, masked account login, server, live balance, equity, margin, free margin, floating P/L
    - Active observed positions and pending orders
    - Recent proposals (last 5)
    - Closed trades overview and today's result
    - Research pipeline state: recent analyses and institutional lessons
    - Performance summary: expectancy, profit factor, average R, max drawdown, with small sample warning.
    """
    conn = mt5.connection
    is_conn = conn.is_connected() if hasattr(conn, "is_connected") else False
    status_str = conn.get_status().value if hasattr(conn, "get_status") else ("CONNECTED" if is_conn else "DISCONNECTED")
    server_str = str(getattr(conn, "server", "") or "")
    login_raw = getattr(conn, "login", None)

    masked_login = mask_account_login(login_raw)

    account_info = None
    if is_conn:
        try:
            acc = conn.get_account_info() if hasattr(conn, "get_account_info") else None
            if acc:
                account_info = {
                    "balance": float(acc.balance or 0.0),
                    "equity": float(acc.equity or 0.0),
                    "margin": float(acc.margin or 0.0),
                    "margin_free": float(acc.margin_free or 0.0),
                    "profit": float(acc.profit or 0.0),
                    "currency": str(acc.currency or "USD"),
                    "leverage": int(acc.leverage or 100),
                }
        except Exception:
            account_info = None

    open_positions: list[dict[str, Any]] = []
    if is_conn:
        try:
            positions = conn.get_positions() if hasattr(conn, "get_positions") else []
            open_positions = [_safe_model_dump(p) for p in positions]
        except Exception:
            open_positions = []

    pending_orders: list[dict[str, Any]] = []
    if is_conn:
        try:
            orders = conn.get_orders() if hasattr(conn, "get_orders") else []
            pending_orders = [_safe_model_dump(o) for o in orders]
        except Exception:
            pending_orders = []

    proposals = journal.list_proposals(limit=5)
    recent_proposals = [_safe_model_dump(p) for p in proposals]
    active_proposals_count = len([p for p in proposals if getattr(p, "status", "") in ("PROPOSED", "APPROVED", "PENDING")])

    all_trades = journal.list_trades(limit=1000)
    recent_trades = [_safe_model_dump(t) for t in all_trades[:5]]

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [
        t for t in all_trades
        if (getattr(t, "close_time_utc", "") or "").startswith(today_str)
    ]
    today_pnl = sum(float(getattr(t, "net_profit", 0.0) or getattr(t, "gross_profit", 0.0) or 0.0) for t in today_trades)
    today_r = sum(float(getattr(t, "r_multiple", 0.0) or 0.0) for t in today_trades)
    today_result = {
        "trade_count": len(today_trades),
        "net_profit": round(today_pnl, 2),
        "total_r": round(today_r, 2),
    }

    _prune_expired_forex_runs()
    with _lock:
        recent_analyses = [dict(run) for run in list(_forex_runs.values())[-5:]]

    upcoming_events: list[dict[str, Any]] = []
    try:
        from tradingagents.forex.calendar import EventImpact, get_calendar_events_for_pair
        today_events = get_calendar_events_for_pair(
            symbol="EURUSD",
            curr_date=today_str,
            min_impact=EventImpact.HIGH,
        )
        upcoming_events = [
            {
                "event_id": ev.event_id,
                "currency": ev.currency,
                "title": ev.title,
                "impact": ev.impact.value if hasattr(ev.impact, "value") else str(ev.impact),
                "date": ev.date,
                "time_utc": ev.time_utc,
                "forecast": ev.forecast,
                "previous": ev.previous,
            }
            for ev in today_events[:5]
        ]
    except Exception:
        upcoming_events = []

    recent_lessons = [_safe_model_dump(lesson) for lesson in learning_mgr.store.list_lessons()[:3]]

    from tradingagents.analytics.performance import ForexPerformanceEngine
    closed_trades = [
        t for t in all_trades
        if getattr(t, "status", None) == TradeStatus.CLOSED or getattr(t, "close_time_utc", None)
    ]
    perf = ForexPerformanceEngine().calculate_metrics(closed_trades)
    is_adequate = len(closed_trades) >= 30
    sample_warning = (
        None if is_adequate
        else f"Sample size warning: only {len(closed_trades)} closed trade(s). At least 30 closed trades required for statistical significance."
    )

    performance_summary = {
        "expectancy": perf.expectancy,
        "profit_factor": perf.profit_factor,
        "average_r": perf.average_r,
        "max_drawdown_pct": perf.maximum_drawdown_pct,
        "win_rate": perf.win_rate,
        "win_rate_pct": perf.win_rate_pct,
        "trade_count": perf.trade_count,
        "net_profit": perf.net_profit,
        "is_sample_size_adequate": is_adequate,
        "sample_warning": sample_warning,
    }

    return {
        "mt5": {
            "connection_status": status_str,
            "is_connected": is_conn,
            "server": server_str,
            "masked_login": masked_login,
            "account": account_info,
            "open_positions": open_positions,
            "pending_orders": pending_orders,
        },
        "trading": {
            "open_positions_count": len(open_positions),
            "pending_orders_count": len(pending_orders),
            "active_proposals_count": active_proposals_count,
            "recent_proposals": recent_proposals,
            "recent_trades": recent_trades,
            "today_result": today_result,
        },
        "research": {
            "recent_analyses": recent_analyses,
            "upcoming_events": upcoming_events,
            "recent_lessons": recent_lessons,
        },
        "performance": performance_summary,
    }


@router.get("/analytics/dashboard")
async def get_analytics_dashboard(
    pair: str = Query(default="GLOBAL"),
    initial_capital: float = Query(default=100000.0, gt=0),
    atr_pips: float | None = Query(default=None),
    analytics_mgr: ForexAnalyticsManager = Depends(get_analytics_manager),
):
    """Generate consolidated institutional performance dashboard."""
    dashboard = analytics_mgr.generate_dashboard(
        pair=pair, initial_capital=initial_capital, atr_pips=atr_pips
    )
    return {
        "dashboard": dashboard.to_dict(),
        "markdown": dashboard.render_markdown_dashboard(),
    }


@router.get("/analytics/performance")
async def get_performance_report(
    initial_capital: float = Query(default=100000.0, gt=0),
    metrics_mgr: ForexMetricsManager = Depends(get_metrics_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Retrieve full deterministic performance analytics, multi-dimensional segmentation, and execution friction (Phase 19)."""
    trades = journal.list_trades(limit=10000)
    proposals = journal.list_proposals(limit=5000)
    events = journal.get_events(limit=10000)
    report = metrics_mgr.get_comprehensive_performance(
        trades=trades, proposals=proposals, events=events, initial_capital=initial_capital
    )

    closed = sorted(
        (trade for trade in trades if getattr(trade, "close_time_utc", None)),
        key=lambda trade: str(getattr(trade, "close_time_utc", "")),
    )
    cumulative_r: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    drawdown: list[dict[str, Any]] = []
    r_distribution: list[dict[str, Any]] = []
    mfe_vs_realized: list[dict[str, Any]] = []
    mae_distribution: list[dict[str, Any]] = []
    running_r = 0.0
    running_equity = initial_capital
    peak_equity = initial_capital
    for trade in closed:
        trade_id = str(getattr(trade, "trade_id", ""))
        timestamp = str(getattr(trade, "close_time_utc", ""))
        metadata = getattr(trade, "metadata", None) or {}
        r_value = getattr(trade, "r_multiple", None)
        net_profit = getattr(trade, "net_profit", None)
        mfe_r = metadata.get("mfe_r")
        mae_r = metadata.get("mae_r")
        if r_value is not None:
            realized_r = float(r_value)
            running_r += realized_r
            cumulative_r.append({"trade_id": trade_id, "timestamp": timestamp, "value": running_r})
            r_distribution.append({"trade_id": trade_id, "value": realized_r})
            if mfe_r is not None:
                mfe_vs_realized.append({"trade_id": trade_id, "mfe_r": float(mfe_r), "realized_r": realized_r})
        if mae_r is not None:
            mae_distribution.append({"trade_id": trade_id, "value": float(mae_r)})
        if net_profit is not None:
            running_equity += float(net_profit)
            peak_equity = max(peak_equity, running_equity)
            equity.append({"trade_id": trade_id, "timestamp": timestamp, "value": running_equity})
            drawdown.append({"trade_id": trade_id, "timestamp": timestamp, "value": peak_equity - running_equity})

    summary = metrics_mgr.compute_summary(trades=trades)
    return {
        "performance": _safe_model_dump(report),
        "series": {
            "cumulative_r": cumulative_r,
            "equity": equity,
            "drawdown": drawdown,
            "r_distribution": r_distribution,
            "mfe_vs_realized": mfe_vs_realized,
            "mae_distribution": mae_distribution,
        },
        "execution_friction": {
            "total_executions_analyzed": summary.total_executions_analyzed,
            "total_execution_friction_usd": summary.total_execution_friction_usd,
            "average_execution_quality_score": summary.avg_execution_quality_score,
        },
        "markdown": report.summary_markdown,
    }


@router.get("/analytics/metrics")
async def get_deep_metrics(
    initial_capital: float = Query(default=100000.0, gt=0),
    analytics_mgr: ForexAnalyticsManager = Depends(get_analytics_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Calculate advanced risk-adjusted metrics (Calmar, Ulcer, SQN, Drawdown episodes)."""
    trades = journal.list_trades(limit=10000)
    equity_curve = [initial_capital]
    curr = initial_capital
    for t in trades:
        profit = getattr(t, "net_pnl", getattr(t, "profit", 0.0)) or 0.0
        curr += profit
        equity_curve.append(curr)

    deep = calculate_deep_metrics(equity_curve=equity_curve, trades=trades, initial_capital=initial_capital)
    return {"metrics": _safe_model_dump(deep)}


@router.post("/analytics/monte-carlo")
async def run_monte_carlo_analysis(
    req: MonteCarloRequest,
    request: Request,
    analytics_mgr: ForexAnalyticsManager = Depends(get_analytics_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Execute Monte Carlo bootstrap resampling and ruin probabilities."""
    trades = journal.list_trades(limit=10000)
    result = analytics_mgr.run_monte_carlo(
        trades=trades,
        trials=req.trials,
        initial_capital=req.initial_capital,
        seed=req.seed,
    )
    return {"monte_carlo": _safe_model_dump(result)}


@router.get("/analytics/calibration")
async def get_risk_calibration(
    pair: str = Query(default="GLOBAL"),
    atr_pips: float | None = Query(default=None),
    analytics_mgr: ForexAnalyticsManager = Depends(get_analytics_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Empirical stop loss and take profit calibration curves from excursion metrics."""
    trades = journal.list_trades(limit=10000)
    calibration = analytics_mgr.calibrate_stops(trades=trades, pair=pair, atr_pips=atr_pips)
    return {"calibration": _safe_model_dump(calibration)}


@router.post("/analytics/ablation")
async def run_ablation_study(
    request: Request,
    req: dict[str, Any] | None = None,
    analytics_mgr: ForexAnalyticsManager = Depends(get_analytics_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Evaluate marginal alpha contributions across agents in the pipeline."""
    trades = journal.list_trades(limit=10000)
    study = analytics_mgr.run_ablation(trades=trades)
    return {"ablation_study": _safe_model_dump(study)}


# ---------------------------------------------------------------------------
# 7. Post-Trade Learning & Memory Retrieval Endpoints
# ---------------------------------------------------------------------------

@router.get("/learning/lessons")
async def list_lessons(
    pair: str | None = None,
    setup_type: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    min_evidence_count: int | None = Query(default=None, ge=0),
    active: bool | None = None,
    tag: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Query stored heuristic lessons from past trade reflections."""
    filters: dict[str, Any] = {"pair": pair, "setup_type": setup_type, "tag": tag}
    if timeframe is not None:
        filters["timeframe"] = timeframe
    if direction is not None:
        filters["direction"] = direction
    if min_evidence_count is not None:
        filters["min_evidence_count"] = min_evidence_count
    if active is not None:
        filters["active"] = active
    if limit != 200:
        filters["limit"] = limit
    lessons = learning_mgr.store.list_lessons(**filters)
    return {"lessons": [_safe_model_dump(les) for les in lessons], "count": len(lessons)}


@router.get("/learning/lessons/{lesson_id}")
async def get_lesson_detail(
    lesson_id: str,
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Return a lesson and explicit availability for its stored source records."""
    lesson = learning_mgr.store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    trade_id = getattr(lesson, "source_trade_id", None)
    proposal_id = getattr(lesson, "proposal_id", None)
    return {
        "lesson": _safe_model_dump(lesson),
        "sources": {
            "trade": {"id": trade_id, "available": bool(trade_id and journal.get_trade(trade_id))},
            "proposal": {"id": proposal_id, "available": bool(proposal_id and journal.get_proposal(proposal_id))},
        },
    }



@router.post("/learning/reflect/{trade_id}")
async def reflect_on_trade(
    trade_id: str,
    request: Request,
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Trigger automated post-trade reflection and lesson extraction for a settled position."""
    try:
        reflection = learning_mgr.reflect_on_trade(trade=trade_id)
        return {"reflection": _safe_model_dump(reflection), "status": "COMPLETED"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.get("/learning/retrieve")
async def retrieve_lessons(
    pair: str = Query(..., description="Target currency pair"),
    timeframe: str | None = Query(default=None),
    setup_type: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    session: str | None = Query(default=None),
    market_regime: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Retrieve ranked contextually relevant heuristics for prompt injection."""
    retrieved = learning_mgr.retriever.retrieve_lessons(
        pair=pair,
        timeframe=timeframe,
        setup_type=setup_type,
        direction=direction,
        session=session,
        market_regime=market_regime,
        limit=limit,
    )
    markdown_prompt = learning_mgr.retriever.format_lessons_for_prompt(retrieved)
    return {
        "lessons": [_safe_model_dump(r) for r in retrieved],
        "applied_lesson_ids": [r.lesson.lesson_id for r in retrieved],
        "markdown_prompt": markdown_prompt,
        "count": len(retrieved),
    }


# ---------------------------------------------------------------------------
# 8. Skipped Proposal Evaluation & Comparative Performance Endpoints (Phase 17)
# ---------------------------------------------------------------------------

@router.get("/metrics/skipped")
async def list_skipped_proposals(
    pair: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Query skipped proposals and their persisted simulation outcomes."""
    props = journal.list_proposals(pair=pair, limit=limit + offset)
    skipped = [
        p for p in props
        if p.status in (ProposalStatus.SKIPPED, ProposalStatus.SKIPPED_BY_USER)
    ]
    sliced = skipped[offset : offset + limit]
    return {
        "skipped_proposals": [_safe_model_dump(p) for p in sliced],
        "count": len(sliced),
        "total_skipped": len(skipped),
    }


@router.post("/metrics/skipped/evaluate")
async def evaluate_skipped_proposals(
    request: Request,
    metrics_mgr: ForexMetricsManager = Depends(get_metrics_manager),
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Run counterfactual market simulation across all skipped/expired proposals."""
    try:
        from tradingagents.learning.history_provider import MT5TradeHistoryProvider
        provider = MT5TradeHistoryProvider(observer=mt5)
        simulations = metrics_mgr.evaluate_skipped_proposals(history_provider=provider)
        return {
            "evaluated_count": len(simulations),
            "simulations": [_safe_model_dump(s) for s in simulations],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/metrics/comparative")
async def get_comparative_performance(
    metrics_mgr: ForexMetricsManager = Depends(get_metrics_manager),
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Benchmark AI theoretical performance against actual human execution and skipped trades."""
    try:
        from tradingagents.learning.history_provider import MT5TradeHistoryProvider
        provider = MT5TradeHistoryProvider(observer=mt5)
        summary = metrics_mgr.get_comparative_performance(history_provider=provider)
        return {"comparative_performance": _safe_model_dump(summary)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 9. Confidence Calibration Endpoints (Phase 18)
# ---------------------------------------------------------------------------


class CalibrateConfidenceRequest(BaseModel):
    confidence: float = Field(ge=0.0, le=100.0, description="Raw model confidence score (0-100 scale)")
    pair: str | None = Field(default=None, description="Optional pair to calibrate against pair-specific historical trades")
    min_samples: int = Field(default=10, ge=1, description="Minimum samples required for calibration")


@router.get("/metrics/confidence-calibration")
async def get_confidence_calibration(
    pair: str | None = None,
    min_samples: int = Query(default=10, ge=1),
    metrics_mgr: ForexMetricsManager = Depends(get_metrics_manager),
):
    """Retrieve full confidence calibration scorecard, bucket metrics, ECE, and sample warnings."""
    report = metrics_mgr.get_confidence_calibration(pair=pair, min_samples=min_samples)
    return {
        "report": _safe_model_dump(report),
        "markdown": report.summary_markdown,
    }


@router.post("/metrics/calibrate-confidence")
async def calibrate_single_confidence(
    req: CalibrateConfidenceRequest,
    metrics_mgr: ForexMetricsManager = Depends(get_metrics_manager),
):
    """Calibrate a single model confidence score against empirical outcomes.

    Strictly guarantees that uncalibrated confidence is never displayed as true probability.
    """
    result = metrics_mgr.calibrate_proposal_confidence(
        raw_confidence=req.confidence,
        pair=req.pair,
    )
    return {"calibrated_result": _safe_model_dump(result)}


