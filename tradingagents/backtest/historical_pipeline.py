"""Reusable point-in-time Forex graph pipeline for historical evaluations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from inspect import signature
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
)
from tradingagents.backtest.historical_data import candle_frame
from tradingagents.dataflows.forex_context import historical_market_scope
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.dataflows.trading_economics import TradingEconomicsCalendar
from tradingagents.forex import ForexTradingAgentsGraph
from tradingagents.forex.conversion import AvailabilityStatus, ForexConversionRate
from tradingagents.forex.domain import normalize_forex_pair
from tradingagents.forex.pips import pip_size_for
from tradingagents.risk.context import (
    ForexMarketContext,
    ForexPortfolioContext,
    ForexRiskContext,
)
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    OpenPosition,
    PendingExposure,
)


@dataclass(frozen=True)
class HistoricalForexPipelineConfig:
    pair: str
    timeframe: str
    analyst_selection: list[str] = field(
        default_factory=lambda: ["forex_technical", "forex_macro", "forex_news"]
    )
    provider: str = "openai"
    quick_model: str = "gpt-4.1-mini"
    deep_model: str = "gpt-4.1"
    research_depth: str = "standard"
    token_limits: int | None = None
    debate_enabled: bool = True
    memory_enabled: bool = False
    broker_symbol: str | None = None
    broker_digits: int | None = None
    broker_point: float | None = None
    broker_pip_size: float | None = None
    broker_contract_size: float = 100000.0
    broker_volume_min: float = 0.01
    broker_volume_max: float = 100.0
    broker_volume_step: float = 0.01
    broker_assumption_source: str = "CONFIGURED_ASSUMPTION"


def _pit_atr_pips(
    candles: list[ForexBar], pair: str, period: int = 14
) -> float | None:
    """Return Wilder ATR using only bars already available at the cutoff."""
    if len(candles) < period:
        return None
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        ranges = [candle.high - candle.low]
        if previous_close is not None:
            ranges.extend(
                (abs(candle.high - previous_close), abs(candle.low - previous_close))
            )
        true_ranges.append(max(ranges))
        previous_close = candle.close
    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    value = atr / pip_size_for(pair)
    return value if value > 0 else None


def create_historical_forex_pipeline(
    config: HistoricalForexPipelineConfig,
    provenance: dict[str, Any],
    *,
    graph_factory: Callable[..., Any] | None = None,
    memory_source: Any = None,
) -> Callable[[str, datetime, list[ForexBar]], ForexTraderProposal | None]:
    """Create the real graph callback shared by historical validation workflows.

    The callback establishes a historical market scope for every analysis point.
    It never executes against a broker and rejects pair-changing graph output.
    """
    expected_pair = normalize_forex_pair(config.pair)
    account_snapshot: ForexAccountProfile | None = None
    open_positions: tuple[OpenPosition, ...] = ()
    pending_exposures: tuple[PendingExposure, ...] = ()
    conversion_snapshot: tuple[ForexConversionRate, ...] = ()
    snapshot_as_of: datetime | None = None
    audit_metadata: dict[str, Any] = {"applied_lesson_ids": []}

    def set_account_snapshot(snapshot: ForexAccountProfile) -> None:
        nonlocal account_snapshot
        account_snapshot = snapshot.model_copy(deep=True)

    def set_deterministic_snapshot(
        account: ForexAccountProfile,
        positions: tuple[OpenPosition, ...],
        pending: tuple[PendingExposure, ...],
        conversions: tuple[ForexConversionRate, ...],
        as_of_utc: datetime,
    ) -> None:
        nonlocal account_snapshot, open_positions, pending_exposures, conversion_snapshot, snapshot_as_of
        account_snapshot = account.model_copy(deep=True)
        open_positions = tuple(position.model_copy(deep=True) for position in positions)
        pending_exposures = tuple(item.model_copy(deep=True) for item in pending)
        conversion_snapshot = tuple(conversions)
        snapshot_as_of = as_of_utc

    def pipeline(pair: str, cutoff: datetime, pit_candles: list[ForexBar]):
        canonical_pair = normalize_forex_pair(pair)
        if canonical_pair != expected_pair:
            raise ValueError("historical pipeline pair does not match configured pair")
        if not pit_candles:
            return None
        if account_snapshot is None or snapshot_as_of != cutoff:
            raise ValueError("historical deterministic account/portfolio snapshot is unavailable")

        last_bar = pit_candles[-1]
        observed_at = last_bar.close_time or last_bar.timestamp
        spread_pips = max(0.0, last_bar.spread_pips)
        atr_pips = _pit_atr_pips(pit_candles, canonical_pair)
        digits = config.broker_digits
        if digits is None:
            digits = 3 if pip_size_for(canonical_pair) == 0.01 else 5
        point = config.broker_point
        if point is None:
            point = 10 ** (-digits)
        risk_context = ForexRiskContext(
            pair=canonical_pair,
            as_of_utc=cutoff,
            market=ForexMarketContext(
                quote_status=AvailabilityStatus.AVAILABLE,
                bid=last_bar.close,
                ask=last_bar.close + spread_pips * pip_size_for(canonical_pair),
                spread_pips=spread_pips,
                atr_status=(
                    AvailabilityStatus.AVAILABLE
                    if atr_pips is not None
                    else AvailabilityStatus.UNAVAILABLE
                ),
                atr_pips=atr_pips,
                source=f"historical:{last_bar.data_source}",
                observed_at=observed_at,
            ),
            account=account_snapshot,
            broker=BrokerExecutionConstraints(
                broker_symbol=(
                    last_bar.broker_symbol
                    or config.broker_symbol
                    or canonical_pair
                ),
                digits=digits,
                point=point,
                pip_size=config.broker_pip_size or pip_size_for(canonical_pair),
                contract_size=config.broker_contract_size,
                min_volume=config.broker_volume_min,
                max_volume=config.broker_volume_max,
                volume_step=config.broker_volume_step,
            ),
            portfolio=ForexPortfolioContext(
                open_positions=open_positions,
                pending_orders_status=AvailabilityStatus.AVAILABLE,
                pending_order_count=len(pending_exposures),
                pending_exposures=pending_exposures,
            ),
            conversions=conversion_snapshot,
        )

        with historical_market_scope(
            canonical_pair, candle_frame(pit_candles, provenance), cutoff
        ):
            TradingEconomicsCalendar().query(
                canonical_pair,
                (cutoff - timedelta(days=7)).date().isoformat(),
                (cutoff + timedelta(days=7)).date().isoformat(),
                as_of=cutoff,
            )
            if graph_factory is not None:
                parameter_count = len(signature(graph_factory).parameters)
                graph = (
                    graph_factory(config, account_snapshot, memory_source, risk_context)
                    if parameter_count >= 4
                    else (
                        graph_factory(config, account_snapshot, memory_source)
                        if parameter_count >= 3
                        else graph_factory(config)
                    )
                )
            else:
                graph = ForexTradingAgentsGraph(
                    config={
                        "llm_provider": config.provider,
                        "quick_think_llm": config.quick_model,
                        "deep_think_llm": config.deep_model,
                        "max_tokens": config.token_limits,
                        "max_debate_rounds": 3 if config.research_depth == "deep" else 1,
                        "historical_backtest": True,
                        "historical_debate_enabled": config.debate_enabled,
                        "historical_memory_enabled": config.memory_enabled,
                    },
                    selected_analysts=config.analyst_selection,
                    sizing_account=account_snapshot,
                    risk_context=risk_context,
                    learning_manager=memory_source,
                    auto_record_trades=False,
                )
            final_state, _signal = graph.run(
                pair=canonical_pair,
                trade_date=cutoff.isoformat(),
                execution_timeframe=config.timeframe,
            )

        audit_metadata["applied_lesson_ids"] = list(final_state.get("applied_lesson_ids") or [])
        audit_metadata["decision_cutoff"] = cutoff.isoformat()

        raw_proposal = final_state.get("forex_proposal")
        if raw_proposal is None:
            raise ValueError("historical graph did not produce a typed decision")
        proposal = (
            ForexTraderProposal.model_validate(raw_proposal)
            if isinstance(raw_proposal, dict)
            else raw_proposal
        )
        decision = ForexRiskDecision.model_validate(final_state.get("forex_risk_decision"))
        if decision.pair != canonical_pair or proposal.pair != canonical_pair:
            raise ValueError("historical graph returned a different pair")
        if (
            decision.approved_action != ForexAction.NO_TRADE
            and decision.decision != ForexRiskDecisionAction.REJECT
            and (not decision.approved_lot_size or decision.approved_lot_size <= 0)
        ):
            raise ValueError("historical graph did not authorize a positive lot size")

        updates: dict[str, Any] = {
            "action": decision.approved_action,
            "suggested_lot_size": decision.approved_lot_size,
            "risk_action": decision.decision,
        }
        for key, value in (
            ("entry_price", decision.entry_price),
            ("stop_loss", decision.stop_loss),
            ("take_profit_1", decision.take_profit),
        ):
            if value is not None:
                updates[key] = value
        return proposal.model_copy(update=updates)

    pipeline.historical_pipeline_config = config  # type: ignore[attr-defined]
    pipeline.set_account_snapshot = set_account_snapshot  # type: ignore[attr-defined]
    pipeline.set_deterministic_snapshot = set_deterministic_snapshot  # type: ignore[attr-defined]
    pipeline.audit_metadata = audit_metadata  # type: ignore[attr-defined]
    return pipeline
