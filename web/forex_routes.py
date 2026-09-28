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
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

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
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestEngine,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalStatus,
    TradeExitReason,
    TradeStatus,
)
from tradingagents.dataflows.forex_data import ForexBar
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

router = APIRouter(prefix="/api/forex", tags=["forex"])

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

# In-memory stores for runs, events, and backtests
_forex_runs: dict[str, dict[str, Any]] = {}
_forex_run_events: dict[str, list[dict[str, Any]]] = {}
_forex_completed_reports: dict[str, dict[str, Any]] = {}
_backtest_runs: dict[str, dict[str, Any]] = {}


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
            _learning_mgr = ForexLearningManager(journal=get_journal())
        return _learning_mgr


def get_mt5_observer() -> MT5Observer:
    """Provide active MT5Observer instance."""
    global _mt5_observer
    with _lock:
        if _mt5_observer is None:
            _mt5_observer = MT5Observer()
        return _mt5_observer


def set_forex_dependencies(
    journal: ForexTradeJournal | None = None,
    journal_manager: ForexJournalManager | None = None,
    analytics_manager: ForexAnalyticsManager | None = None,
    metrics_manager: ForexMetricsManager | None = None,
    learning_manager: ForexLearningManager | None = None,
    mt5_observer: MT5Observer | None = None,
) -> None:
    """Inject dependencies for testing or configuration."""
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
    global _journal, _journal_mgr, _analytics_mgr, _metrics_mgr, _learning_mgr, _mt5_observer
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


def verify_forex_auth(request: Request) -> bool:
    """Enforce authentication on Forex mutation and execution routes.

    Delegates to web.server.verify_auth.
    CRITICAL: Never catch HTTPException — unauthorized requests MUST raise 401.
    """
    try:
        from web.server import verify_auth
    except ImportError:
        return True

    return verify_auth(request)


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
    conservative_stops: bool = Field(default=True)
    max_open_trades: int = Field(default=5, ge=1)
    candles: list[dict[str, Any]] | None = Field(
        default=None, description="Optional custom OHLCV candle records"
    )
    count: int = Field(default=300, ge=20, le=5000, description="Generated candle count for the synthetic demo")


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
    _forex_run_events.setdefault(run_id, []).append(evt)


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
    raw_events = journal.get_events(trade_id=trade_id)
    proposal = journal.get_proposal(trade.proposal_id) if trade.proposal_id else None

    return {
        "trade": _safe_model_dump(trade),
        "proposal": _safe_model_dump(proposal),
        "executions": [_safe_model_dump(e) for e in executions],
        "events": [_safe_model_dump(ev) for ev in raw_events],
    }


@router.post("/journal/trades/manual-open")
async def manual_open_trade(
    req: ManualTradeOpenRequest,
    request: Request,
    journal_mgr: ForexJournalManager = Depends(get_journal_manager),
):
    """Record an open trade directly or from a proposal."""
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Query proposals from SQLite store."""
    prop_status = None
    if status:
        try:
            prop_status = ProposalStatus(status.upper())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid proposal status: {status}") from exc


    proposals = journal.list_proposals(pair=pair, status=prop_status, limit=limit + offset)
    sliced_proposals = proposals[offset : offset + limit]
    return {"proposals": [_safe_model_dump(p) for p in sliced_proposals], "count": len(sliced_proposals)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Retrieve specific proposal by ID."""
    proposal = journal.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    return {"proposal": _safe_model_dump(proposal)}


@router.post("/proposals")
async def create_proposal(
    req: ProposalCreateRequest,
    journal: ForexTradeJournal = Depends(get_journal),
):
    """Submit and persist a new ForexTraderProposal."""
    try:
        norm_pair = normalize_forex_pair(req.pair)
        action_enum = ForexAction(req.action.upper())
        order_enum = OrderType(req.order_type.upper())
        setup_enum = SetupType(req.setup_type.upper())

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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
    try:
        positions = mt5.get_open_positions()
    except Exception as exc:
        logger.info("MT5 observer positions unavailable for reconciliation: %s", exc)
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
    status_str = conn.get_status().value if hasattr(conn, "get_status") else "DISCONNECTED"
    is_conn = conn.is_connected()
    return {
        "status": status_str,
        "is_connected": is_conn,
        "connected": is_conn,
        "terminal_path": str(conn.terminal_path or ""),
        "server": str(conn.server or ""),
        "login": conn.login,
        "account_login": conn.login,
    }


@router.post("/mt5/connect")
async def connect_mt5(
    req: MT5ConnectRequest,
    request: Request,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Attempt connection to MetaTrader 5 terminal."""
    verify_forex_auth(request)
    try:
        conn = mt5.connection
        connected = conn.connect(
            path=req.path,
            login=req.login,
            password=req.password,
            server=req.server,
        )
        return {"connected": connected, "status": conn.get_status().value}
    except MT5Error as exc:
        return JSONResponse(
            status_code=503,
            content={"connected": False, "error": str(exc), "code": getattr(exc, "code", None)},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"connected": False, "error": str(exc)})


@router.post("/mt5/disconnect")
async def disconnect_mt5(
    request: Request,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Disconnect from MetaTrader 5 terminal."""
    verify_forex_auth(request)
    try:
        mt5.connection.disconnect()
        return {"status": "DISCONNECTED", "is_connected": False}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



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
    except MT5Error as exc:
        # Fallback to major pairs if offline
        return {
            "symbols": [{"name": p.broker_symbol, "canonical_symbol": p.symbol} for p in MAJOR_PAIRS.values()],
            "count": len(MAJOR_PAIRS),
            "offline": True,
            "error": str(exc),
        }


@router.get("/mt5/symbol/{symbol}")
async def get_mt5_symbol_info(
    symbol: str,
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Fetch detailed contract specifications and pricing for a symbol."""
    try:
        info = mt5.get_symbol_info(symbol)
        return {"symbol_info": _safe_model_dump(info)}
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
    mt5: MT5Observer = Depends(get_mt5_observer),
):
    """Query execution deals history from MT5."""
    try:
        d_from = datetime.fromisoformat(date_from) if date_from else None
        d_to = datetime.fromisoformat(date_to) if date_to else None
        deals = mt5.get_deals(date_from=d_from, date_to=d_to, position=position)
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

        account = ForexAccountProfile(
            balance=req.account_balance,
            equity=req.account_balance,
        )
        risk_limits = ForexRiskLimits(
            max_risk_percent=req.risk_percent,
            default_risk_percent=req.risk_percent,
        )
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
            logger.warning("Could not write markdown report tree for %s: %s", run_id, rep_exc)

        report_id = report_path.parent.name if report_path else None
        proposal_id = final_state.get("forex_proposal_id") or getattr(proposal, "proposal_id", None)

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
            "investment_debate": final_state.get("investment_debate_state", {}),
            "trader_plan": final_state.get("trader_investment_plan", ""),
            "final_decision": final_state.get("final_trade_decision", ""),
            "report_path": str(report_path) if report_path else None,
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
        error_msg = str(exc)
        if "openrouter.ai/workspaces" in error_msg:
            error_msg = "OpenRouter credit limit exceeded."
        elif "402" in error_msg:
            error_msg = "Provider returned 402 — credit/billing limit reached."

        with _lock:
            if run_id in _forex_runs:
                _forex_runs[run_id].update(
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=error_msg,
                )

        _emit_fx_event(
            run_id,
            "error",
            {
                "run_id": run_id,
                "error": error_msg,
                "message": f"Forex analysis failed: {error_msg}",
            },
        )
        logger.error("Forex analysis failed for run %s: %s", run_id, error_msg, exc_info=True)


@router.post("/analyze")
async def start_forex_analysis(
    req: ForexAnalysisRequest,
    request: Request,
):
    """Start asynchronous Forex multi-agent analysis with live agent execution and SSE updates."""
    verify_optional_auth(request)

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
        "started_at": now_iso,
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

    worker = threading.Thread(
        target=_run_forex_analysis,
        args=(run_id, req),
        daemon=True,
    )
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
    runs = sorted(_forex_runs.values(), key=lambda r: r["started_at"], reverse=True)
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_forex_run(run_id: str):
    """Get metadata and report for a specific Forex analysis run."""
    if run_id not in _forex_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    report = _forex_completed_reports.get(run_id)
    return {"run": _forex_runs[run_id], "report": report}


@router.get("/runs/{run_id}/events")
async def stream_forex_events(run_id: str, request: Request):
    """Server-Sent Events (SSE) streaming endpoint for live agent node progress."""
    if run_id not in _forex_runs:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        last_idx = 0
        while True:
            if await request.is_disconnected():
                break

            events = _forex_run_events.get(run_id, [])
            while last_idx < len(events):
                evt = events[last_idx]
                yield f"event: {evt['type']}\ndata: {json.dumps(evt['data'])}\n\n"
                last_idx += 1

                if evt["type"] in ("complete", "error"):
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
    if run_id not in _forex_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    report = _forex_completed_reports.get(run_id)
    if report is None:
        return {"run_id": run_id, "status": _forex_runs[run_id].get("status", "running"), "report": None}
    return report


@router.get("/analyze/{run_id}/status")
async def get_forex_status_alias(run_id: str):
    """Compatibility alias for run status inspection."""
    if run_id not in _forex_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return _forex_runs[run_id]


# ---------------------------------------------------------------------------
# 5. Demonstration Backtesting Endpoints
# ---------------------------------------------------------------------------

@router.post("/backtest/run")
async def run_backtest(
    req: ForexBacktestRequest,
    request: Request,
):
    """Run an explicitly requested, isolated demonstration backtest."""
    verify_forex_auth(request)
    if not req.demo_mode:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FOREX_BACKTEST_DEMO_ONLY",
                "message": "Production Forex backtesting is unavailable. Set demo_mode=true to run the demonstration strategy.",
            },
        )
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
        "validated_strategy_performance": False,
        "notice": demo_notice,
    }
    try:
        norm_pair = normalize_forex_pair(req.pair)
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

        _backtest_runs[backtest_id] = {
            **demo_metadata,
            "backtest_id": backtest_id,
            "pair": norm_pair,
            "timeframe": req.timeframe,
            "result": result_dict,
            "markdown_report": markdown_rep,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            **demo_metadata,
            "backtest_id": backtest_id,
            "pair": norm_pair,
            "status": "completed",
            "result": result_dict,
            "markdown_report": markdown_rep,
        }
    except Exception as exc:
        logger.error("Forex backtest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.get("/backtest/runs")
async def list_backtest_runs():
    """List completed backtests."""
    runs = [
        {
            "backtest_id": k,
            "demo_mode": v["demo_mode"],
            "data_source": v["data_source"],
            "strategy": v["strategy"],
            "validated_strategy_performance": v["validated_strategy_performance"],
            "notice": v["notice"],
            "pair": v["pair"],
            "timeframe": v["timeframe"],
            "created_at": v["created_at"],
            "total_trades": v["result"]["total_trades"],
            "win_rate_pct": v["result"]["win_rate_pct"],
            "profit_factor": v["result"]["profit_factor"],
            "net_profit": v["result"]["total_net_profit"],
        }
        for k, v in _backtest_runs.items()
    ]
    return {"backtests": runs, "count": len(runs)}


@router.get("/backtest/{backtest_id}")
async def get_backtest(backtest_id: str):
    """Retrieve full result scorecard, equity curve, and markdown report for a backtest."""
    if backtest_id not in _backtest_runs:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return _backtest_runs[backtest_id]


# ---------------------------------------------------------------------------
# 6. Quantitative Analytics & Diagnostics Endpoints
# ---------------------------------------------------------------------------

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
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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
    tag: str | None = None,
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Query stored heuristic lessons from past trade reflections."""
    lessons = learning_mgr.store.list_lessons(pair=pair, setup_type=setup_type, tag=tag)
    return {"lessons": [_safe_model_dump(les) for les in lessons], "count": len(lessons)}



@router.post("/learning/reflect/{trade_id}")
async def reflect_on_trade(
    trade_id: str,
    request: Request,
    learning_mgr: ForexLearningManager = Depends(get_learning_manager),
):
    """Trigger automated post-trade reflection and lesson extraction for a settled position."""
    verify_forex_auth(request)
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
    verify_forex_auth(request)
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


