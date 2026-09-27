"""Institutional Post-Trade Analytics & Performance Reporting (Phase 15).

Calculates comprehensive metrics across settled trades:
- Win/Loss rates, Profit Factor, Expectancy (R-multiple & cash).
- Peak-to-trough Maximum Drawdown ($ and %).
- Categorical distribution breakdowns by Pair, Setup Type, Exit Reason, and Session.
- Institutional Markdown Dashboard formatting.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.models import TradeJournalRecord, TradeStatus
from tradingagents.journal.models import (
    PairMetrics,
    PerformanceReport,
    SetupMetrics,
)

logger = logging.getLogger(__name__)


class PostTradeAnalytics:
    """Computes comprehensive quantitative performance analytics across trading history."""

    @staticmethod
    def compute_performance_report(
        trades: Sequence[TradeJournalRecord],
        initial_capital: float = 100000.0,
    ) -> PerformanceReport:
        """Compute complete institutional trading performance report."""
        closed = [t for t in trades if t.status == TradeStatus.CLOSED]
        open_count = sum(1 for t in trades if t.status == TradeStatus.OPEN)

        if not closed:
            return PerformanceReport(
                total_trades=len(trades),
                open_trades=open_count,
                closed_trades=0,
            )

        winners = [t for t in closed if (t.net_profit or 0.0) > 0.0]
        losers = [t for t in closed if (t.net_profit or 0.0) < 0.0]
        breakeven = [t for t in closed if (t.net_profit or 0.0) == 0.0]

        closed_count = len(closed)
        win_count = len(winners)
        loss_count = len(losers)
        be_count = len(breakeven)

        win_rate = round((win_count / closed_count) * 100.0, 1)
        loss_rate = round((loss_count / closed_count) * 100.0, 1)

        gross_profit = sum(t.net_profit for t in winners if t.net_profit is not None)
        gross_loss = abs(sum(t.net_profit for t in losers if t.net_profit is not None))
        total_net_profit = round(gross_profit - gross_loss, 2)

        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0

        # R-Multiple & Expectancy
        r_vals = [t.r_multiple for t in closed if t.r_multiple is not None]
        avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else 0.0
        max_win_r = max(r_vals) if r_vals else 0.0
        max_loss_r = min(r_vals) if r_vals else 0.0

        win_r_vals = [t.r_multiple for t in winners if t.r_multiple is not None]
        loss_r_vals = [abs(t.r_multiple) for t in losers if t.r_multiple is not None]
        avg_win_r = (sum(win_r_vals) / len(win_r_vals)) if win_r_vals else 0.0
        avg_loss_r = (sum(loss_r_vals) / len(loss_r_vals)) if loss_r_vals else 0.0
        expectancy_r = round(
            ((win_rate / 100.0) * avg_win_r) - ((loss_rate / 100.0) * avg_loss_r), 2
        )

        avg_win_cash = (gross_profit / win_count) if win_count > 0 else 0.0
        avg_loss_cash = (gross_loss / loss_count) if loss_count > 0 else 0.0
        expectancy_cash = round(
            ((win_rate / 100.0) * avg_win_cash) - ((loss_rate / 100.0) * avg_loss_cash), 2
        )

        # Pip Statistics
        pips = [t.pips_gained for t in closed if t.pips_gained is not None]
        total_pips = round(sum(pips), 1)
        avg_pips = round(total_pips / closed_count, 1) if closed_count > 0 else 0.0
        win_pips = [t.pips_gained for t in winners if t.pips_gained is not None]
        loss_pips = [t.pips_gained for t in losers if t.pips_gained is not None]
        win_avg_pips = round(sum(win_pips) / len(win_pips), 1) if win_pips else 0.0
        loss_avg_pips = round(sum(loss_pips) / len(loss_pips), 1) if loss_pips else 0.0

        # Maximum Drawdown
        current_equity = initial_capital
        peak_equity = initial_capital
        max_dd_cash = 0.0
        max_dd_pct = 0.0

        # Sort chronologically by close time
        sorted_closed = sorted(
            closed, key=lambda t: t.close_time_utc or t.open_time_utc
        )
        for t in sorted_closed:
            pnl = t.net_profit or 0.0
            current_equity += pnl
            if current_equity > peak_equity:
                peak_equity = current_equity
            dd_cash = peak_equity - current_equity
            dd_pct = (dd_cash / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if dd_cash > max_dd_cash:
                max_dd_cash = dd_cash
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        # Directional Win Rates
        longs = [t for t in closed if t.action == ForexAction.LONG]
        shorts = [t for t in closed if t.action == ForexAction.SHORT]
        long_win_rate = (
            round(sum(1 for t in longs if (t.net_profit or 0.0) > 0) / len(longs) * 100.0, 1)
            if longs
            else 0.0
        )
        short_win_rate = (
            round(sum(1 for t in shorts if (t.net_profit or 0.0) > 0) / len(shorts) * 100.0, 1)
            if shorts
            else 0.0
        )

        # Breakdown by Pair
        by_pair: dict[str, PairMetrics] = {}
        pairs = {t.pair for t in closed}
        for p in sorted(pairs):
            pair_trades = [t for t in closed if t.pair == p]
            p_wins = sum(1 for t in pair_trades if (t.net_profit or 0.0) > 0)
            p_loss = sum(1 for t in pair_trades if (t.net_profit or 0.0) < 0)
            p_net = round(sum(t.net_profit or 0.0 for t in pair_trades), 2)
            p_g_win = sum(t.net_profit or 0.0 for t in pair_trades if (t.net_profit or 0.0) > 0)
            p_g_loss = abs(sum(t.net_profit or 0.0 for t in pair_trades if (t.net_profit or 0.0) < 0))
            p_pf = (
                round(p_g_win / p_g_loss, 2)
                if p_g_loss > 0
                else (999.0 if p_g_win > 0 else 0.0)
            )
            p_pips = round(sum(t.pips_gained or 0.0 for t in pair_trades), 1)
            p_avg_pips = round(p_pips / len(pair_trades), 1)

            by_pair[p] = PairMetrics(
                pair=p,
                total_trades=len(pair_trades),
                winning_trades=p_wins,
                losing_trades=p_loss,
                win_rate=round(p_wins / len(pair_trades) * 100.0, 1),
                net_profit=p_net,
                profit_factor=p_pf,
                total_pips=p_pips,
                avg_pips=p_avg_pips,
            )

        # Breakdown by Exit Reason
        by_exit: dict[str, int] = {}
        for t in closed:
            r_str = t.exit_reason.value if t.exit_reason else "MANUAL"
            by_exit[r_str] = by_exit.get(r_str, 0) + 1

        # Breakdown by Setup Type (from metadata if available)
        by_setup: dict[str, SetupMetrics] = {}
        setups = {t.metadata.get("setup_type", "UNKNOWN") for t in closed}
        for s in sorted(setups):
            s_trades = [t for t in closed if t.metadata.get("setup_type", "UNKNOWN") == s]
            s_wins = sum(1 for t in s_trades if (t.net_profit or 0.0) > 0)
            s_loss = sum(1 for t in s_trades if (t.net_profit or 0.0) < 0)
            s_net = round(sum(t.net_profit or 0.0 for t in s_trades), 2)
            s_gw = sum(t.net_profit or 0.0 for t in s_trades if (t.net_profit or 0.0) > 0)
            s_gl = abs(sum(t.net_profit or 0.0 for t in s_trades if (t.net_profit or 0.0) < 0))
            s_pf = round(s_gw / s_gl, 2) if s_gl > 0 else (999.0 if s_gw > 0 else 0.0)

            by_setup[s] = SetupMetrics(
                setup_type=s,
                total_trades=len(s_trades),
                winning_trades=s_wins,
                losing_trades=s_loss,
                win_rate=round(s_wins / len(s_trades) * 100.0, 1) if s_trades else 0.0,
                net_profit=s_net,
                profit_factor=s_pf,
            )

        return PerformanceReport(
            total_trades=len(trades),
            open_trades=open_count,
            closed_trades=closed_count,
            winning_trades=win_count,
            losing_trades=loss_count,
            breakeven_trades=be_count,
            win_rate=win_rate,
            loss_rate=loss_rate,
            total_net_profit=total_net_profit,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=profit_factor,
            expectancy_cash=expectancy_cash,
            expectancy_r=expectancy_r,
            avg_r_multiple=avg_r,
            max_win_r=max_win_r,
            max_loss_r=max_loss_r,
            total_pips_gained=total_pips,
            avg_pips_per_trade=avg_pips,
            win_avg_pips=win_avg_pips,
            loss_avg_pips=loss_avg_pips,
            max_drawdown_cash=round(max_dd_cash, 2),
            max_drawdown_percent=round(max_dd_pct, 2),
            long_win_rate=long_win_rate,
            short_win_rate=short_win_rate,
            by_pair=by_pair,
            by_setup=by_setup,
            by_exit_reason=by_exit,
        )

    @classmethod
    def render_markdown_dashboard(cls, report: PerformanceReport) -> str:
        """Render a formatted, high-impact institutional performance dashboard."""
        lines = [
            "# 📊 Quantitative Forex Journal: Performance Dashboard",
            f"*Generated at: {report.generated_at_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}*",
            "",
            "## 1. Executive Summary & Key Performance Indicators",
            "",
            "| Metric | Value | Benchmark / Target |",
            "| :--- | :--- | :--- |",
            f"| **Total Closed Trades** | `{report.closed_trades}` (Open: `{report.open_trades}`) | — |",
            f"| **Win Rate** | **`{report.win_rate}%`** ({report.winning_trades}W / {report.losing_trades}L / {report.breakeven_trades}BE) | $\\ge 50.0\\%$ |",
            f"| **Profit Factor** | **`{report.profit_factor}`** | $\\ge 1.75$ |",
            f"| **Total Net Profit** | **`${report.total_net_profit:+,.2f}`** | Positive |",
            f"| **Expectancy (R)** | **`{report.expectancy_r:+.2f}R`** per trade | $\\ge +0.35R$ |",
            f"| **Expectancy ($)** | `${report.expectancy_cash:+,.2f}` | Positive |",
            f"| **Average R-Multiple** | `{report.avg_r_multiple:+.2f}R` (Max: `+{report.max_win_r:.1f}R`, Min: `{report.max_loss_r:.1f}R`) | $\\ge +0.50R$ |",
            f"| **Total Pips Gained** | **`{report.total_pips_gained:+.1f} pips`** (Avg: `{report.avg_pips_per_trade:+.1f}`) | Positive |",
            f"| **Win / Loss Pip Ratio** | `+{report.win_avg_pips:.1f}` vs `{report.loss_avg_pips:.1f}` pips | $\\ge 1.5\\text{{x}}$ |",
            f"| **Max Drawdown** | **`${report.max_drawdown_cash:,.2f}`** (`{report.max_drawdown_percent:.1f}%`) | $\\le 6.0\\%$ |",
            f"| **Directional Win Rates** | Long: `{report.long_win_rate}%` / Short: `{report.short_win_rate}%` | Balanced |",
            "",
        ]

        # Pair breakdown table
        if report.by_pair:
            lines.extend([
                "## 2. Currency Pair Performance Breakdown",
                "",
                "| Pair | Trades | Win Rate | Net Profit | Profit Factor | Total Pips | Avg Pips |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ])
            for _p, m in report.by_pair.items():
                pf_str = f"{m.profit_factor:.2f}" if m.profit_factor < 999.0 else "∞"
                lines.append(
                    f"| **{m.pair}** | {m.total_trades} | {m.win_rate:.1f}% | `${m.net_profit:+,.2f}` | `{pf_str}` | `{m.total_pips:+.1f}` | `{m.avg_pips:+.1f}` |"
                )
            lines.append("")

        # Exit reasons breakdown
        if report.by_exit_reason:
            lines.extend([
                "## 3. Exit Reason Distribution",
                "",
                "| Exit Reason | Count | % of Closed Trades |",
                "| :--- | :--- | :--- |",
            ])
            for reason, count in sorted(report.by_exit_reason.items(), key=lambda x: x[1], reverse=True):
                pct = round(count / report.closed_trades * 100.0, 1) if report.closed_trades > 0 else 0.0
                lines.append(f"| `{reason}` | {count} | {pct:.1f}% |")
            lines.append("")

        return "\n".join(lines)
