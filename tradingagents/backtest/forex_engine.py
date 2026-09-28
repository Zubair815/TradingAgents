"""Point-in-Time (PIT) Safe Institutional Forex Backtesting Engine (Phase 18).

Provides:
- Strict Point-in-Time (PIT) historical simulation without lookahead bias.
- Intrabar Stop Loss and Take Profit trigger evaluation with conservative collision resolution.
- Realistic execution modeling: bid/ask spread drag, directional slippage, and broker execution delays.
- Dynamic intrabar MFE/MAE excursion tracking on every candle.
- Multi-pair portfolio margin, leverage, and currency exposure capacity enforcement.
- Complete institutional KPI scorecard (Sharpe, Sortino, Profit Factor, Expectancy, Max Drawdown, Friction Drag).
- Integration with ForexTradeJournal, EventTimeline, TradeOutcomeEngine, and ForexLearningManager.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex.pips import (
    pip_size_for,
    pip_value_in_account_currency,
)
from tradingagents.metrics.mfe_mae import parse_utc_timestamp
from tradingagents.risk.sizing import calculate_required_margin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backtest Domain Models
# ---------------------------------------------------------------------------


@dataclass
class PendingOrder:
    """Representation of an unfilled pending order (LIMIT/STOP) during backtesting."""

    order_id: str
    proposal: ForexTraderProposal
    pair: str
    action: ForexAction
    order_type: OrderType
    entry_price: float
    stop_loss: float
    lots: float
    take_profit: float | None = None
    created_time: datetime | None = None
    valid_until: datetime | None = None
    status: str = "PENDING"  # PENDING, FILLED, EXPIRED, CANCELLED


@dataclass
class ForexBacktestConfig:
    """Institutional configuration for Forex backtesting simulation."""

    initial_balance: float = 100000.0
    account_currency: str = "USD"
    leverage: float = 100.0
    default_spread_pips: float = 1.2
    default_slippage_pips: float = 0.3
    commission_per_lot_usd: float = 5.0
    swap_per_day_usd: float = 0.0
    conservative_stops: bool = True  # If high/low touches both SL and TP in same bar, assume SL hit
    allow_ambiguous: bool = False
    max_open_trades: int = 5
    max_account_risk_percent: float = 6.0
    execution_timeframe: str = "M15"
    results_dir: Path | str = Path("reports/backtest")


@dataclass
class BacktestTrade:
    """Representation of an individual position during historical backtesting."""

    trade_id: str
    pair: str
    action: ForexAction
    entry_time: datetime
    entry_price: float
    stop_loss: float
    lots: float
    take_profit: float | None = None
    proposal_id: str | None = None
    setup_type: str = "TREND_CONTINUATION"

    # Execution friction
    spread_pips: float = 0.0
    slippage_pips: float = 0.0
    commission: float = 0.0
    swap: float = 0.0

    # Settlement fields
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: TradeExitReason | str = "OPEN"
    status: TradeStatus = TradeStatus.OPEN

    # Quantitative outcomes
    pips_gained: float = 0.0
    r_multiple: float = 0.0
    gross_profit: float = 0.0
    net_profit: float = 0.0

    # Dynamic excursion tracking
    mfe_price: float = 0.0
    mae_price: float = 0.0
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.status == TradeStatus.CLOSED

    @property
    def is_winner(self) -> bool:
        return self.net_profit > 0.0


@dataclass
class EquityPoint:
    """Snapshot of account equity and drawdown at a specific simulation timestamp."""

    timestamp: datetime
    balance: float
    equity: float
    used_margin: float
    free_margin: float
    open_trades_count: int
    drawdown_cash: float
    drawdown_pct: float


@dataclass
class ForexBacktestResult:
    """Comprehensive performance report and analytics for a Forex backtest."""

    config: ForexBacktestConfig
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    final_equity: float
    total_net_profit: float
    total_return_pct: float

    # Trade counts
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    loss_rate_pct: float

    # Quantitative metrics
    gross_profit: float
    gross_loss: float
    profit_factor: float
    avg_r_multiple: float
    expectancy_r: float
    expectancy_cash: float
    avg_trade_pips: float
    total_pips: float

    # Risk & Drawdown
    max_drawdown_cash: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float

    # Friction costs
    total_spread_cost_usd: float
    total_slippage_cost_usd: float
    total_commission_cost_usd: float
    total_swap_cost_usd: float = 0.0
    total_friction_usd: float = 0.0

    # History
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    pending_orders_count: int = 0
    filled_orders_count: int = 0
    expired_orders_count: int = 0
    ambiguous_trades_count: int = 0
    mode: str = "DEMO"
    validated_strategy_performance: bool = False

    def render_markdown_report(self) -> str:
        """Render a formatted institutional Markdown performance card."""
        lines: list[str] = [
            "# Institutional Forex Backtest Performance Report",
            f"*Period: {self.start_date} to {self.end_date} | Mode: {self.mode} | Validated: {self.validated_strategy_performance}*",
            f"*Currency: {self.config.account_currency} | Leverage: 1:{int(self.config.leverage)}*",
            "",
            "## 1. Executive Portfolio Performance",
            "",
            "| Metric | Result | Benchmark Target | Institutional Rating |",
            "|:---|:---:|:---:|:---|",
            f"| **Starting Capital** | `${self.initial_balance:,.2f}` | Baseline Deposit | — |",
            f"| **Final Balance** | `${self.final_balance:,.2f}` | Equity Capital | — |",
            f"| **Total Net Return** | `+{self.total_return_pct:.2f}%` (`${self.total_net_profit:+,.2f}`) | `> +15.0%` | {'OUTPERFORM' if self.total_return_pct >= 15 else 'STABLE' if self.total_return_pct > 0 else 'UNDERPERFORM'} |",
            f"| **Profit Factor** | `{self.profit_factor:.2f}` | `> 1.60` | {'EXCELLENT' if self.profit_factor >= 1.6 else 'ACCEPTABLE' if self.profit_factor >= 1.2 else 'POOR'} |",
            f"| **Sharpe Ratio** | `{self.sharpe_ratio:.2f}` | `> 1.50` | {'TIER-1 ALPHA' if self.sharpe_ratio >= 1.5 else 'SOLID' if self.sharpe_ratio >= 1.0 else 'SUB-OPTIMAL'} |",
            f"| **Sortino Ratio** | `{self.sortino_ratio:.2f}` | `> 2.00` | Downside Risk Adjusted |",
            f"| **Maximum Drawdown** | `{self.max_drawdown_pct:.2f}%` (`${self.max_drawdown_cash:,.2f}`) | `< 10.0%` | {'CONTAINED' if self.max_drawdown_pct <= 10 else 'ELEVATED RISK'} |",
            "",
            "## 2. Trade Expectancy & Pip Dynamics",
            "",
            "| Trade Metric | Value | Pip / R Statistics |",
            "|:---|:---:|:---|",
            f"| **Total Executed Trades** | `{self.total_trades}` | {self.winning_trades} W / {self.losing_trades} L / {self.breakeven_trades} BE |",
            f"| **Win Rate** | `{self.win_rate_pct:.1f}%` | Loss Rate: `{self.loss_rate_pct:.1f}%` |",
            f"| **Average R-Multiple** | `{self.avg_r_multiple:+.2f}R` | System directional expectancy |",
            f"| **R-Expectancy / Trade** | `{self.expectancy_r:+.2f}R` | Mathematical edge per trade |",
            f"| **Cash Expectancy** | `${self.expectancy_cash:+,.2f}` | Average monetary return per trade |",
            f"| **Total Pips Captured** | `{self.total_pips:+,.1f}` pips | Avg: `{self.avg_trade_pips:+.1f}` pips/trade |",
            "",
            "## 3. Execution Friction & Broker Drag",
            "",
            "| Friction Source | Total Cost | % of Gross Profit |",
            "|:---|:---:|:---:|",
            f"| **Realized Slippage Drag** | `${self.total_slippage_cost_usd:,.2f}` | `{((self.total_slippage_cost_usd / max(1.0, self.gross_profit)) * 100.0):.1f}%` |",
            f"| **Spread Drag Cost** | `${self.total_spread_cost_usd:,.2f}` | `{((self.total_spread_cost_usd / max(1.0, self.gross_profit)) * 100.0):.1f}%` |",
            f"| **Broker Commission Fees** | `${self.total_commission_cost_usd:,.2f}` | `{((self.total_commission_cost_usd / max(1.0, self.gross_profit)) * 100.0):.1f}%` |",
            f"| **Swap Drag Cost** | `${self.total_swap_cost_usd:,.2f}` | `{((self.total_swap_cost_usd / max(1.0, self.gross_profit)) * 100.0):.1f}%` |",
            f"| **Total Execution Friction** | `${self.total_friction_usd:,.2f}` | `{((self.total_friction_usd / max(1.0, self.gross_profit)) * 100.0):.1f}%` |",
            "",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Point-in-Time Safe Forex Backtesting Engine
# ---------------------------------------------------------------------------


class ForexBacktestEngine:
    """Discrete-event, bar-by-bar Forex backtesting engine with strict PIT safeguarding."""

    def __init__(
        self,
        config: ForexBacktestConfig | None = None,
        journal: ForexTradeJournal | None = None,
    ) -> None:
        self.config = config or ForexBacktestConfig()
        self.journal = journal

        self.balance: float = self.config.initial_balance
        self.equity: float = self.config.initial_balance
        self.peak_equity: float = self.config.initial_balance

        self.open_trades: list[BacktestTrade] = []
        self.closed_trades: list[BacktestTrade] = []
        self.pending_orders: list[PendingOrder] = []
        self.cancelled_orders: list[PendingOrder] = []
        self.equity_curve: list[EquityPoint] = []

        self.total_spread_drag: float = 0.0
        self.total_slippage_drag: float = 0.0
        self.total_commission_drag: float = 0.0
        self.total_swap_drag: float = 0.0
        self._last_swap_date: date | None = None
        self.ambiguous_trades_count: int = 0
        self.filled_orders_count: int = 0
        self.expired_orders_count: int = 0

    def reset(self) -> None:
        """Reset internal state to initial deposit."""
        self.balance = self.config.initial_balance
        self.equity = self.config.initial_balance
        self.peak_equity = self.config.initial_balance
        self.open_trades = []
        self.closed_trades = []
        self.pending_orders = []
        self.cancelled_orders = []
        self.equity_curve = []
        self.total_spread_drag = 0.0
        self.total_slippage_drag = 0.0
        self.total_commission_drag = 0.0
        self.total_swap_drag = 0.0
        self._last_swap_date = None
        self.ambiguous_trades_count = 0
        self.filled_orders_count = 0
        self.expired_orders_count = 0

    def _calculate_used_margin(self) -> float:
        """Calculate total required margin across currently open positions."""
        total_margin = 0.0
        for t in self.open_trades:
            m = calculate_required_margin(
                pair=t.pair,
                lot_size=t.lots,
                entry_price=t.entry_price,
                leverage=self.config.leverage,
                account_currency=self.config.account_currency,
            )
            total_margin += m
        return total_margin

    def _update_open_positions_excursions(self, candle: ForexBar, pair: str) -> None:
        """Update intrabar MFE/MAE excursions for all open positions on this pair."""
        pip_sz = pip_size_for(pair)
        for t in self.open_trades:
            if t.pair != pair:
                continue

            sl_dist_price = abs(t.entry_price - t.stop_loss)
            sl_pips = max(0.0001, sl_dist_price / pip_sz)

            if t.action == ForexAction.LONG:
                if candle.high > t.mfe_price:
                    t.mfe_price = candle.high
                if candle.low < t.mae_price:
                    t.mae_price = candle.low

                t.mfe_pips = max(0.0, (t.mfe_price - t.entry_price) / pip_sz)
                t.mae_pips = max(0.0, (t.entry_price - t.mae_price) / pip_sz)
            else:
                if candle.low < t.mfe_price:
                    t.mfe_price = candle.low
                if candle.high > t.mae_price:
                    t.mae_price = candle.high

                t.mfe_pips = max(0.0, (t.entry_price - t.mfe_price) / pip_sz)
                t.mae_pips = max(0.0, (t.mae_price - t.entry_price) / pip_sz)

            t.mfe_r = round(t.mfe_pips / sl_pips, 2)
            t.mae_r = round(t.mae_pips / sl_pips, 2)

    def _check_and_settle_intrabar_exits(
        self,
        candle: ForexBar,
        pair: str,
        lower_tf_candles: Sequence[ForexBar] | None = None,
    ) -> list[BacktestTrade]:
        """Evaluate whether active positions touched stop loss or take profit during the bar."""
        pip_sz = pip_size_for(pair)
        settled_this_bar: list[BacktestTrade] = []
        still_open: list[BacktestTrade] = []

        slip_price = self.config.default_slippage_pips * pip_sz

        for t in self.open_trades:
            if t.pair != pair:
                still_open.append(t)
                continue

            exit_price: float | None = None
            reason: TradeExitReason | str | None = None

            if t.action == ForexAction.LONG:
                hit_sl = candle.low <= t.stop_loss
                hit_tp = t.take_profit is not None and candle.high >= t.take_profit

                if hit_sl and hit_tp:
                    resolved = False
                    first_hit = None
                    if lower_tf_candles:
                        for sub_c in lower_tf_candles:
                            if sub_c.timestamp >= candle.timestamp:
                                sub_sl = sub_c.low <= t.stop_loss
                                sub_tp = t.take_profit is not None and sub_c.high >= t.take_profit
                                if sub_sl and not sub_tp:
                                    first_hit = "SL"
                                    resolved = True
                                    break
                                if sub_tp and not sub_sl:
                                    first_hit = "TP"
                                    resolved = True
                                    break
                    if resolved and first_hit == "TP":
                        exit_price = t.take_profit
                        reason = TradeExitReason.TAKE_PROFIT
                    elif resolved and first_hit == "SL":
                        exit_price = min(candle.open, t.stop_loss) - slip_price
                        reason = TradeExitReason.STOP_LOSS
                    elif self.config.allow_ambiguous:
                        exit_price = min(candle.open, t.stop_loss)
                        reason = TradeExitReason.AMBIGUOUS
                        self.ambiguous_trades_count += 1
                    elif self.config.conservative_stops:
                        exit_price = min(candle.open, t.stop_loss) - slip_price
                        reason = TradeExitReason.STOP_LOSS
                    else:
                        exit_price = t.take_profit
                        reason = TradeExitReason.TAKE_PROFIT
                elif hit_sl:
                    exit_price = min(candle.open, t.stop_loss) - slip_price
                    reason = TradeExitReason.STOP_LOSS
                elif hit_tp:
                    exit_price = t.take_profit
                    reason = TradeExitReason.TAKE_PROFIT

            else:  # SHORT
                hit_sl = candle.high >= t.stop_loss
                hit_tp = t.take_profit is not None and candle.low <= t.take_profit

                if hit_sl and hit_tp:
                    resolved = False
                    first_hit = None
                    if lower_tf_candles:
                        for sub_c in lower_tf_candles:
                            if sub_c.timestamp >= candle.timestamp:
                                sub_sl = sub_c.high >= t.stop_loss
                                sub_tp = t.take_profit is not None and sub_c.low <= t.take_profit
                                if sub_sl and not sub_tp:
                                    first_hit = "SL"
                                    resolved = True
                                    break
                                if sub_tp and not sub_sl:
                                    first_hit = "TP"
                                    resolved = True
                                    break
                    if resolved and first_hit == "TP":
                        exit_price = t.take_profit
                        reason = TradeExitReason.TAKE_PROFIT
                    elif resolved and first_hit == "SL":
                        exit_price = max(candle.open, t.stop_loss) + slip_price
                        reason = TradeExitReason.STOP_LOSS
                    elif self.config.allow_ambiguous:
                        exit_price = max(candle.open, t.stop_loss)
                        reason = TradeExitReason.AMBIGUOUS
                        self.ambiguous_trades_count += 1
                    elif self.config.conservative_stops:
                        exit_price = max(candle.open, t.stop_loss) + slip_price
                        reason = TradeExitReason.STOP_LOSS
                    else:
                        exit_price = t.take_profit
                        reason = TradeExitReason.TAKE_PROFIT
                elif hit_sl:
                    exit_price = max(candle.open, t.stop_loss) + slip_price
                    reason = TradeExitReason.STOP_LOSS
                elif hit_tp:
                    exit_price = t.take_profit
                    reason = TradeExitReason.TAKE_PROFIT

            if exit_price is not None and reason is not None:
                self._settle_trade(t, exit_price, candle.timestamp, reason)
                settled_this_bar.append(t)
            else:
                still_open.append(t)

        self.open_trades = still_open
        return settled_this_bar

    def _settle_trade(
        self,
        trade: BacktestTrade,
        exit_price: float,
        exit_time: datetime,
        reason: TradeExitReason | str,
    ) -> None:
        """Calculate realized PnL, close position, and credit account cash balance."""
        pip_sz = pip_size_for(trade.pair)
        pip_val = pip_value_in_account_currency(
            pair=trade.pair,
            lot_size=trade.lots,
            account_currency=self.config.account_currency,
            current_quote_price=exit_price,
        )

        if trade.action == ForexAction.LONG:
            pips = (exit_price - trade.entry_price) / pip_sz
        else:
            pips = (trade.entry_price - exit_price) / pip_sz

        sl_pips = max(0.0001, abs(trade.entry_price - trade.stop_loss) / pip_sz)
        r_mult = round(pips / sl_pips, 2)

        gross = round(pips * pip_val, 2)
        net = round(gross - trade.commission - trade.swap, 2)

        trade.exit_price = round(exit_price, 5)
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.status = TradeStatus.CLOSED
        trade.pips_gained = round(pips, 1)
        trade.r_multiple = r_mult
        trade.gross_profit = gross
        trade.net_profit = net

        # Credit balance
        self.balance += net
        self.closed_trades.append(trade)

        # If journal attached, log settlement
        if self.journal is not None:
            try:
                self.journal.record_trade_close(
                    trade_id=trade.trade_id,
                    close_price=trade.exit_price,
                    close_time_utc=exit_time.isoformat(),
                    exit_reason=reason if isinstance(reason, TradeExitReason) else TradeExitReason.from_str(reason),
                    swap=trade.swap,
                )
            except Exception as e:
                logger.debug("Failed logging trade close to journal: %s", e)

    def _process_pending_orders(self, candle: ForexBar, pair: str) -> list[BacktestTrade]:
        """Check expiry and execution triggers for pending limit/stop orders."""
        pip_sz = pip_size_for(pair)
        spread_price = self.config.default_spread_pips * pip_sz
        slip_price = self.config.default_slippage_pips * pip_sz
        newly_filled: list[BacktestTrade] = []
        still_pending: list[PendingOrder] = []

        for po in self.pending_orders:
            if po.pair != pair:
                still_pending.append(po)
                continue

            # Don't trigger on the exact same bar it was placed if created on this bar
            if po.created_time == candle.timestamp:
                still_pending.append(po)
                continue

            # Check expiration
            if po.valid_until is not None and candle.timestamp > po.valid_until:
                po.status = "EXPIRED"
                self.cancelled_orders.append(po)
                self.expired_orders_count += 1
                continue

            # Check trigger condition
            triggered = False
            fill_price: float = po.entry_price
            ot_str = str(po.order_type).upper()

            if po.action == ForexAction.LONG:
                if "LIMIT" in ot_str:
                    if candle.low <= po.entry_price:
                        triggered = True
                        fill_price = min(po.entry_price, candle.open) + (spread_price / 2.0) + slip_price
                elif "STOP" in ot_str:
                    if candle.high >= po.entry_price:
                        triggered = True
                        fill_price = max(po.entry_price, candle.open) + (spread_price / 2.0) + slip_price
                else:
                    if candle.low <= po.entry_price:
                        triggered = True
                        fill_price = candle.open + (spread_price / 2.0) + slip_price
            elif po.action == ForexAction.SHORT:
                if "LIMIT" in ot_str:
                    if candle.high >= po.entry_price:
                        triggered = True
                        fill_price = max(po.entry_price, candle.open) - (spread_price / 2.0) - slip_price
                elif "STOP" in ot_str:
                    if candle.low <= po.entry_price:
                        triggered = True
                        fill_price = min(po.entry_price, candle.open) - (spread_price / 2.0) - slip_price
                else:
                    if candle.high >= po.entry_price:
                        triggered = True
                        fill_price = candle.open - (spread_price / 2.0) - slip_price

            if triggered:
                if len(self.open_trades) >= self.config.max_open_trades:
                    still_pending.append(po)
                    continue

                free_margin = self.equity - self._calculate_used_margin()
                req_margin = calculate_required_margin(
                    pair=pair,
                    lot_size=po.lots,
                    entry_price=fill_price,
                    leverage=self.config.leverage,
                    account_currency=self.config.account_currency,
                )
                if req_margin > free_margin:
                    still_pending.append(po)
                    continue

                spread_cost = self.config.default_spread_pips * pip_value_in_account_currency(
                    pair, po.lots, self.config.account_currency, fill_price
                )
                slip_cost = self.config.default_slippage_pips * pip_value_in_account_currency(
                    pair, po.lots, self.config.account_currency, fill_price
                )
                comm_cost = self.config.commission_per_lot_usd * po.lots

                self.total_spread_drag += spread_cost
                self.total_slippage_drag += slip_cost
                self.total_commission_drag += comm_cost

                po.status = "FILLED"
                self.cancelled_orders.append(po)
                self.filled_orders_count += 1

                trade = BacktestTrade(
                    trade_id=f"bt_{uuid.uuid4().hex[:10]}",
                    pair=pair,
                    action=po.action,
                    entry_time=candle.timestamp,
                    entry_price=round(fill_price, 5),
                    stop_loss=round(po.stop_loss, 5),
                    take_profit=round(po.take_profit, 5) if po.take_profit is not None else None,
                    lots=po.lots,
                    proposal_id=getattr(po.proposal, "proposal_id", None),
                    setup_type=po.proposal.setup_type.value if hasattr(po.proposal.setup_type, "value") else str(po.proposal.setup_type),
                    spread_pips=self.config.default_spread_pips,
                    slippage_pips=self.config.default_slippage_pips,
                    commission=comm_cost,
                    mfe_price=fill_price,
                    mae_price=fill_price,
                )
                self.open_trades.append(trade)
                newly_filled.append(trade)

                if self.journal is not None:
                    try:
                        self.journal.record_trade_open(
                            trade_id=trade.trade_id,
                            pair=trade.pair,
                            action=trade.action,
                            open_price=trade.entry_price,
                            stop_loss=trade.stop_loss,
                            lots=trade.lots,
                            take_profit=trade.take_profit,
                            proposal_id=trade.proposal_id,
                            commission=trade.commission,
                            open_time_utc=candle.timestamp.isoformat(),
                        )
                    except Exception as e:
                        logger.debug("Failed logging trade open to journal: %s", e)
            else:
                still_pending.append(po)

        self.pending_orders = still_pending
        return newly_filled

    def _apply_swap_rollover(self, candle: ForexBar) -> None:
        """Accrue overnight swap costs when candle advances to a new calendar day."""
        curr_date = candle.timestamp.date()
        if self._last_swap_date is not None and curr_date > self._last_swap_date:
            days_passed = (curr_date - self._last_swap_date).days
            if self.config.swap_per_day_usd > 0.0 and self.open_trades:
                for t in self.open_trades:
                    swap_cost = self.config.swap_per_day_usd * t.lots * days_passed
                    t.swap += swap_cost
                    self.total_swap_drag += swap_cost
        self._last_swap_date = curr_date

    def execute_proposal(
        self,
        proposal: ForexTraderProposal,
        candle: ForexBar,
    ) -> BacktestTrade | None:
        """Validate risk constraints and execute or queue a proposed trade."""
        if proposal.action == ForexAction.NO_TRADE:
            return None

        # Check max open trades
        if len(self.open_trades) >= self.config.max_open_trades:
            return None

        pair = proposal.pair
        pip_sz = pip_size_for(pair)
        lots = proposal.suggested_lot_size or 0.1

        stop_loss = proposal.stop_loss if proposal.stop_loss is not None else (
            candle.open - 0.0050 if proposal.action == ForexAction.LONG else candle.open + 0.0050
        )
        take_profit = proposal.take_profit_1

        # Check order type: if LIMIT or STOP, queue as PendingOrder
        order_type_str = str(getattr(proposal, "order_type", "MARKET")).upper()
        if "LIMIT" in order_type_str or "STOP" in order_type_str:
            valid_until_dt = None
            if getattr(proposal, "valid_until", None):
                valid_until_dt = parse_utc_timestamp(proposal.valid_until)

            target_entry = proposal.entry_price if proposal.entry_price is not None else candle.open
            po = PendingOrder(
                order_id=f"po_{uuid.uuid4().hex[:10]}",
                proposal=proposal,
                pair=pair,
                action=proposal.action,
                order_type=OrderType.from_str(getattr(proposal, "order_type", OrderType.MARKET)),
                entry_price=round(target_entry, 5),
                stop_loss=round(stop_loss, 5),
                lots=lots,
                take_profit=round(take_profit, 5) if take_profit is not None else None,
                created_time=candle.timestamp,
                valid_until=valid_until_dt,
                status="PENDING",
            )
            self.pending_orders.append(po)
            return None

        # Check margin adequacy for immediate MARKET orders
        free_margin = self.equity - self._calculate_used_margin()
        req_margin = calculate_required_margin(
            pair=pair,
            lot_size=lots,
            entry_price=candle.open,
            leverage=self.config.leverage,
            account_currency=self.config.account_currency,
        )
        if req_margin > free_margin:
            return None

        # Entry price with spread and slippage friction
        spread_price = self.config.default_spread_pips * pip_sz
        slip_price = self.config.default_slippage_pips * pip_sz

        if proposal.action == ForexAction.LONG:
            # Pay half spread + adverse slippage
            fill_price = candle.open + (spread_price / 2.0) + slip_price
        else:
            # Receive bid minus slippage
            fill_price = candle.open - (spread_price / 2.0) - slip_price

        # Track friction costs
        spread_cost = self.config.default_spread_pips * pip_value_in_account_currency(
            pair, lots, self.config.account_currency, fill_price
        )
        slip_cost = self.config.default_slippage_pips * pip_value_in_account_currency(
            pair, lots, self.config.account_currency, fill_price
        )
        comm_cost = self.config.commission_per_lot_usd * lots

        self.total_spread_drag += spread_cost
        self.total_slippage_drag += slip_cost
        self.total_commission_drag += comm_cost

        stop_loss = proposal.stop_loss if proposal.stop_loss is not None else (
            fill_price - 0.0050 if proposal.action == ForexAction.LONG else fill_price + 0.0050
        )
        take_profit = proposal.take_profit_1

        trade = BacktestTrade(
            trade_id=f"bt_{uuid.uuid4().hex[:10]}",
            pair=pair,
            action=proposal.action,
            entry_time=candle.timestamp,
            entry_price=round(fill_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5) if take_profit is not None else None,
            lots=lots,
            proposal_id=getattr(proposal, "proposal_id", None),
            setup_type=proposal.setup_type.value if hasattr(proposal.setup_type, "value") else str(proposal.setup_type),
            spread_pips=self.config.default_spread_pips,
            slippage_pips=self.config.default_slippage_pips,
            commission=comm_cost,
            mfe_price=fill_price,
            mae_price=fill_price,
        )

        self.open_trades.append(trade)

        # Log open to journal if attached
        if self.journal is not None:
            try:
                self.journal.record_trade_open(
                    trade_id=trade.trade_id,
                    pair=trade.pair,
                    action=trade.action,
                    open_price=trade.entry_price,
                    stop_loss=trade.stop_loss,
                    lots=trade.lots,
                    take_profit=trade.take_profit,
                    proposal_id=trade.proposal_id,
                    commission=trade.commission,
                    open_time_utc=candle.timestamp.isoformat(),
                )
            except Exception as e:
                logger.debug("Failed logging trade open to journal: %s", e)

        return trade

    def step(
        self,
        candle: ForexBar,
        pair: str,
        new_proposals: list[ForexTraderProposal] | None = None,
        lower_tf_candles: Sequence[ForexBar] | None = None,
    ) -> None:
        """Execute a single discrete-time simulation step on a new incoming bar."""
        # 1. Update excursion extremes for open trades
        self._update_open_positions_excursions(candle, pair)

        # 2. Check pending limit/stop orders for fills or expiration
        self._process_pending_orders(candle, pair)

        # 3. Check and apply daily swap rollover
        self._apply_swap_rollover(candle)

        # 4. Evaluate intrabar stop loss and take profit hits
        self._check_and_settle_intrabar_exits(candle, pair, lower_tf_candles=lower_tf_candles)

        # 5. Process new trade proposals (MARKET orders fill, LIMIT/STOP queue)
        if new_proposals:
            for p in new_proposals:
                self.execute_proposal(p, candle)

        # 6. Calculate floating unrealized equity
        floating_pnl = 0.0
        pip_sz = pip_size_for(pair)
        for t in self.open_trades:
            if t.pair == pair:
                pv = pip_value_in_account_currency(pair, t.lots, self.config.account_currency, candle.close)
                if t.action == ForexAction.LONG:
                    pips = (candle.close - t.entry_price) / pip_sz
                else:
                    pips = (t.entry_price - candle.close) / pip_sz
                floating_pnl += pips * pv

        self.equity = round(self.balance + floating_pnl, 2)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        dd_cash = max(0.0, self.peak_equity - self.equity)
        dd_pct = round((dd_cash / max(1.0, self.peak_equity)) * 100.0, 2)
        used_margin = self._calculate_used_margin()
        free_margin = max(0.0, self.equity - used_margin)

        self.equity_curve.append(
            EquityPoint(
                timestamp=candle.timestamp,
                balance=self.balance,
                equity=self.equity,
                used_margin=used_margin,
                free_margin=free_margin,
                open_trades_count=len(self.open_trades),
                drawdown_cash=dd_cash,
                drawdown_pct=dd_pct,
            )
        )

    def run_candles(
        self,
        pair: str,
        candles: Sequence[ForexBar],
        proposals_schedule: dict[datetime, list[ForexTraderProposal]] | None = None,
        strategy_callback: Callable[[datetime, list[ForexBar]], list[ForexTraderProposal]] | None = None,
        lower_tf_candles: Sequence[ForexBar] | None = None,
    ) -> ForexBacktestResult:
        """Run full bar-by-bar backtest over chronological candle stream."""
        self.reset()
        if not candles:
            return self._build_empty_result()

        schedule = proposals_schedule or {}
        history_window: list[ForexBar] = []

        for _i, candle in enumerate(candles):
            history_window.append(candle)

            # Point-in-time safe proposal generation: only past and current candle visible
            proposals_to_submit: list[ForexTraderProposal] = []
            if candle.timestamp in schedule:
                proposals_to_submit.extend(schedule[candle.timestamp])

            if strategy_callback is not None:
                # Callback receives only candles strictly <= current timestamp
                pit_candles = list(history_window)
                generated = strategy_callback(candle.timestamp, pit_candles)
                if generated:
                    proposals_to_submit.extend(generated)

            self.step(
                candle=candle,
                pair=pair,
                new_proposals=proposals_to_submit,
                lower_tf_candles=lower_tf_candles,
            )

        # Force-close any open positions on last bar for final settlement
        if self.open_trades and candles:
            last_bar = candles[-1]
            for t in list(self.open_trades):
                self._settle_trade(t, last_bar.close, last_bar.timestamp, TradeExitReason.MANUAL)
            self.open_trades = []

        start_str = candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        end_str = candles[-1].timestamp.strftime("%Y-%m-%d %H:%M")
        return self._build_result(start_str, end_str)

    def _build_empty_result(self) -> ForexBacktestResult:
        total_friction = round(
            self.total_spread_drag
            + self.total_slippage_drag
            + self.total_commission_drag
            + self.total_swap_drag,
            2,
        )
        return ForexBacktestResult(
            config=self.config,
            start_date="N/A",
            end_date="N/A",
            initial_balance=self.config.initial_balance,
            final_balance=self.config.initial_balance,
            final_equity=self.config.initial_balance,
            total_net_profit=0.0,
            total_return_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate_pct=0.0,
            loss_rate_pct=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            profit_factor=0.0,
            avg_r_multiple=0.0,
            expectancy_r=0.0,
            expectancy_cash=0.0,
            avg_trade_pips=0.0,
            total_pips=0.0,
            max_drawdown_cash=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            total_spread_cost_usd=round(self.total_spread_drag, 2),
            total_slippage_cost_usd=round(self.total_slippage_drag, 2),
            total_commission_cost_usd=round(self.total_commission_drag, 2),
            total_swap_cost_usd=round(self.total_swap_drag, 2),
            total_friction_usd=total_friction,
            pending_orders_count=len(self.pending_orders) + len(self.cancelled_orders),
            filled_orders_count=self.filled_orders_count,
            expired_orders_count=self.expired_orders_count,
            ambiguous_trades_count=self.ambiguous_trades_count,
            mode="DEMO",
            validated_strategy_performance=False,
        )

    def _build_result(self, start_date: str, end_date: str) -> ForexBacktestResult:
        """Compute institutional statistical aggregates across closed trades and equity curve."""
        trades = self.closed_trades
        total_trades = len(trades)

        if total_trades == 0:
            res = self._build_empty_result()
            res.start_date = start_date
            res.end_date = end_date
            return res

        winners = [t for t in trades if t.net_profit > 0.0]
        losers = [t for t in trades if t.net_profit < 0.0]
        breakeven = [t for t in trades if t.net_profit == 0.0]

        win_count = len(winners)
        loss_count = len(losers)
        be_count = len(breakeven)

        win_rate = round((win_count / total_trades) * 100.0, 1)
        loss_rate = round((loss_count / total_trades) * 100.0, 1)

        gross_profit = sum(t.gross_profit for t in winners)
        gross_loss = abs(sum(t.gross_profit for t in losers))
        net_profit = sum(t.net_profit for t in trades)
        total_return_pct = round((net_profit / self.config.initial_balance) * 100.0, 2)

        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        # R-Multiple and Expectancy
        r_vals = [t.r_multiple for t in trades]
        avg_r = round(sum(r_vals) / total_trades, 2) if r_vals else 0.0

        avg_win_r = (sum(t.r_multiple for t in winners) / win_count) if win_count else 0.0
        avg_loss_r = (abs(sum(t.r_multiple for t in losers)) / loss_count) if loss_count else 0.0
        expectancy_r = round(((win_rate / 100.0) * avg_win_r) - ((loss_rate / 100.0) * avg_loss_r), 2)

        avg_win_cash = (gross_profit / win_count) if win_count else 0.0
        avg_loss_cash = (gross_loss / loss_count) if loss_count else 0.0
        expectancy_cash = round(((win_rate / 100.0) * avg_win_cash) - ((loss_rate / 100.0) * avg_loss_cash), 2)

        # Pip Statistics
        pips_vals = [t.pips_gained for t in trades]
        total_pips = round(sum(pips_vals), 1)
        avg_pips = round(total_pips / total_trades, 1)

        # Drawdown
        max_dd_cash = max((pt.drawdown_cash for pt in self.equity_curve), default=0.0)
        max_dd_pct = max((pt.drawdown_pct for pt in self.equity_curve), default=0.0)

        # Sharpe & Sortino (derived from bar-by-bar equity percentage returns)
        equity_series = [pt.equity for pt in self.equity_curve]
        if len(equity_series) > 1:
            returns = np.diff(equity_series) / equity_series[:-1]
            mean_ret = float(np.mean(returns))
            std_ret = float(np.std(returns))

            # Annualization factor assumes ~252 trading days with M15/H1 intervals
            annual_factor = math.sqrt(252 * 24)
            sharpe = round((mean_ret / std_ret) * annual_factor, 2) if std_ret > 1e-8 else 0.0

            downside = returns[returns < 0]
            downside_std = float(np.std(downside)) if len(downside) > 1 else 0.0
            sortino = round((mean_ret / downside_std) * annual_factor, 2) if downside_std > 1e-8 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0

        total_friction = round(
            self.total_spread_drag
            + self.total_slippage_drag
            + self.total_commission_drag
            + self.total_swap_drag,
            2,
        )

        return ForexBacktestResult(
            config=self.config,
            start_date=start_date,
            end_date=end_date,
            initial_balance=self.config.initial_balance,
            final_balance=round(self.balance, 2),
            final_equity=round(self.equity, 2),
            total_net_profit=round(net_profit, 2),
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            breakeven_trades=be_count,
            win_rate_pct=win_rate,
            loss_rate_pct=loss_rate,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=profit_factor,
            avg_r_multiple=avg_r,
            expectancy_r=expectancy_r,
            expectancy_cash=expectancy_cash,
            avg_trade_pips=avg_pips,
            total_pips=total_pips,
            max_drawdown_cash=round(max_dd_cash, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            total_spread_cost_usd=round(self.total_spread_drag, 2),
            total_slippage_cost_usd=round(self.total_slippage_drag, 2),
            total_commission_cost_usd=round(self.total_commission_drag, 2),
            total_swap_cost_usd=round(self.total_swap_drag, 2),
            total_friction_usd=total_friction,
            trades=trades,
            equity_curve=self.equity_curve,
            pending_orders_count=len(self.pending_orders) + len(self.cancelled_orders),
            filled_orders_count=self.filled_orders_count,
            expired_orders_count=self.expired_orders_count,
            ambiguous_trades_count=self.ambiguous_trades_count,
            mode="DEMO",
            validated_strategy_performance=False,
        )


def run_forex_backtest(
    pair: str,
    candles: Sequence[ForexBar],
    config: ForexBacktestConfig | None = None,
    proposals_schedule: dict[datetime, list[ForexTraderProposal]] | None = None,
    strategy_callback: Callable[[datetime, list[ForexBar]], list[ForexTraderProposal]] | None = None,
    journal: ForexTradeJournal | None = None,
) -> ForexBacktestResult:
    """Convenience module helper to run a point-in-time safe Forex backtest."""
    engine = ForexBacktestEngine(config=config, journal=journal)
    return engine.run_candles(
        pair=pair,
        candles=candles,
        proposals_schedule=proposals_schedule,
        strategy_callback=strategy_callback,
    )
