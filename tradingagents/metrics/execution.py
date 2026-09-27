"""Execution Quality & Slippage Analytics Engine (Phase 16).

Calculates realized execution slippage, spread drag, broker fill delays,
and composite execution quality benchmarks across trading sessions and currency pairs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.models import OrderExecutionRecord
from tradingagents.forex.pips import pip_size_for, pip_value_in_account_currency
from tradingagents.forex.sessions import session_for_pair
from tradingagents.metrics.models import ExecutionQuality, SlippageType

logger = logging.getLogger(__name__)


def calculate_execution_slippage(
    requested_price: float,
    fill_price: float,
    action: ForexAction | str,
    pair: str,
) -> tuple[float, SlippageType]:
    """Calculate directional slippage in pips and categorize fill type.

    Parameters
    ----------
    requested_price:
        Intended price when order was dispatched.
    fill_price:
        Actual transaction price executed by broker.
    action:
        Order direction (LONG/BUY or SHORT/SELL).
    pair:
        Forex pair symbol.

    Returns
    -------
    tuple[float, SlippageType]
        (signed_slippage_pips, SlippageType)
        Note: Positive pips indicate adverse slippage (cost to trader);
              Negative pips indicate price improvement (benefit to trader).
    """
    pip_sz = pip_size_for(pair)

    if isinstance(action, str):
        act_str = action.strip().upper()
        is_buy = "BUY" in act_str or "LONG" in act_str
    else:
        is_buy = action == ForexAction.LONG

    diff = fill_price - requested_price if is_buy else requested_price - fill_price

    slippage_pips = round(diff / pip_sz, 2)

    if abs(slippage_pips) < 0.05:
        slip_type = SlippageType.EXACT
    elif slippage_pips > 0:
        slip_type = SlippageType.ADVERSE
    else:
        slip_type = SlippageType.IMPROVEMENT

    return slippage_pips, slip_type


def calculate_slippage_cost(
    slippage_pips: float,
    volume: float,
    pair: str,
    account_currency: str = "USD",
    current_quote_price: float | None = None,
) -> float:
    """Calculate monetary friction of slippage in account currency.

    Positive slippage pips produce positive cost (loss);
    Negative slippage pips produce negative cost (gain/savings).
    """
    pip_val = pip_value_in_account_currency(
        pair=pair,
        lot_size=volume,
        account_currency=account_currency,
        current_quote_price=current_quote_price,
    )
    return round(slippage_pips * pip_val, 2)


def calculate_spread_cost(
    spread_pips: float,
    volume: float,
    pair: str,
    account_currency: str = "USD",
    current_quote_price: float | None = None,
) -> float:
    """Calculate monetary spread drag cost in account currency."""
    pip_val = pip_value_in_account_currency(
        pair=pair,
        lot_size=volume,
        account_currency=account_currency,
        current_quote_price=current_quote_price,
    )
    return round(abs(spread_pips) * pip_val, 2)


def calculate_execution_quality_score(
    slippage_pips: float,
    spread_pips: float,
    execution_delay_ms: float | None = None,
) -> float:
    """Compute institutional Execution Quality Score (0.0 to 100.0).

    100.0 represents zero slippage (or price improvement), tight spread,
    and sub-150ms execution fill latency.
    """
    score = 100.0

    # Slippage penalty: 0 if favorable or exact; -15 pts per pip of adverse slippage
    if slippage_pips > 0.0:
        slip_penalty = min(50.0, slippage_pips * 15.0)
        score -= slip_penalty

    # Spread penalty: benchmark is ~1.0 pip for standard liquid majors
    if spread_pips > 1.2:
        excess_spread = spread_pips - 1.2
        spread_penalty = min(30.0, excess_spread * 10.0)
        score -= spread_penalty

    # Delay penalty: < 150ms ideal; penalized up to 20 pts for multi-second delays
    if execution_delay_ms is not None and execution_delay_ms > 150.0:
        excess_delay = execution_delay_ms - 150.0
        delay_penalty = min(20.0, (excess_delay / 500.0) * 10.0)
        score -= delay_penalty

    return max(0.0, min(100.0, round(score, 1)))


class ExecutionQualityAnalyzer:
    """Analyzes broker deals and order executions for slippage, spread, and quality."""

    def __init__(self, account_currency: str = "USD") -> None:
        self.account_currency = account_currency.upper()

    def analyze_execution(
        self,
        trade_id: str,
        pair: str,
        action: ForexAction | str,
        requested_price: float,
        fill_price: float,
        volume: float = 0.1,
        spread_at_open_pips: float = 0.0,
        deal_id: str | None = None,
        proposal_id: str | None = None,
        order_type: str = "MARKET",
        execution_delay_ms: float | None = None,
        current_quote_price: float | None = None,
        session: str | None = None,
        broker: str = "MetaTrader5",
        timestamp_utc: str | None = None,
    ) -> ExecutionQuality:
        """Analyze a single broker deal and construct an ExecutionQuality record."""
        # Normalize action
        if isinstance(action, str):
            act = ForexAction.SHORT if "SELL" in action.upper() or "SHORT" in action.upper() else ForexAction.LONG
        else:
            act = action

        # Slippage calculations
        slippage_pips, slip_type = calculate_execution_slippage(
            requested_price=requested_price,
            fill_price=fill_price,
            action=act,
            pair=pair,
        )

        slip_cost = calculate_slippage_cost(
            slippage_pips=slippage_pips,
            volume=volume,
            pair=pair,
            account_currency=self.account_currency,
            current_quote_price=current_quote_price,
        )

        spread_cost = calculate_spread_cost(
            spread_pips=spread_at_open_pips,
            volume=volume,
            pair=pair,
            account_currency=self.account_currency,
            current_quote_price=current_quote_price,
        )

        total_friction = round(slip_cost + spread_cost, 2)

        quality_score = calculate_execution_quality_score(
            slippage_pips=slippage_pips,
            spread_pips=spread_at_open_pips,
            execution_delay_ms=execution_delay_ms,
        )

        # Infer session if not provided
        eff_session = session
        if eff_session is None:
            now_dt = datetime.now(timezone.utc)
            active_s = session_for_pair(pair, now_dt, active_only=True)
            if active_s:
                eff_session = active_s[0].name
            else:
                rel_s = session_for_pair(pair, now_dt)
                eff_session = rel_s[0].name if rel_s else "OFF_HOURS"

        now_str = timestamp_utc or datetime.now(timezone.utc).isoformat()
        did = deal_id or f"deal_{uuid.uuid4().hex[:12]}"

        return ExecutionQuality(
            deal_id=did,
            trade_id=trade_id,
            proposal_id=proposal_id,
            pair=pair.strip().upper(),
            action=act,
            order_type=order_type,
            volume=volume,
            requested_price=requested_price,
            fill_price=fill_price,
            slippage_pips=slippage_pips,
            slippage_type=slip_type,
            slippage_cost_usd=slip_cost,
            spread_at_open_pips=spread_at_open_pips,
            spread_cost_usd=spread_cost,
            total_execution_friction_usd=total_friction,
            execution_delay_ms=execution_delay_ms,
            quality_score=quality_score,
            session=eff_session,
            broker=broker,
            timestamp_utc=now_str,
        )

    def benchmark_broker_execution(
        self,
        executions: Sequence[ExecutionQuality | OrderExecutionRecord | dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate execution records into institutional broker performance benchmarks."""
        if not executions:
            return {
                "total_executions": 0,
                "avg_quality_score": 100.0,
                "avg_slippage_pips": 0.0,
                "adverse_fill_pct": 0.0,
                "price_improvement_pct": 0.0,
                "exact_fill_pct": 0.0,
                "total_slippage_cost_usd": 0.0,
                "total_spread_cost_usd": 0.0,
                "total_friction_usd": 0.0,
                "by_pair": {},
                "by_session": {},
            }

        analyzed: list[ExecutionQuality] = []
        for item in executions:
            if isinstance(item, ExecutionQuality):
                analyzed.append(item)
            elif isinstance(item, OrderExecutionRecord):
                eq = self.analyze_execution(
                    trade_id=item.trade_id,
                    pair=item.pair,
                    action=ForexAction.LONG,
                    requested_price=item.price,  # baseline
                    fill_price=item.price,
                    volume=item.volume,
                    spread_at_open_pips=item.spread_at_open_pips,
                    deal_id=item.deal_id,
                    proposal_id=item.proposal_id,
                    order_type=item.order_type,
                    timestamp_utc=item.timestamp_utc,
                )
                eq.slippage_pips = item.slippage_pips
                eq.slippage_cost_usd = calculate_slippage_cost(
                    item.slippage_pips, item.volume, item.pair, self.account_currency
                )
                eq.quality_score = calculate_execution_quality_score(
                    item.slippage_pips, item.spread_at_open_pips
                )
                analyzed.append(eq)
            elif isinstance(item, dict):
                eq = self.analyze_execution(
                    trade_id=str(item.get("trade_id", "trd_unspecified")),
                    pair=str(item.get("pair", "EURUSD")),
                    action=item.get("action", ForexAction.LONG),
                    requested_price=float(item.get("requested_price", item.get("price", 0.0))),
                    fill_price=float(item.get("fill_price", item.get("price", 0.0))),
                    volume=float(item.get("volume", 0.1)),
                    spread_at_open_pips=float(item.get("spread_at_open_pips", 0.0)),
                    execution_delay_ms=float(item["execution_delay_ms"]) if item.get("execution_delay_ms") is not None else None,
                    session=item.get("session"),
                )
                analyzed.append(eq)

        total_count = len(analyzed)
        scores = [e.quality_score for e in analyzed]
        slippages = [e.slippage_pips for e in analyzed]
        slip_costs = [e.slippage_cost_usd for e in analyzed]
        spread_costs = [e.spread_cost_usd for e in analyzed]
        frictions = [e.total_execution_friction_usd for e in analyzed]

        adverse_count = sum(1 for e in analyzed if e.slippage_type == SlippageType.ADVERSE)
        improvement_count = sum(1 for e in analyzed if e.slippage_type == SlippageType.IMPROVEMENT)
        exact_count = sum(1 for e in analyzed if e.slippage_type == SlippageType.EXACT)

        # By pair aggregation
        by_pair: dict[str, dict[str, Any]] = {}
        for e in analyzed:
            p = e.pair
            if p not in by_pair:
                by_pair[p] = {"count": 0, "slippages": [], "scores": [], "friction": 0.0}
            by_pair[p]["count"] += 1
            by_pair[p]["slippages"].append(e.slippage_pips)
            by_pair[p]["scores"].append(e.quality_score)
            by_pair[p]["friction"] += e.total_execution_friction_usd

        pair_summary = {
            p: {
                "count": data["count"],
                "avg_slippage_pips": round(sum(data["slippages"]) / len(data["slippages"]), 2),
                "avg_score": round(sum(data["scores"]) / len(data["scores"]), 1),
                "total_friction_usd": round(data["friction"], 2),
            }
            for p, data in by_pair.items()
        }

        # By session aggregation
        by_session: dict[str, dict[str, Any]] = {}
        for e in analyzed:
            s = e.session or "UNKNOWN"
            if s not in by_session:
                by_session[s] = {"count": 0, "slippages": [], "scores": []}
            by_session[s]["count"] += 1
            by_session[s]["slippages"].append(e.slippage_pips)
            by_session[s]["scores"].append(e.quality_score)

        session_summary = {
            s: {
                "count": data["count"],
                "avg_slippage_pips": round(sum(data["slippages"]) / len(data["slippages"]), 2),
                "avg_score": round(sum(data["scores"]) / len(data["scores"]), 1),
            }
            for s, data in by_session.items()
        }

        return {
            "total_executions": total_count,
            "avg_quality_score": round(sum(scores) / total_count, 1),
            "avg_slippage_pips": round(sum(slippages) / total_count, 2),
            "max_adverse_slippage_pips": round(max(slippages), 2) if slippages else 0.0,
            "best_price_improvement_pips": round(min(slippages), 2) if slippages else 0.0,
            "adverse_fill_pct": round((adverse_count / total_count) * 100.0, 1),
            "price_improvement_pct": round((improvement_count / total_count) * 100.0, 1),
            "exact_fill_pct": round((exact_count / total_count) * 100.0, 1),
            "total_slippage_cost_usd": round(sum(slip_costs), 2),
            "total_spread_cost_usd": round(sum(spread_costs), 2),
            "total_friction_usd": round(sum(frictions), 2),
            "by_pair": pair_summary,
            "by_session": session_summary,
        }
