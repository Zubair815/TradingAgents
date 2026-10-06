"""Comprehensive Institutional Forex Performance Analytics Engine (Phase 19).

Computes deterministic overall metrics, multi-dimensional segmentation,
broker execution friction diagnostics, and separate theoretical vs realized performance.

Requirements (Phase 19):
- Overall: trade count, wins, losses, breakeven, win rate, average winner, average loser,
  average R, median R, expectancy, profit factor, gross P/L, net P/L, maximum drawdown,
  loss streak, holding duration, average MFE, average MAE.
- Segment by: pair, timeframe, setup, session, direction, weekday, regime, news condition,
  model, provider, prompt version, strategy version, confidence band.
- Execution metrics: entry deviation, SL changes, TP changes, manual exits, partial close behavior.
- Separate: proposal theoretical performance vs actual executed performance.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from tradingagents.database.models import (
    TradeStatus,
)
from tradingagents.forex.pips import pip_size_for
from tradingagents.metrics.mfe_mae import parse_utc_timestamp

logger = logging.getLogger(__name__)


def is_settled_trade(trade: Any) -> bool:
    """Return whether a journal-like record has a realized outcome."""
    status = getattr(trade, "status", None)
    if isinstance(trade, dict):
        status = trade.get("status", status)
        net_profit = trade.get("net_profit")
        close_time = trade.get("close_time_utc")
    else:
        net_profit = getattr(trade, "net_profit", None)
        close_time = getattr(trade, "close_time_utc", None)
    status_value = getattr(status, "value", status)
    return str(status_value).upper() == TradeStatus.CLOSED.value or (
        net_profit is not None and close_time is not None
    )


def get_excursion_value(trade: Any, key: str) -> Any:
    """Read excursion evidence from canonical nested metadata with legacy fallback."""
    direct = trade.get(key) if isinstance(trade, dict) else getattr(trade, key, None)
    if direct is not None:
        return direct
    metadata = trade.get("metadata") if isinstance(trade, dict) else getattr(trade, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    nested = metadata.get("mfe_mae")
    if isinstance(nested, dict) and nested.get(key) is not None:
        return nested[key]
    return metadata.get(key)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class PerformanceMetricsSummary(BaseModel):
    """Deterministic trading performance metrics across a group of trades."""

    trade_count: int = Field(default=0, ge=0, description="Total completed/evaluated trades")
    wins: int = Field(default=0, ge=0, description="Winning trades count")
    losses: int = Field(default=0, ge=0, description="Losing trades count")
    breakeven: int = Field(default=0, ge=0, description="Breakeven/scratch trades count")
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Win rate decimal (0.0 to 1.0)")
    win_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Win rate percentage")
    average_winner: float = Field(default=0.0, description="Average net profit of winning trades ($)")
    average_winner_r: float = Field(default=0.0, description="Average R multiple of winning trades")
    average_loser: float = Field(default=0.0, description="Average loss of losing trades ($)")
    average_loser_r: float = Field(default=0.0, description="Average R multiple of losing trades")
    average_r: float = Field(default=0.0, description="Mean realized R multiple across all trades")
    median_r: float = Field(default=0.0, description="Median realized R multiple")
    expectancy: float = Field(default=0.0, description="Expectancy in R: (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)")
    expectancy_cash: float = Field(default=0.0, description="Expectancy in cash ($) per trade")
    profit_factor: float = Field(default=0.0, ge=0.0, description="Gross profit divided by gross loss")
    gross_profit: float = Field(default=0.0, description="Sum of positive profits ($)")
    gross_loss: float = Field(default=0.0, description="Absolute sum of losses ($)")
    net_profit: float = Field(default=0.0, description="Net profit/loss ($)")
    maximum_drawdown: float = Field(default=0.0, ge=0.0, description="Peak-to-trough maximum drawdown ($)")
    maximum_drawdown_pct: float = Field(default=0.0, ge=0.0, description="Maximum drawdown percentage (%)")
    loss_streak: int = Field(default=0, ge=0, description="Maximum consecutive losing trades")
    win_streak: int = Field(default=0, ge=0, description="Maximum consecutive winning trades")
    holding_duration_seconds: float = Field(default=0.0, ge=0.0, description="Average trade duration in seconds")
    holding_duration_formatted: str = Field(default="0h 0m", description="Human-readable average hold duration")
    average_mfe_r: float = Field(default=0.0, description="Average peak MFE in R")
    average_mfe_pips: float = Field(default=0.0, description="Average peak MFE in pips")
    average_mae_r: float = Field(default=0.0, description="Average adverse MAE in R")
    average_mae_pips: float = Field(default=0.0, description="Average adverse MAE in pips")


class ExecutionAnalyticsMetrics(BaseModel):
    """Broker execution friction, manual deviations, and mid-trade management diagnostics."""

    entry_deviation_pips: float = Field(default=0.0, description="Mean entry price deviation between proposal and fill")
    max_entry_deviation_pips: float = Field(default=0.0, description="Maximum entry price deviation in pips")
    sl_changes_count: int = Field(default=0, ge=0, description="Total stop loss adjustments during trade lifecycle")
    tp_changes_count: int = Field(default=0, ge=0, description="Total take profit adjustments during trade lifecycle")
    manual_exits_count: int = Field(default=0, ge=0, description="Trades closed via discretionary manual intervention")
    manual_exits_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of trades exited manually")
    partial_close_count: int = Field(default=0, ge=0, description="Trades with partial scale-outs/closes")
    partial_close_volume: float = Field(default=0.0, ge=0.0, description="Total volume scaled out partially")
    partial_close_avg_captured_r: float = Field(default=0.0, description="Average R multiple captured at partial close")


class SegmentationBreakdown(BaseModel):
    """Multi-dimensional performance breakdown across all institutional dimensions."""

    by_pair: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_timeframe: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_setup: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_session: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_direction: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_weekday: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_regime: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_news_condition: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_model: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_provider: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_prompt_version: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_strategy_version: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_system_version: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)
    by_confidence_band: dict[str, PerformanceMetricsSummary] = Field(default_factory=dict)


class ForexPerformanceReport(BaseModel):
    """Consolidated institutional performance analytics scorecard."""

    overall: PerformanceMetricsSummary = Field(default_factory=PerformanceMetricsSummary)
    theoretical: PerformanceMetricsSummary = Field(default_factory=PerformanceMetricsSummary)
    realized: PerformanceMetricsSummary = Field(default_factory=PerformanceMetricsSummary)
    execution: ExecutionAnalyticsMetrics = Field(default_factory=ExecutionAnalyticsMetrics)
    segmentation: SegmentationBreakdown = Field(default_factory=SegmentationBreakdown)
    generated_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    summary_markdown: str = Field(default="", description="Rendered institutional Markdown scorecard")


# ---------------------------------------------------------------------------
# Session & Dimension Helpers
# ---------------------------------------------------------------------------


def detect_trade_session(dt: datetime | str | None) -> str:
    """Classify UTC trade timestamp into standard Forex market trading sessions."""
    d = parse_utc_timestamp(dt)
    if d is None:
        return "UNKNOWN"
    hour = d.hour
    if 0 <= hour < 7:
        return "ASIAN"
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "LONDON_NY_OVERLAP"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "ASIAN"


def detect_weekday_name(dt: datetime | str | None) -> str:
    """Return English weekday name (Monday to Friday)."""
    d = parse_utc_timestamp(dt)
    if d is None:
        return "UNKNOWN"
    return d.strftime("%A")


def detect_confidence_band(val: Any) -> str:
    """Map raw confidence to discrete bucket label (50-60, 60-70, 70-80, 80-90, 90-100, <50)."""
    if val is None:
        return "UNRATED"
    try:
        c = float(val)
        if 0.0 < c <= 1.0:
            c = c * 100.0
        if c < 50.0:
            return "<50"
        if c < 60.0:
            return "50-60"
        if c < 70.0:
            return "60-70"
        if c < 80.0:
            return "70-80"
        if c < 90.0:
            return "80-90"
        return "90-100"
    except (ValueError, TypeError):
        return "UNRATED"


# ---------------------------------------------------------------------------
# Performance Engine
# ---------------------------------------------------------------------------


class ForexPerformanceEngine:
    """Institutional deterministic metrics engine for Forex trade analysis."""

    def __init__(self, initial_capital: float = 100000.0) -> None:
        self.initial_capital = initial_capital

    def calculate_metrics(
        self,
        trades: Sequence[Any],
        initial_capital: float | None = None,
    ) -> PerformanceMetricsSummary:
        """Compute all deterministic overall performance metrics for a sequence of trades."""
        cap = initial_capital if initial_capital is not None else self.initial_capital
        if not trades:
            return PerformanceMetricsSummary()

        trade_count = len(trades)
        wins = 0
        losses = 0
        breakevens = 0

        win_pnls: list[float] = []
        loss_pnls: list[float] = []
        win_rs: list[float] = []
        loss_rs: list[float] = []
        all_rs: list[float] = []

        durations: list[float] = []
        mfe_rs: list[float] = []
        mfe_pips: list[float] = []
        mae_rs: list[float] = []
        mae_pips: list[float] = []

        streak_loss = 0
        max_loss_streak = 0
        streak_win = 0
        max_win_streak = 0

        # Sort trades chronologically if timestamps available
        def _get_ts(t: Any) -> datetime:
            ts = getattr(t, "close_time_utc", None) or getattr(t, "open_time_utc", None)
            if ts is None and isinstance(t, dict):
                ts = t.get("close_time_utc") or t.get("open_time_utc")
            dt = parse_utc_timestamp(ts)
            return dt or datetime.min.replace(tzinfo=timezone.utc)

        sorted_trades = sorted(trades, key=_get_ts)

        # Equity tracking for Maximum Drawdown
        equity = cap
        peak_equity = cap
        max_dd_cash = 0.0
        max_dd_pct = 0.0

        for t in sorted_trades:
            # Extract PnL and R
            pnl = getattr(t, "net_profit", None)
            if pnl is None and isinstance(t, dict):
                pnl = t.get("net_profit") or t.get("gross_profit")
            pnl_val = float(pnl) if pnl is not None else 0.0

            r_val = getattr(t, "r_multiple", None)
            if r_val is None and hasattr(t, "theoretical_r"):
                r_val = getattr(t, "theoretical_r", None)
            if r_val is None and isinstance(t, dict):
                r_val = t.get("r_multiple") or t.get("theoretical_r")
            r_num = float(r_val) if r_val is not None else (1.0 if pnl_val > 0 else (-1.0 if pnl_val < 0 else 0.0))
            all_rs.append(r_num)

            # Classify win / loss / breakeven
            if r_num > 0.05 or pnl_val > 0.0:
                wins += 1
                win_pnls.append(pnl_val)
                win_rs.append(r_num)
                streak_win += 1
                max_win_streak = max(max_win_streak, streak_win)
                streak_loss = 0
            elif r_num < -0.05 or pnl_val < 0.0:
                losses += 1
                loss_pnls.append(abs(pnl_val))
                loss_rs.append(r_num)
                streak_loss += 1
                max_loss_streak = max(max_loss_streak, streak_loss)
                streak_win = 0
            else:
                breakevens += 1
                streak_win = 0
                streak_loss = 0

            # Excursion tracking
            mfe_r_val = get_excursion_value(t, "mfe_r")
            if mfe_r_val is not None:
                mfe_rs.append(float(mfe_r_val))

            mfe_p_val = get_excursion_value(t, "mfe_pips")
            if mfe_p_val is not None:
                mfe_pips.append(float(mfe_p_val))

            mae_r_val = get_excursion_value(t, "mae_r")
            if mae_r_val is not None:
                mae_rs.append(float(mae_r_val))

            mae_p_val = get_excursion_value(t, "mae_pips")
            if mae_p_val is not None:
                mae_pips.append(float(mae_p_val))

            # Holding duration
            t_open = getattr(t, "open_time_utc", None)
            t_close = getattr(t, "close_time_utc", None)
            if isinstance(t, dict):
                t_open = t_open or t.get("open_time_utc")
                t_close = t_close or t.get("close_time_utc")
            d_open = parse_utc_timestamp(t_open)
            d_close = parse_utc_timestamp(t_close)
            if d_open and d_close and d_close >= d_open:
                durations.append((d_close - d_open).total_seconds())

            # Drawdown update
            equity += pnl_val
            if equity > peak_equity:
                peak_equity = equity
            dd_cash = peak_equity - equity
            dd_pct = (dd_cash / peak_equity * 100.0) if peak_equity > 0 else 0.0
            max_dd_cash = max(max_dd_cash, dd_cash)
            max_dd_pct = max(max_dd_pct, dd_pct)

        win_rate = round(wins / trade_count, 4) if trade_count > 0 else 0.0
        win_rate_pct = round(win_rate * 100.0, 1)

        gross_profit = round(sum(win_pnls), 2)
        gross_loss = round(sum(loss_pnls), 2)
        net_profit = round(gross_profit - gross_loss, 2)

        profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )

        avg_winner = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
        avg_winner_r = round(sum(win_rs) / len(win_rs), 2) if win_rs else 0.0
        avg_loser = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
        avg_loser_r = round(sum(loss_rs) / len(loss_rs), 2) if loss_rs else 0.0

        avg_r = round(sum(all_rs) / len(all_rs), 2) if all_rs else 0.0
        median_r = round(float(statistics.median(all_rs)), 2) if all_rs else 0.0

        # Expectancy: (P(Win) * AvgWinR) + (P(Loss) * AvgLossR)
        loss_rate = losses / trade_count if trade_count > 0 else 0.0
        expectancy = round((win_rate * avg_winner_r) + (loss_rate * avg_loser_r), 2)
        expectancy_cash = round((win_rate * avg_winner) - (loss_rate * avg_loser), 2)

        avg_duration_sec = round(sum(durations) / len(durations), 1) if durations else 0.0
        hrs = int(avg_duration_sec // 3600)
        mins = int((avg_duration_sec % 3600) // 60)
        duration_fmt = f"{hrs}h {mins}m" if avg_duration_sec > 0 else "N/A"

        avg_mfe_r = round(sum(mfe_rs) / len(mfe_rs), 2) if mfe_rs else 0.0
        avg_mfe_p = round(sum(mfe_pips) / len(mfe_pips), 1) if mfe_pips else 0.0
        avg_mae_r = round(sum(mae_rs) / len(mae_rs), 2) if mae_rs else 0.0
        avg_mae_p = round(sum(mae_pips) / len(mae_pips), 1) if mae_pips else 0.0

        return PerformanceMetricsSummary(
            trade_count=trade_count,
            wins=wins,
            losses=losses,
            breakeven=breakevens,
            win_rate=win_rate,
            win_rate_pct=win_rate_pct,
            average_winner=avg_winner,
            average_winner_r=avg_winner_r,
            average_loser=avg_loser,
            average_loser_r=avg_loser_r,
            average_r=avg_r,
            median_r=median_r,
            expectancy=expectancy,
            expectancy_cash=expectancy_cash,
            profit_factor=profit_factor,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_profit=net_profit,
            maximum_drawdown=round(max_dd_cash, 2),
            maximum_drawdown_pct=round(max_dd_pct, 2),
            loss_streak=max_loss_streak,
            win_streak=max_win_streak,
            holding_duration_seconds=avg_duration_sec,
            holding_duration_formatted=duration_fmt,
            average_mfe_r=avg_mfe_r,
            average_mfe_pips=avg_mfe_p,
            average_mae_r=avg_mae_r,
            average_mae_pips=avg_mae_p,
        )

    def calculate_execution_metrics(
        self,
        trades: Sequence[Any],
        events: Sequence[Any] | None = None,
        proposals: Sequence[Any] | None = None,
    ) -> ExecutionAnalyticsMetrics:
        """Compute execution friction, entry slippage, SL/TP changes, and manual exits."""
        if not trades:
            return ExecutionAnalyticsMetrics()

        prop_map: dict[str, Any] = {}
        if proposals:
            for p in proposals:
                pid = getattr(p, "proposal_id", None) or (p.get("proposal_id") if isinstance(p, dict) else None)
                if pid:
                    prop_map[pid] = p

        deviations: list[float] = []
        manual_exits = 0

        for t in trades:
            # Check entry deviation
            pair = getattr(t, "pair", "EURUSD") or "EURUSD"
            pip_sz = pip_size_for(pair)
            open_p = getattr(t, "open_price", None)
            prop_id = getattr(t, "proposal_id", None)

            if isinstance(t, dict):
                open_p = open_p or t.get("open_price")
                prop_id = prop_id or t.get("proposal_id")

            if open_p is not None and prop_id and prop_id in prop_map:
                prop = prop_map[prop_id]
                expected_p = getattr(prop, "entry_price", None)
                if isinstance(prop, dict):
                    expected_p = expected_p or prop.get("entry_price")
                if expected_p is not None and pip_sz > 0:
                    dev = round(abs(float(open_p) - float(expected_p)) / pip_sz, 2)
                    deviations.append(dev)

            # Check manual exit
            exit_r = getattr(t, "exit_reason", None)
            if hasattr(exit_r, "value"):
                exit_r = exit_r.value
            if isinstance(t, dict):
                exit_r = exit_r or t.get("exit_reason")
            exit_str = str(exit_r).upper() if exit_r else ""
            if "MANUAL" in exit_str:
                manual_exits += 1

        avg_dev = round(sum(deviations) / len(deviations), 2) if deviations else 0.0
        max_dev = max(deviations) if deviations else 0.0
        manual_pct = round((manual_exits / len(trades)) * 100.0, 1) if trades else 0.0

        # Timeline event analysis for SL changes, TP changes, partial closes
        sl_changes = 0
        tp_changes = 0
        partial_closes = 0
        partial_vol = 0.0
        partial_rs: list[float] = []

        if events:
            for ev in events:
                etype = getattr(ev, "event_type", None)
                if hasattr(etype, "value"):
                    etype = etype.value
                if isinstance(ev, dict):
                    etype = etype or ev.get("event_type")
                etype_str = str(etype).upper()

                if "SL_CHANGED" in etype_str or "STOP_LOSS_MODIFIED" in etype_str:
                    sl_changes += 1
                elif "TP_CHANGED" in etype_str or "TAKE_PROFIT_MODIFIED" in etype_str:
                    tp_changes += 1
                elif "PARTIAL_CLOSE" in etype_str:
                    partial_closes += 1
                    vol = getattr(ev, "volume", None)
                    if isinstance(ev, dict):
                        vol = vol or ev.get("volume")
                    if vol:
                        partial_vol += float(vol)
                    # Realized R at partial close if present in payload
                    payload = getattr(ev, "payload", {}) or {}
                    if isinstance(ev, dict):
                        payload = payload or ev.get("payload", {})
                    if isinstance(payload, dict) and "realized_r" in payload:
                        partial_rs.append(float(payload["realized_r"]))

        avg_part_r = round(sum(partial_rs) / len(partial_rs), 2) if partial_rs else 0.0

        return ExecutionAnalyticsMetrics(
            entry_deviation_pips=avg_dev,
            max_entry_deviation_pips=max_dev,
            sl_changes_count=sl_changes,
            tp_changes_count=tp_changes,
            manual_exits_count=manual_exits,
            manual_exits_pct=manual_pct,
            partial_close_count=partial_closes,
            partial_close_volume=round(partial_vol, 2),
            partial_close_avg_captured_r=avg_part_r,
        )

    def calculate_segmentation(self, trades: Sequence[Any]) -> SegmentationBreakdown:
        """Compute performance slices across all dimensions required by Phase 19."""
        breakdown = SegmentationBreakdown()
        if not trades:
            return breakdown

        def _get_field(t: Any, name: str, default: str = "UNKNOWN") -> str:
            val = getattr(t, name, None)
            if val is None and hasattr(t, "metadata") and isinstance(t.metadata, dict):
                val = t.metadata.get(name)
                if val is None and isinstance(t.metadata.get("version_metadata"), dict):
                    val = t.metadata["version_metadata"].get(name)
            if val is None and hasattr(t, "proposal") and t.proposal:
                val = getattr(t.proposal, name, None)
            if val is None and isinstance(t, dict):
                val = t.get(name)
                if val is None and isinstance(t.get("metadata"), dict):
                    val = t["metadata"].get(name)
                    if val is None and isinstance(t["metadata"].get("version_metadata"), dict):
                        val = t["metadata"]["version_metadata"].get(name)
            if hasattr(val, "value"):
                val = val.value
            return str(val) if val is not None else default

        # 1. By Pair
        pairs = {_get_field(t, "pair") for t in trades}
        for p in sorted(pairs):
            slice_trades = [t for t in trades if _get_field(t, "pair") == p]
            breakdown.by_pair[p] = self.calculate_metrics(slice_trades)

        # 2. By Timeframe
        timeframes = {_get_field(t, "timeframe", "H1") for t in trades}
        for tf in sorted(timeframes):
            slice_trades = [t for t in trades if _get_field(t, "timeframe", "H1") == tf]
            breakdown.by_timeframe[tf] = self.calculate_metrics(slice_trades)

        # 3. By Setup
        setups = {_get_field(t, "setup_type", "TREND_CONTINUATION") for t in trades}
        for s in sorted(setups):
            slice_trades = [t for t in trades if _get_field(t, "setup_type", "TREND_CONTINUATION") == s]
            breakdown.by_setup[s] = self.calculate_metrics(slice_trades)

        # 4. By Session
        for t in trades:
            ts = getattr(t, "open_time_utc", None) or (t.get("open_time_utc") if isinstance(t, dict) else None)
            sess = detect_trade_session(ts)
            setattr(t, "_session_cached", sess) if hasattr(t, "__dict__") else None

        sessions = {"ASIAN", "LONDON", "LONDON_NY_OVERLAP", "NEW_YORK"}
        for sess in sorted(sessions):
            slice_trades = [
                t for t in trades
                if getattr(t, "_session_cached", detect_trade_session(getattr(t, "open_time_utc", None) or (t.get("open_time_utc") if isinstance(t, dict) else None))) == sess
            ]
            if slice_trades:
                breakdown.by_session[sess] = self.calculate_metrics(slice_trades)

        # 5. By Direction (LONG vs SHORT)
        def _extract_act(t: Any) -> str:
            raw = getattr(t, "action", None)
            if raw is None and isinstance(t, dict):
                raw = t.get("action")
            if hasattr(raw, "value"):
                return str(raw.value).upper()
            s = str(raw or "").upper()
            if "." in s:
                s = s.split(".")[-1]
            return s

        for act in ("LONG", "SHORT"):
            slice_trades = [t for t in trades if _extract_act(t) == act]
            if slice_trades:
                breakdown.by_direction[act] = self.calculate_metrics(slice_trades)

        # 6. By Weekday
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        for day in weekdays:
            slice_trades = [
                t for t in trades
                if detect_weekday_name(getattr(t, "open_time_utc", None) or (t.get("open_time_utc") if isinstance(t, dict) else None)) == day
            ]
            if slice_trades:
                breakdown.by_weekday[day] = self.calculate_metrics(slice_trades)

        # 7. By Regime
        regimes = {_get_field(t, "regime", "TRENDING") for t in trades}
        for reg in sorted(regimes):
            slice_trades = [t for t in trades if _get_field(t, "regime", "TRENDING") == reg]
            breakdown.by_regime[reg] = self.calculate_metrics(slice_trades)

        # 8. By News Condition
        news_conditions = {_get_field(t, "news_condition", "CLEAN_CALENDAR") for t in trades}
        for nc in sorted(news_conditions):
            slice_trades = [t for t in trades if _get_field(t, "news_condition", "CLEAN_CALENDAR") == nc]
            breakdown.by_news_condition[nc] = self.calculate_metrics(slice_trades)

        # 9. By Model (Phase 23)
        def _get_model(t: Any) -> str:
            m = _get_field(t, "quick_model", "")
            if not m or m == "UNKNOWN":
                m = _get_field(t, "model_name", "")
            return m or "DEFAULT_MODEL"

        models = {_get_model(t) for t in trades}
        for m in sorted(models):
            slice_trades = [t for t in trades if _get_model(t) == m]
            breakdown.by_model[m] = self.calculate_metrics(slice_trades)

        # 10. By Provider (Phase 23)
        providers = {_get_field(t, "provider", "DEFAULT_PROVIDER") for t in trades}
        for prv in sorted(providers):
            slice_trades = [t for t in trades if _get_field(t, "provider", "DEFAULT_PROVIDER") == prv]
            breakdown.by_provider[prv] = self.calculate_metrics(slice_trades)

        # 11. By Prompt Version / Hash (Phase 23)
        def _get_prompt(t: Any) -> str:
            p = _get_field(t, "prompt_version", "")
            if not p or p == "UNKNOWN":
                p = _get_field(t, "prompt_hash", "")
            return p or "DEFAULT_PROMPT"

        prompts = {_get_prompt(t) for t in trades}
        for pmp in sorted(prompts):
            slice_trades = [t for t in trades if _get_prompt(t) == pmp]
            breakdown.by_prompt_version[pmp] = self.calculate_metrics(slice_trades)

        # 12. By Strategy Version (Phase 23)
        def _get_strategy(t: Any) -> str:
            s = _get_field(t, "strategy_version", "")
            if not s or s == "UNKNOWN":
                s = _get_field(t, "version_id", "")
            return s or "V1"

        strategies = {_get_strategy(t) for t in trades}
        for strat in sorted(strategies):
            slice_trades = [t for t in trades if _get_strategy(t) == strat]
            breakdown.by_strategy_version[strat] = self.calculate_metrics(slice_trades)

        # 13. By System Version (Phase 23)
        system_versions = {_get_field(t, "system_version", "1.0.0") for t in trades}
        for sys_ver in sorted(system_versions):
            slice_trades = [t for t in trades if _get_field(t, "system_version", "1.0.0") == sys_ver]
            breakdown.by_system_version[sys_ver] = self.calculate_metrics(slice_trades)

        # 13. By Confidence Band (Phase 18 Integration)
        confidence_bands = ["50-60", "60-70", "70-80", "80-90", "90-100", "<50"]
        for band in confidence_bands:
            slice_trades = [
                t for t in trades
                if detect_confidence_band(getattr(t, "confidence", None) or (t.get("confidence") if isinstance(t, dict) else None)) == band
            ]
            if slice_trades:
                breakdown.by_confidence_band[band] = self.calculate_metrics(slice_trades)

        return breakdown

    def generate_performance_report(
        self,
        trades: Sequence[Any],
        proposals: Sequence[Any] | None = None,
        events: Sequence[Any] | None = None,
        simulations: Sequence[Any] | None = None,
        initial_capital: float | None = None,
    ) -> ForexPerformanceReport:
        """Assemble full institutional performance report with separate theoretical vs realized tracking."""
        cap = initial_capital if initial_capital is not None else self.initial_capital

        # Realized performance across executed journal trades
        realized_trades = [t for t in trades if is_settled_trade(t)]
        realized_metrics = self.calculate_metrics(realized_trades, initial_capital=cap)

        # Theoretical performance across AI proposals / counterfactual simulations
        theoretical_items = list(simulations or [])
        if not theoretical_items and proposals:
            theoretical_items = list(proposals)
        theoretical_metrics = self.calculate_metrics(theoretical_items, initial_capital=cap)

        # Overall metrics (combined executed trades)
        overall_metrics = self.calculate_metrics(realized_trades, initial_capital=cap)

        # Execution friction & management
        exec_metrics = self.calculate_execution_metrics(trades=realized_trades, events=events, proposals=proposals)

        # Segmentation
        segmentation = self.calculate_segmentation(trades=realized_trades)

        # Markdown Scorecard
        md = self._render_performance_markdown(
            overall=overall_metrics,
            theoretical=theoretical_metrics,
            realized=realized_metrics,
            execution=exec_metrics,
            segmentation=segmentation,
        )

        return ForexPerformanceReport(
            overall=overall_metrics,
            theoretical=theoretical_metrics,
            realized=realized_metrics,
            execution=exec_metrics,
            segmentation=segmentation,
            summary_markdown=md,
        )

    def _render_performance_markdown(
        self,
        overall: PerformanceMetricsSummary,
        theoretical: PerformanceMetricsSummary,
        realized: PerformanceMetricsSummary,
        execution: ExecutionAnalyticsMetrics,
        segmentation: SegmentationBreakdown,
    ) -> str:
        """Render a GitHub-flavored institutional performance dashboard."""
        lines = [
            "# Comprehensive Forex Performance Analytics",
            "",
            "## 1. Overall Portfolio Performance",
            "",
            "| Metric | Value | Diagnostic / Target |",
            "|:---|:---:|:---|",
            f"| **Trade Count** | `{overall.trade_count}` | Total completed trades |",
            f"| **Win Rate** | `{overall.win_rate_pct:.1f}%` ({overall.wins}W / {overall.losses}L / {overall.breakeven}BE) | Institutional benchmark `> 50.0%` |",
            f"| **Profit Factor** | `{overall.profit_factor:.2f}` | Institutional benchmark `> 1.50` |",
            f"| **Expectancy per Trade** | `{overall.expectancy:+.2f}R` (`${overall.expectancy_cash:+,.2f}`) | Positive edge required |",
            f"| **Average Winner** | `{overall.average_winner_r:+.2f}R` (`${overall.average_winner:+,.2f}`) | Mean winning payout |",
            f"| **Average Loser** | `{overall.average_loser_r:+.2f}R` (`${overall.average_loser:+,.2f}`) | Mean loss containment |",
            f"| **Average R-Multiple** | `{overall.average_r:+.2f}R` | Mean trade outcome |",
            f"| **Median R-Multiple** | `{overall.median_r:+.2f}R` | Non-skewed central tendency |",
            f"| **Gross P/L** | Gross Wins: `${overall.gross_profit:+,.2f}` | Gross Losses: `${overall.gross_loss:,.2f}` |",
            f"| **Net Realized P/L** | `${overall.net_profit:+,.2f}` | Total portfolio return |",
            f"| **Maximum Drawdown** | `${overall.maximum_drawdown:,.2f}` (`{overall.maximum_drawdown_pct:.2f}%`) | Peak-to-trough capital decline |",
            f"| **Max Loss Streak** | `{overall.loss_streak}` trades | Consecutive loss risk |",
            f"| **Average Holding Duration** | `{overall.holding_duration_formatted}` | Holding efficiency |",
            f"| **Average Peak MFE** | `{overall.average_mfe_r:.2f}R` (`{overall.average_mfe_pips:.1f}` pips) | Target crest potential |",
            f"| **Average Adverse MAE** | `{overall.average_mae_r:.2f}R` (`{overall.average_mae_pips:.1f}` pips) | Stop protection buffer |",
            "",
            "## 2. Theoretical AI Edge vs Realized Human Execution",
            "",
            "| Performance Dimension | AI Theoretical Performance | Actual Executed Performance | Variance |",
            "|:---|:---:|:---:|:---:|",
            f"| **Trades Analyzed** | `{theoretical.trade_count}` | `{realized.trade_count}` | `{realized.trade_count - theoretical.trade_count:+d}` |",
            f"| **Win Rate** | `{theoretical.win_rate_pct:.1f}%` | `{realized.win_rate_pct:.1f}%` | `{realized.win_rate_pct - theoretical.win_rate_pct:+.1f}%` |",
            f"| **Profit Factor** | `{theoretical.profit_factor:.2f}` | `{realized.profit_factor:.2f}` | `{realized.profit_factor - theoretical.profit_factor:+.2f}` |",
            f"| **Average R** | `{theoretical.average_r:+.2f}R` | `{realized.average_r:+.2f}R` | `{realized.average_r - theoretical.average_r:+.2f}R` |",
            f"| **Expectancy** | `{theoretical.expectancy:+.2f}R` | `{realized.expectancy:+.2f}R` | `{realized.expectancy - theoretical.expectancy:+.2f}R` |",
            f"| **Net Profit** | `${theoretical.net_profit:+,.2f}` | `${realized.net_profit:+,.2f}` | `${realized.net_profit - theoretical.net_profit:+,.2f}` |",
            "",
            "## 3. Broker Execution Quality & Mid-Trade Management",
            "",
            "| Execution Metric | Value | Diagnostic / Interpretation |",
            "|:---|:---:|:---|",
            f"| **Average Entry Deviation** | `{execution.entry_deviation_pips:+.2f}` pips | Slippage/delay between proposed and filled entry |",
            f"| **Maximum Entry Deviation** | `{execution.max_entry_deviation_pips:+.2f}` pips | Worst entry execution gap |",
            f"| **Stop Loss Modifications** | `{execution.sl_changes_count}` adjustments | Trailing/moving stops during trade |",
            f"| **Take Profit Modifications** | `{execution.tp_changes_count}` adjustments | Dynamic target changes |",
            f"| **Manual Discretionary Exits** | `{execution.manual_exits_count}` (`{execution.manual_exits_pct:.1f}%`) | Exits cut manually before TP/SL |",
            f"| **Partial Closes Logged** | `{execution.partial_close_count}` (`{execution.partial_close_volume:.2f}` lots) | Scaling out behavior (Avg captured: `{execution.partial_close_avg_captured_r:+.2f}R`) |",
            "",
        ]

        # Segmentation Tables
        if segmentation.by_pair:
            lines.extend([
                "## 4. Multi-Dimensional Performance Breakdown",
                "",
                "### Performance by Pair",
                "",
                "| Pair | Trades | Win Rate | Profit Factor | Expectancy | Net P/L | Max DD % |",
                "|:---|:---:|:---:|:---:|:---:|:---:|:---:|",
            ])
            for p, m in segmentation.by_pair.items():
                lines.append(
                    f"| `{p}` | {m.trade_count} | {m.win_rate_pct:.1f}% | {m.profit_factor:.2f} | {m.expectancy:+.2f}R | ${m.net_profit:+,.2f} | {m.maximum_drawdown_pct:.1f}% |"
                )
            lines.append("")

        if segmentation.by_direction:
            lines.extend([
                "### Performance by Direction",
                "",
                "| Direction | Trades | Win Rate | Profit Factor | Expectancy | Net P/L |",
                "|:---|:---:|:---:|:---:|:---:|:---:|",
            ])
            for d, m in segmentation.by_direction.items():
                lines.append(
                    f"| **{d}** | {m.trade_count} | {m.win_rate_pct:.1f}% | {m.profit_factor:.2f} | {m.expectancy:+.2f}R | ${m.net_profit:+,.2f} |"
                )
            lines.append("")

        if segmentation.by_confidence_band:
            lines.extend([
                "### Performance by Confidence Band",
                "",
                "| Confidence Band | Trades | Win Rate | Average R | Expectancy | Net P/L |",
                "|:---|:---:|:---:|:---:|:---:|:---:|",
            ])
            for band, m in segmentation.by_confidence_band.items():
                lines.append(
                    f"| **{band}%** | {m.trade_count} | {m.win_rate_pct:.1f}% | {m.average_r:+.2f}R | {m.expectancy:+.2f}R | ${m.net_profit:+,.2f} |"
                )
            lines.append("")

        return "\n".join(lines)
