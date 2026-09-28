"""Skipped Proposal Evaluation & Theoretical Outcome Simulation (Phase 17).

Provides counterfactual market simulation for skipped and expired trade proposals:
1. Evaluates whether entry was triggered within the validity window (or EXPIRED_NOT_TRIGGERED).
2. Simulates price movement through subsequent candles.
3. Detects SL, TP1, and TP2 touches.
4. Resolves same-candle SL and TP touches using lower-resolution data (e.g. M1);
   if still unresolved, flags as AMBIGUOUS without conveniently assuming profitability.
5. Computes MFE, MAE, and theoretical R-multiples.
6. Maintains separate metrics for AI theoretical performance vs actual user execution performance.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from tradingagents.agents.schemas_forex import ForexAction, OrderType
from tradingagents.database.models import (
    OrderExecutionRecord,
    ProposalRecord,
    TradeJournalRecord,
)
from tradingagents.forex.pips import pip_size_for
from tradingagents.learning.history_provider import TradeHistoryProvider
from tradingagents.metrics.mfe_mae import parse_utc_timestamp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Domain Models
# ---------------------------------------------------------------------------


class ProposalSimulationStatus(str, Enum):
    """Evaluation status for simulated counterfactual proposal outcomes."""

    EXPIRED_NOT_TRIGGERED = "EXPIRED_NOT_TRIGGERED"
    HIT_SL = "HIT_SL"
    HIT_TP1 = "HIT_TP1"
    HIT_TP2 = "HIT_TP2"
    AMBIGUOUS = "AMBIGUOUS"
    OPEN_ACTIVE = "OPEN_ACTIVE"


class SkippedProposalSimulation(BaseModel):
    """Complete simulation result for an evaluated skipped or expired proposal."""

    proposal_id: str
    pair: str
    action: ForexAction
    status: ProposalSimulationStatus
    entry_triggered: bool = False
    entry_price: float = 0.0
    entry_time_utc: str | None = None
    exit_price: float | None = None
    exit_time_utc: str | None = None
    exit_reason: str | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None

    # Excursions
    mfe_pips: float = Field(default=0.0, ge=0.0)
    mae_pips: float = Field(default=0.0, ge=0.0)
    mfe_r: float = Field(default=0.0, ge=0.0)
    mae_r: float = Field(default=0.0, ge=0.0)
    theoretical_r: float | None = None

    # Milestone checks
    hit_sl: bool = False
    hit_tp1: bool = False
    hit_tp2: bool = False

    # Quality & Provenance
    candles_evaluated: int = 0
    sub_resolution_checked: bool = False
    is_ambiguous: bool = False
    ambiguity_resolved: bool = False
    resolution_used: str = "PRIMARY"
    evaluation_notes: str = ""
    evaluated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AITheoreticalMetrics(BaseModel):
    """Aggregate theoretical performance metrics across all generated AI proposals."""

    total_proposals: int = 0
    evaluated_count: int = 0
    triggered_count: int = 0
    untriggered_count: int = 0
    hit_sl_count: int = 0
    hit_tp1_count: int = 0
    hit_tp2_count: int = 0
    ambiguous_count: int = 0
    open_active_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate_pct: float = 0.0
    total_theoretical_r: float = 0.0
    avg_theoretical_r: float = 0.0
    expectancy_r: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0


class UserExecutionMetrics(BaseModel):
    """Aggregate actual performance metrics across human-executed trades."""

    total_executed_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    scratch_count: int = 0
    win_rate_pct: float = 0.0
    total_realized_r: float = 0.0
    avg_realized_r: float = 0.0
    expectancy_r: float = 0.0
    avg_slippage_pips: float = 0.0
    total_friction_usd: float = 0.0


class ComparativePerformanceSummary(BaseModel):
    """Comparative analytics benchmarking AI theoretical performance against actual human execution."""

    ai_theoretical: AITheoreticalMetrics
    user_execution: UserExecutionMetrics
    skipped_proposals_count: int = 0
    skipped_avoided_losses: int = 0
    skipped_missed_winners: int = 0
    skipped_untriggered: int = 0
    skipped_ambiguous: int = 0
    user_skip_alpha_r: float = 0.0
    skip_efficiency_pct: float = 0.0
    verdict: str = ""
    generated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Helper Candle Utilities
# ---------------------------------------------------------------------------


def _extract_candle_tuple(
    candle: Any,
) -> tuple[float, float, float, float, datetime | None, str | None]:
    """Extract (open, high, low, close, dt, iso_str) from arbitrary candle object."""
    if hasattr(candle, "open") and hasattr(candle, "high") and hasattr(candle, "low") and hasattr(candle, "close"):
        o = float(candle.open)
        h = float(candle.high)
        low_val = float(candle.low)
        c = float(candle.close)
        raw_ts = getattr(candle, "timestamp", None)
        dt = parse_utc_timestamp(raw_ts)
        iso = dt.isoformat() if dt else (str(raw_ts) if raw_ts else None)
        return o, h, low_val, c, dt, iso

    if isinstance(candle, dict):
        o = float(candle.get("open") or candle.get("Open") or 0.0)
        h = float(candle.get("high") or candle.get("High") or 0.0)
        low_val = float(candle.get("low") or candle.get("Low") or 0.0)
        c = float(candle.get("close") or candle.get("Close") or 0.0)
        raw_ts = candle.get("timestamp") or candle.get("Date") or candle.get("time")
        dt = parse_utc_timestamp(raw_ts)
        iso = dt.isoformat() if dt else (str(raw_ts) if raw_ts else None)
        return o, h, low_val, c, dt, iso

    if isinstance(candle, (tuple, list)) and len(candle) >= 4:
        try:
            raw_ts = candle[0] if len(candle) >= 5 else None
            dt = parse_utc_timestamp(raw_ts) if raw_ts is not None else None
            iso = dt.isoformat() if dt else None
            o = float(candle[1] if len(candle) >= 5 else candle[0])
            h = float(candle[2] if len(candle) >= 5 else candle[1])
            low_val = float(candle[3] if len(candle) >= 5 else candle[2])
            c = float(candle[4] if len(candle) >= 5 else candle[3])
            return o, h, low_val, c, dt, iso
        except Exception:
            pass

    return 0.0, 0.0, 0.0, 0.0, None, None


# ---------------------------------------------------------------------------
# Evaluator Engine
# ---------------------------------------------------------------------------


class SkippedProposalEvaluator:
    """Evaluates counterfactual market outcomes for skipped and expired trade proposals."""

    def __init__(self, history_provider: TradeHistoryProvider | None = None) -> None:
        self.history_provider = history_provider

    def evaluate(
        self,
        proposal: ProposalRecord | dict[str, Any],
        candles: Sequence[Any] | pd.DataFrame,
        sub_resolution_candles: Sequence[Any] | pd.DataFrame | None = None,
        history_provider: TradeHistoryProvider | None = None,
    ) -> SkippedProposalSimulation:
        """Simulate what would have happened if a proposal had been executed.

        Parameters
        ----------
        proposal:
            ProposalRecord instance or dict containing proposal fields.
        candles:
            Sequence of candles (or DataFrame) covering the evaluation period.
        sub_resolution_candles:
            Optional finer-resolution bars (e.g. M1) used to resolve collisions.
        history_provider:
            Optional TradeHistoryProvider for fetching intraday sub-bars.
        """
        # 1. Unpack proposal fields
        if isinstance(proposal, ProposalRecord):
            pid = proposal.proposal_id
            pair = proposal.pair
            action_raw = proposal.action
            order_type_raw = proposal.order_type
            entry_price = proposal.entry_price
            entry_zone_low = proposal.entry_zone_low
            entry_zone_high = proposal.entry_zone_high
            stop_loss = proposal.stop_loss
            take_profit_1 = proposal.take_profit_1
            take_profit_2 = proposal.take_profit_2
            valid_until_str = proposal.valid_until
        else:
            pid = str(proposal.get("proposal_id", "prop_sim"))
            pair = str(proposal.get("pair", "EURUSD"))
            action_raw = proposal.get("action", ForexAction.LONG)
            order_type_raw = proposal.get("order_type", OrderType.MARKET)
            entry_price = proposal.get("entry_price")
            entry_zone_low = proposal.get("entry_zone_low")
            entry_zone_high = proposal.get("entry_zone_high")
            stop_loss = proposal.get("stop_loss")
            take_profit_1 = proposal.get("take_profit_1") or proposal.get("take_profit")
            take_profit_2 = proposal.get("take_profit_2")
            valid_until_str = proposal.get("valid_until_utc") or proposal.get("valid_until")

        action = ForexAction.from_str(action_raw)
        if isinstance(order_type_raw, str):
            try:
                order_type = OrderType(order_type_raw.upper())
            except ValueError:
                order_type = OrderType.MARKET
        else:
            order_type = order_type_raw or OrderType.MARKET

        pip_size = pip_size_for(pair)
        valid_until_dt = parse_utc_timestamp(valid_until_str)

        # Normalize candle sequence
        candle_list: list[tuple[float, float, float, float, datetime | None, str | None]] = []
        if isinstance(candles, pd.DataFrame):
            for _, row in candles.iterrows():
                candle_list.append(_extract_candle_tuple(row.to_dict()))
        else:
            for c in candles:
                candle_list.append(_extract_candle_tuple(c))

        if not candle_list:
            return SkippedProposalSimulation(
                proposal_id=pid,
                pair=pair,
                action=action,
                status=ProposalSimulationStatus.EXPIRED_NOT_TRIGGERED,
                entry_triggered=False,
                entry_price=entry_price or 0.0,
                evaluation_notes="No candle data provided for simulation.",
            )

        # 2. Check Entry Triggering
        entry_triggered = False
        entry_idx = 0
        effective_entry = entry_price or candle_list[0][0]
        entry_time_utc: str | None = None

        if order_type == OrderType.MARKET:
            entry_triggered = True
            entry_idx = 0
            effective_entry = entry_price if entry_price is not None else candle_list[0][0]
            entry_time_utc = candle_list[0][5]
        else:
            target_p = entry_price
            for i, (c_open, c_high, c_low, _c_close, c_dt, c_iso) in enumerate(candle_list):
                if valid_until_dt and c_dt and c_dt > valid_until_dt:
                    # Past validity window without triggering
                    break

                matched = False
                if target_p is not None:
                    order_str = str(order_type).upper()
                    if "LIMIT" in order_str:
                        if (action == ForexAction.LONG and c_low <= target_p) or (action == ForexAction.SHORT and c_high >= target_p):
                            matched = True
                    elif "STOP" in order_str:
                        if (action == ForexAction.LONG and c_high >= target_p) or (action == ForexAction.SHORT and c_low <= target_p):
                            matched = True
                    else:
                        if c_low <= target_p <= c_high or (action == ForexAction.LONG and c_low <= target_p) or (action == ForexAction.SHORT and c_high >= target_p):
                            matched = True
                elif entry_zone_low is not None and entry_zone_high is not None:
                    if c_high >= entry_zone_low and c_low <= entry_zone_high:
                        matched = True

                if matched:
                    entry_triggered = True
                    entry_idx = i
                    effective_entry = target_p if target_p is not None else c_open
                    entry_time_utc = c_iso
                    break

        if not entry_triggered:
            return SkippedProposalSimulation(
                proposal_id=pid,
                pair=pair,
                action=action,
                status=ProposalSimulationStatus.EXPIRED_NOT_TRIGGERED,
                entry_triggered=False,
                entry_price=entry_price or 0.0,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
                candles_evaluated=len(candle_list),
                theoretical_r=0.0,
                evaluation_notes="Entry was never reached during simulation/validity window.",
            )

        # 3. Simulate Price Movement After Entry
        if stop_loss is None:
            # Fallback stop distance if none planned (prevent div by zero)
            risk_pips = 20.0
            stop_loss = (
                effective_entry - (risk_pips * pip_size)
                if action == ForexAction.LONG
                else effective_entry + (risk_pips * pip_size)
            )
        else:
            risk_pips = max(0.1, abs(effective_entry - stop_loss) / pip_size)

        peak_mfe_pips = 0.0
        deepest_mae_pips = 0.0
        hit_sl = False
        hit_tp1 = False
        hit_tp2 = False
        sim_status = ProposalSimulationStatus.OPEN_ACTIVE
        exit_price: float | None = None
        exit_time_utc: str | None = None
        exit_reason: str | None = None
        theoretical_r: float | None = None
        is_ambiguous = False
        sub_checked = False
        ambiguity_resolved = False
        active_provider = history_provider or self.history_provider

        for i in range(entry_idx, len(candle_list)):
            _c_open, c_high, c_low, c_close, c_dt, c_iso = candle_list[i]

            # Calculate excursions
            if action == ForexAction.LONG:
                mfe_p = max(0.0, (c_high - effective_entry) / pip_size)
                mae_p = max(0.0, (effective_entry - c_low) / pip_size)
                candle_sl_hit = c_low <= stop_loss
                candle_tp1_hit = take_profit_1 is not None and c_high >= take_profit_1
                candle_tp2_hit = take_profit_2 is not None and c_high >= take_profit_2
            else:
                mfe_p = max(0.0, (effective_entry - c_low) / pip_size)
                mae_p = max(0.0, (c_high - effective_entry) / pip_size)
                candle_sl_hit = c_high >= stop_loss
                candle_tp1_hit = take_profit_1 is not None and c_low <= take_profit_1
                candle_tp2_hit = take_profit_2 is not None and c_low <= take_profit_2

            peak_mfe_pips = max(peak_mfe_pips, mfe_p)
            deepest_mae_pips = max(deepest_mae_pips, mae_p)

            # Check collision: SAME CANDLE HITS BOTH SL AND TP
            if candle_sl_hit and (candle_tp1_hit or candle_tp2_hit):
                # Try lower-resolution data to resolve
                sub_checked = True
                resolved = False
                first_hit: str | None = None

                # 1) Try explicit sub-resolution candles if provided
                sub_bars: Sequence[Any] | None = None
                if sub_resolution_candles is not None:
                    if isinstance(sub_resolution_candles, pd.DataFrame):
                        sub_bars = [row.to_dict() for _, row in sub_resolution_candles.iterrows()]
                    else:
                        sub_bars = sub_resolution_candles

                # 2) Or query history_provider for that candle's time window
                if not sub_bars and active_provider is not None and c_dt is not None:
                    # Query 1-minute bars around this candle
                    history_res = active_provider.get_history(
                        pair=pair,
                        start_time=c_dt,
                        end_time=c_dt,
                        timeframe="M1",
                    )
                    if history_res and history_res.is_available and history_res.candles:
                        sub_bars = history_res.candles

                if sub_bars:
                    for sub_c in sub_bars:
                        _so, sh, sl_p, _sc, _sdt, _siso = _extract_candle_tuple(sub_c)
                        if action == ForexAction.LONG:
                            sub_sl = sl_p <= stop_loss
                            sub_tp = (take_profit_1 is not None and sh >= take_profit_1) or (
                                take_profit_2 is not None and sh >= take_profit_2
                            )
                        else:
                            sub_sl = sh >= stop_loss
                            sub_tp = (take_profit_1 is not None and sl_p <= take_profit_1) or (
                                take_profit_2 is not None and sl_p <= take_profit_2
                            )

                        if sub_sl and not sub_tp:
                            first_hit = "SL"
                            resolved = True
                            break
                        if sub_tp and not sub_sl:
                            first_hit = "TP1"
                            resolved = True
                            break

                if resolved and first_hit == "SL":
                    hit_sl = True
                    sim_status = ProposalSimulationStatus.HIT_SL
                    exit_price = stop_loss
                    exit_time_utc = c_iso
                    exit_reason = "STOP_LOSS"
                    theoretical_r = -1.0
                    ambiguity_resolved = True
                    break

                if resolved and first_hit in ("TP1", "TP2"):
                    hit_tp1 = True
                    sim_status = ProposalSimulationStatus.HIT_TP1
                    exit_price = take_profit_1
                    exit_time_utc = c_iso
                    exit_reason = "TAKE_PROFIT_1"
                    if candle_tp2_hit:
                        hit_tp2 = True
                        sim_status = ProposalSimulationStatus.HIT_TP2
                    tp_target = take_profit_1 if take_profit_1 is not None else effective_entry
                    target_pips = abs(tp_target - effective_entry) / pip_size
                    theoretical_r = round(target_pips / risk_pips, 2)
                    ambiguity_resolved = True
                    break

                # If still unresolved: AMBIGUOUS. Never choose profitable path conveniently!
                is_ambiguous = True
                sim_status = ProposalSimulationStatus.AMBIGUOUS
                exit_price = effective_entry
                exit_time_utc = c_iso
                exit_reason = "AMBIGUOUS"
                theoretical_r = None
                break

            # Scenario: Only SL touched
            if candle_sl_hit:
                hit_sl = True
                sim_status = ProposalSimulationStatus.HIT_SL
                exit_price = stop_loss
                exit_time_utc = c_iso
                exit_reason = "STOP_LOSS"
                theoretical_r = -1.0
                break

            # Scenario: Only TP touched
            if candle_tp1_hit or candle_tp2_hit:
                hit_tp1 = candle_tp1_hit
                hit_tp2 = candle_tp2_hit
                sim_status = (
                    ProposalSimulationStatus.HIT_TP2
                    if candle_tp2_hit
                    else ProposalSimulationStatus.HIT_TP1
                )
                chosen_tp = take_profit_2 if candle_tp2_hit and take_profit_2 else take_profit_1
                exit_price = chosen_tp
                exit_time_utc = c_iso
                exit_reason = "TAKE_PROFIT_2" if candle_tp2_hit else "TAKE_PROFIT_1"
                tp_target = chosen_tp if chosen_tp is not None else effective_entry
                target_pips = abs(tp_target - effective_entry) / pip_size
                theoretical_r = round(target_pips / risk_pips, 2)
                break

        # If still OPEN_ACTIVE after evaluating all candles
        if sim_status == ProposalSimulationStatus.OPEN_ACTIVE:
            last_c = candle_list[-1]
            exit_price = last_c[3]
            exit_time_utc = last_c[5]
            exit_reason = "WINDOW_END"
            if action == ForexAction.LONG:
                net_pips = (last_c[3] - effective_entry) / pip_size
            else:
                net_pips = (effective_entry - last_c[3]) / pip_size
            theoretical_r = round(net_pips / risk_pips, 2)

        mfe_r = round(peak_mfe_pips / risk_pips, 2)
        mae_r = round(deepest_mae_pips / risk_pips, 2)

        return SkippedProposalSimulation(
            proposal_id=pid,
            pair=pair,
            action=action,
            status=sim_status,
            entry_triggered=True,
            entry_price=effective_entry,
            entry_time_utc=entry_time_utc,
            exit_price=exit_price,
            exit_time_utc=exit_time_utc,
            exit_reason=exit_reason,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            mfe_pips=round(peak_mfe_pips, 1),
            mae_pips=round(deepest_mae_pips, 1),
            mfe_r=mfe_r,
            mae_r=mae_r,
            theoretical_r=theoretical_r,
            hit_sl=hit_sl,
            hit_tp1=hit_tp1,
            hit_tp2=hit_tp2,
            candles_evaluated=len(candle_list),
            sub_resolution_checked=sub_checked,
            is_ambiguous=is_ambiguous,
            ambiguity_resolved=ambiguity_resolved,
            resolution_used="SUB_RESOLUTION" if ambiguity_resolved else "PRIMARY",
            evaluation_notes=(
                "Ambiguity resolved using lower-resolution tick/bar sequence."
                if ambiguity_resolved
                else ("Ambiguous SL/TP collision within same candle." if is_ambiguous else "Evaluated cleanly.")
            ),
        )


# ---------------------------------------------------------------------------
# Separate Performance Engine Functions
# ---------------------------------------------------------------------------


def calculate_ai_theoretical_performance(
    proposals: Sequence[ProposalRecord],
    simulations: dict[str, SkippedProposalSimulation] | Sequence[SkippedProposalSimulation],
) -> AITheoreticalMetrics:
    """Compute aggregate theoretical metrics for AI proposals independently of user execution."""
    sim_map: dict[str, SkippedProposalSimulation] = {}
    if isinstance(simulations, dict):
        sim_map = simulations
    else:
        for s in simulations:
            sim_map[s.proposal_id] = s

    total_proposals = len(proposals)
    evaluated_count = len(sim_map)
    triggered_count = 0
    untriggered_count = 0
    hit_sl_count = 0
    hit_tp1_count = 0
    hit_tp2_count = 0
    ambiguous_count = 0
    open_active_count = 0

    settled_wins = 0
    settled_losses = 0
    total_r = 0.0
    mfe_r_sum = 0.0
    mae_r_sum = 0.0

    for sim in sim_map.values():
        if not sim.entry_triggered or sim.status == ProposalSimulationStatus.EXPIRED_NOT_TRIGGERED:
            untriggered_count += 1
            continue

        triggered_count += 1
        mfe_r_sum += sim.mfe_r
        mae_r_sum += sim.mae_r

        if sim.status == ProposalSimulationStatus.AMBIGUOUS or sim.is_ambiguous:
            ambiguous_count += 1
            continue

        if sim.status == ProposalSimulationStatus.HIT_SL:
            hit_sl_count += 1
            settled_losses += 1
            total_r += (sim.theoretical_r if sim.theoretical_r is not None else -1.0)
        elif sim.status in (ProposalSimulationStatus.HIT_TP1, ProposalSimulationStatus.HIT_TP2):
            if sim.status == ProposalSimulationStatus.HIT_TP2:
                hit_tp2_count += 1
            else:
                hit_tp1_count += 1
            settled_wins += 1
            total_r += (sim.theoretical_r if sim.theoretical_r is not None else 1.5)
        elif sim.status == ProposalSimulationStatus.OPEN_ACTIVE:
            open_active_count += 1
            if sim.theoretical_r is not None:
                if sim.theoretical_r > 0:
                    settled_wins += 1
                elif sim.theoretical_r < 0:
                    settled_losses += 1
                total_r += sim.theoretical_r

    settled_total = settled_wins + settled_losses
    win_rate = round((settled_wins / settled_total) * 100.0, 1) if settled_total > 0 else 0.0
    avg_r = round(total_r / settled_total, 2) if settled_total > 0 else 0.0
    avg_mfe = round(mfe_r_sum / triggered_count, 2) if triggered_count > 0 else 0.0
    avg_mae = round(mae_r_sum / triggered_count, 2) if triggered_count > 0 else 0.0

    # Theoretical expectancy
    loss_rate = 1.0 - (win_rate / 100.0) if settled_total > 0 else 0.0
    win_fraction = win_rate / 100.0
    expectancy = round((win_fraction * (total_r / max(1, settled_wins) if settled_wins > 0 else 0.0)) - (loss_rate * 1.0), 2)

    return AITheoreticalMetrics(
        total_proposals=total_proposals,
        evaluated_count=evaluated_count,
        triggered_count=triggered_count,
        untriggered_count=untriggered_count,
        hit_sl_count=hit_sl_count,
        hit_tp1_count=hit_tp1_count,
        hit_tp2_count=hit_tp2_count,
        ambiguous_count=ambiguous_count,
        open_active_count=open_active_count,
        win_count=settled_wins,
        loss_count=settled_losses,
        win_rate_pct=win_rate,
        total_theoretical_r=round(total_r, 2),
        avg_theoretical_r=avg_r,
        expectancy_r=expectancy,
        avg_mfe_r=avg_mfe,
        avg_mae_r=avg_mae,
    )


def calculate_user_execution_performance(
    trades: Sequence[TradeJournalRecord],
    executions: Sequence[OrderExecutionRecord] | None = None,
) -> UserExecutionMetrics:
    """Compute aggregate actual performance metrics across human-executed trades."""
    total_trades = len(trades)
    win_count = 0
    loss_count = 0
    scratch_count = 0
    total_r = 0.0

    for t in trades:
        # Check r_multiple first, then realized_r, then compute from prices
        r_val = getattr(t, "r_multiple", None)
        if r_val is None:
            r_val = getattr(t, "realized_r", None)
        if (
            r_val is None
            and getattr(t, "close_price", None) is not None
            and getattr(t, "open_price", None) is not None
            and getattr(t, "stop_loss", None) is not None
        ):
            risk_dist = abs(t.open_price - t.stop_loss)
            if risk_dist > 0:
                is_l = t.action == ForexAction.LONG
                move = (t.close_price - t.open_price) if is_l else (t.open_price - t.close_price)
                r_val = round(move / risk_dist, 2)

        if r_val is not None:
            if r_val > 0.05:
                win_count += 1
            elif r_val < -0.05:
                loss_count += 1
            else:
                scratch_count += 1
            total_r += r_val
        else:
            gp = getattr(t, "gross_profit", 0.0) or 0.0
            if gp > 0:
                win_count += 1
                total_r += 1.5
            elif gp < 0:
                loss_count += 1
                total_r -= 1.0
            else:
                scratch_count += 1

    settled_total = win_count + loss_count
    win_rate = round((win_count / settled_total) * 100.0, 1) if settled_total > 0 else 0.0
    avg_r = round(total_r / total_trades, 2) if total_trades > 0 else 0.0
    loss_rate = 1.0 - (win_rate / 100.0) if settled_total > 0 else 0.0
    win_fraction = win_rate / 100.0
    expectancy = round(
        (win_fraction * (total_r / max(1, win_count) if win_count > 0 else 0.0)) - (loss_rate * 1.0),
        2,
    )

    # Slippage and friction
    avg_slip = 0.0
    total_friction = 0.0
    if executions:
        from tradingagents.metrics.execution import (
            calculate_slippage_cost,
            calculate_spread_cost,
        )

        slip_sum = sum(abs(getattr(e, "slippage_pips", 0.0) or 0.0) for e in executions)
        avg_slip = round(slip_sum / len(executions), 2) if executions else 0.0
        total_friction = round(
            sum(
                (
                    getattr(e, "slippage_cost", None)
                    or calculate_slippage_cost(
                        slippage_pips=getattr(e, "slippage_pips", 0.0),
                        volume=getattr(e, "volume", 0.1),
                        pair=getattr(e, "pair", "EURUSD"),
                    )
                )
                + (
                    getattr(e, "spread_cost", None)
                    or calculate_spread_cost(
                        spread_pips=getattr(e, "spread_at_open_pips", 0.0),
                        volume=getattr(e, "volume", 0.1),
                        pair=getattr(e, "pair", "EURUSD"),
                    )
                )
                + (getattr(e, "commission", 0.0) or 0.0)
                for e in executions
            ),
            2,
        )

    return UserExecutionMetrics(
        total_executed_trades=total_trades,
        win_count=win_count,
        loss_count=loss_count,
        scratch_count=scratch_count,
        win_rate_pct=win_rate,
        total_realized_r=round(total_r, 2),
        avg_realized_r=avg_r,
        expectancy_r=expectancy,
        avg_slippage_pips=avg_slip,
        total_friction_usd=total_friction,
    )


def compare_ai_vs_user_performance(
    proposals: Sequence[ProposalRecord],
    simulations: Sequence[SkippedProposalSimulation] | dict[str, SkippedProposalSimulation],
    trades: Sequence[TradeJournalRecord],
    executions: Sequence[OrderExecutionRecord] | None = None,
) -> ComparativePerformanceSummary:
    """Generate institutional comparative summary between AI theoretical edge and user execution."""
    sim_list: list[SkippedProposalSimulation] = (
        list(simulations.values()) if isinstance(simulations, dict) else list(simulations)
    )

    ai_metrics = calculate_ai_theoretical_performance(proposals=proposals, simulations=sim_list)
    user_metrics = calculate_user_execution_performance(trades=trades, executions=executions)

    # Analyze skipped proposals specifically
    avoided_losses = 0
    missed_winners = 0
    untriggered = 0
    ambiguous = 0
    avoided_loss_r = 0.0
    missed_profit_r = 0.0

    for sim in sim_list:
        if not sim.entry_triggered or sim.status == ProposalSimulationStatus.EXPIRED_NOT_TRIGGERED:
            untriggered += 1
            continue
        if sim.status == ProposalSimulationStatus.AMBIGUOUS or sim.is_ambiguous:
            ambiguous += 1
            continue
        if sim.status == ProposalSimulationStatus.HIT_SL:
            avoided_losses += 1
            avoided_loss_r += 1.0  # Saved 1.0R by not trading
        elif sim.status in (ProposalSimulationStatus.HIT_TP1, ProposalSimulationStatus.HIT_TP2):
            missed_winners += 1
            missed_profit_r += (sim.theoretical_r if sim.theoretical_r is not None else 1.5)

    # User Skip Alpha: Net R benefit of skipping
    # Positive means skipping toxic setups saved more R than missed winners cost
    user_skip_alpha = round(avoided_loss_r - missed_profit_r, 2)
    decided_skips = avoided_losses + missed_winners
    skip_eff = round((avoided_losses / decided_skips) * 100.0, 1) if decided_skips > 0 else 0.0

    if user_skip_alpha > 0:
        verdict = (
            f"User discretion added +{user_skip_alpha:.2f}R net alpha by filtering out {avoided_losses} "
            f"toxic setups that hit SL, while only missing {missed_winners} winning moves."
        )
    elif user_skip_alpha < 0:
        verdict = (
            f"User discretion dragged performance by {user_skip_alpha:.2f}R, missing {missed_winners} "
            f"profitable AI setups while only avoiding {avoided_losses} losses."
        )
    else:
        verdict = "User discretion had neutral net R impact relative to baseline AI proposals."

    return ComparativePerformanceSummary(
        ai_theoretical=ai_metrics,
        user_execution=user_metrics,
        skipped_proposals_count=len(sim_list),
        skipped_avoided_losses=avoided_losses,
        skipped_missed_winners=missed_winners,
        skipped_untriggered=untriggered,
        skipped_ambiguous=ambiguous,
        user_skip_alpha_r=user_skip_alpha,
        skip_efficiency_pct=skip_eff,
        verdict=verdict,
    )
