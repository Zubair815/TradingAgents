"""Data models for Institutional Forex Analytics, Calibration & Ablation (Phase 19).

Provides typed representations for:
- MonteCarloConfig & MonteCarloResult: Bootstrap resampling, ruin probabilities, drawdown percentiles.
- DeepMetrics & DrawdownEpisode: Calmar, Ulcer Index, SQN, Recovery Factor, Gain-to-Pain, underwater series.
- StopTargetCalibration: Optimal stop/target calibration from MFE/MAE excursions and ATR multipliers.
- AblationVariantResult & AblationStudyResult: Multi-agent and signal ablation delta metrics.
- ExecutiveAnalyticsDashboard: Consolidated analytics report with Markdown rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field as PydanticField

# ---------------------------------------------------------------------------
# 1. Monte Carlo Simulation Models
# ---------------------------------------------------------------------------


class MonteCarloConfig(BaseModel):
    """Configuration parameters for Monte Carlo resampling stress tests."""

    trials: int = PydanticField(default=1000, ge=10, le=100000)
    confidence_level: float = PydanticField(default=0.95, ge=0.5, le=0.999)
    ruin_thresholds: list[float] = PydanticField(default=[10.0, 20.0, 30.0, 50.0])
    sample_size: int | None = PydanticField(default=None)
    random_seed: int | None = PydanticField(default=42)


class DrawdownPercentiles(BaseModel):
    """Quantile distribution of maximum drawdowns from Monte Carlo simulation."""

    p5: float = 0.0
    p25: float = 0.0
    p50: float = 0.0  # Median
    p75: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    worst_case: float = 0.0


class TerminalWealthPercentiles(BaseModel):
    """Quantile distribution of terminal portfolio wealth."""

    p5: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p95: float = 0.0


class MonteCarloResult(BaseModel):
    """Results from Monte Carlo bootstrap resampling stress simulation."""

    trials: int
    initial_capital: float
    ruin_probabilities: dict[str, float] = PydanticField(default_factory=dict)
    drawdown_percentiles: DrawdownPercentiles = PydanticField(default_factory=DrawdownPercentiles)
    terminal_wealth_percentiles: TerminalWealthPercentiles = PydanticField(default_factory=TerminalWealthPercentiles)
    profit_confidence_interval: tuple[float, float] = (0.0, 0.0)
    sharpe_confidence_interval: tuple[float, float] = (0.0, 0.0)
    median_terminal_equity: float = 0.0
    profitable_trials_pct: float = 0.0


# ---------------------------------------------------------------------------
# 2. Deep Metrics & Drawdown Episode Models
# ---------------------------------------------------------------------------


class DrawdownEpisode(BaseModel):
    """Representation of an individual peak-to-recovery drawdown episode."""

    episode_id: int
    peak_index: int
    trough_index: int
    recovery_index: int | None = None
    peak_equity: float
    trough_equity: float
    drawdown_cash: float
    drawdown_pct: float
    duration_bars: int
    recovery_bars: int | None = None
    is_recovered: bool = False


class SQNRating(str, Enum):
    """Van Tharp System Quality Number classification."""

    POOR = "POOR"  # < 1.6
    AVERAGE = "AVERAGE"  # 1.6 - 2.0
    GOOD = "GOOD"  # 2.0 - 2.5
    EXCELLENT = "EXCELLENT"  # 2.5 - 3.0
    SUPERB = "SUPERB"  # 3.0 - 5.0
    HOLY_GRAIL = "HOLY_GRAIL"  # > 5.0

    @classmethod
    def from_sqn(cls, score: float) -> SQNRating:
        if score < 1.6:
            return cls.POOR
        if score < 2.0:
            return cls.AVERAGE
        if score < 2.5:
            return cls.GOOD
        if score < 3.0:
            return cls.EXCELLENT
        if score < 5.0:
            return cls.SUPERB
        return cls.HOLY_GRAIL


class DeepMetrics(BaseModel):
    """Deep quantitative risk-adjusted performance indicators."""

    calmar_ratio: float = 0.0
    ulcer_index: float = 0.0
    ulcer_performance_index: float = 0.0
    system_quality_number: float = 0.0
    sqn_rating: SQNRating = SQNRating.POOR
    recovery_factor: float = 0.0
    gain_to_pain_ratio: float = 0.0
    longest_drawdown_bars: int = 0
    average_drawdown_bars: float = 0.0
    underwater_episodes: list[DrawdownEpisode] = PydanticField(default_factory=list)


# ---------------------------------------------------------------------------
# 3. Risk & Stop Calibration Models
# ---------------------------------------------------------------------------


class StopTargetCalibration(BaseModel):
    """Empirical calibration for Stop Loss and Take Profit levels."""

    pair: str = "GLOBAL"
    optimal_sl_pips: float = 0.0
    optimal_sl_r: float = 1.0
    optimal_tp_pips: float = 0.0
    optimal_tp_r: float = 2.0
    mae_survival_rates: dict[float, float] = PydanticField(default_factory=dict)
    mfe_crest_probabilities: dict[float, float] = PydanticField(default_factory=dict)
    expectancy_by_tp_r: dict[float, float] = PydanticField(default_factory=dict)
    recommended_atr_sl_multiple: float = 1.5
    recommended_atr_tp_multiple: float = 2.5
    expected_gain_improvement_pct: float = 0.0
    recommendations: list[str] = PydanticField(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Multi-Agent & Signal Ablation Models
# ---------------------------------------------------------------------------


class AblationVariantResult(BaseModel):
    """Performance evaluation of a specific system configuration or ablated variant."""

    variant_name: str
    description: str
    total_trades: int
    win_rate_pct: float
    total_net_profit: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    delta_net_profit: float = 0.0
    delta_sharpe: float = 0.0
    delta_max_drawdown_pct: float = 0.0
    delta_win_rate_pct: float = 0.0


class AblationStudyResult(BaseModel):
    """Comprehensive comparative ablation study matrix across system components."""

    baseline_name: str = "Full System (All Analysts + Risk Engine)"
    baseline: AblationVariantResult
    variants: list[AblationVariantResult] = PydanticField(default_factory=list)
    component_importance_ranking: list[tuple[str, float]] = PydanticField(default_factory=list)
    key_findings: list[str] = PydanticField(default_factory=list)


# ---------------------------------------------------------------------------
# 5. Consolidated Executive Dashboard Model
# ---------------------------------------------------------------------------


class ExecutiveAnalyticsDashboard(BaseModel):
    """Consolidated institutional performance, calibration, and ablation dashboard."""

    period_start: str = "N/A"
    period_end: str = "N/A"
    initial_capital: float = 100000.0
    final_equity: float = 100000.0
    total_net_profit: float = 0.0
    total_return_pct: float = 0.0
    total_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0

    deep_metrics: DeepMetrics = PydanticField(default_factory=DeepMetrics)
    monte_carlo: MonteCarloResult | None = None
    calibration: StopTargetCalibration | None = None
    ablation: AblationStudyResult | None = None
    generated_at_utc: datetime = PydanticField(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def render_markdown_dashboard(self) -> str:
        """Render complete institutional markdown analytics dashboard."""
        lines: list[str] = [
            "# Institutional Forex Quantitative Analytics & Performance Dashboard",
            f"*Period: {self.period_start} to {self.period_end} | Initial Capital: ${self.initial_capital:,.2f} | Generated: {self.generated_at_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}*",
            "",
            "## 1. Executive Performance Summary",
            "",
            "| Performance Metric | System Value | Institutional Benchmark | Rating |",
            "|:---|:---:|:---:|:---|",
            f"| **Final Equity** | `${self.final_equity:,.2f}` | — | — |",
            f"| **Total Net Profit** | `${self.total_net_profit:+,.2f}` (`{self.total_return_pct:+.2f}%`) | `> +15.0%` | {'OUTPERFORM' if self.total_return_pct >= 15 else 'STABLE' if self.total_return_pct > 0 else 'DEFICIT'} |",
            f"| **Win Rate** | `{self.win_rate_pct:.1f}%` (n={self.total_trades}) | `> 55.0%` | {'SOLID' if self.win_rate_pct >= 55 else 'DISCIPLINED' if self.win_rate_pct >= 45 else 'LOW'} |",
            f"| **Profit Factor** | `{self.profit_factor:.2f}` | `> 1.60` | {'TIER-1' if self.profit_factor >= 1.6 else 'ACCEPTABLE' if self.profit_factor >= 1.2 else 'POOR'} |",
            f"| **Sharpe Ratio** | `{self.sharpe_ratio:.2f}` | `> 1.50` | {'EXCELLENT' if self.sharpe_ratio >= 1.5 else 'AVERAGE' if self.sharpe_ratio >= 1.0 else 'SUB-PAR'} |",
            f"| **Maximum Drawdown** | `{self.max_drawdown_pct:.2f}%` | `< 10.0%` | {'CONTAINED' if self.max_drawdown_pct <= 10 else 'ELEVATED'} |",
            "",
            "## 2. Advanced Risk & Downside Metrics",
            "",
            "| Metric | Value | Interpretation |",
            "|:---|:---:|:---|",
            f"| **System Quality Number (SQN)** | `{self.deep_metrics.system_quality_number:.2f}` | **{self.deep_metrics.sqn_rating.value}** edge score |",
            f"| **Calmar Ratio** | `{self.deep_metrics.calmar_ratio:.2f}` | Annual return to max drawdown ratio |",
            f"| **Ulcer Index (UI)** | `{self.deep_metrics.ulcer_index:.2f}` | Volatility of drawdowns depth & duration |",
            f"| **Ulcer Performance Index (Martin)** | `{self.deep_metrics.ulcer_performance_index:.2f}` | Excess return per unit of drawdown stress |",
            f"| **Recovery Factor** | `{self.deep_metrics.recovery_factor:.2f}` | Net profit divided by maximum drawdown cash |",
            f"| **Gain-to-Pain Ratio** | `{self.deep_metrics.gain_to_pain_ratio:.2f}` | Cumulative net returns to cumulative losses |",
            f"| **Longest Drawdown Duration** | `{self.deep_metrics.longest_drawdown_bars}` bars | Longest bars spent under previous equity peak |",
            f"| **Average Drawdown Duration** | `{self.deep_metrics.average_drawdown_bars:.1f}` bars | Average duration per drawdown episode |",
            "",
        ]

        if self.monte_carlo:
            lines.extend([
                "## 3. Monte Carlo Simulation & Risk of Ruin",
                f"*Bootstrap resampling across {self.monte_carlo.trials:,} iterations*",
                "",
                "| Drawdown Threshold | Probability of Ruin | Stress Assessment |",
                "|:---:|:---:|:---|",
            ])
            for th_label, prob in self.monte_carlo.ruin_probabilities.items():
                stress = "NEGLIGIBLE" if prob < 0.01 else "LOW RISK" if prob < 0.05 else "MODERATE" if prob < 0.15 else "HIGH RISK"
                lines.append(f"| **{th_label} Drawdown** | `{prob * 100.0:.2f}%` | {stress} |")

            lines.extend([
                "",
                "| Drawdown Quantile | Simulated Max Drawdown % |",
                "|:---|:---:|",
                f"| **5th Percentile (Best-Case)** | `{self.monte_carlo.drawdown_percentiles.p5:.2f}%` |",
                f"| **50th Percentile (Median)** | `{self.monte_carlo.drawdown_percentiles.p50:.2f}%` |",
                f"| **95th Percentile (Stress Value-at-Risk)** | `{self.monte_carlo.drawdown_percentiles.p95:.2f}%` |",
                f"| **99th Percentile (Tail Risk)** | `{self.monte_carlo.drawdown_percentiles.p99:.2f}%` |",
                f"| **Worst-Case Simulation** | `{self.monte_carlo.drawdown_percentiles.worst_case:.2f}%` |",
                "",
                f"**95% Confidence Interval for Net Profit:** `${self.monte_carlo.profit_confidence_interval[0]:+,.2f}` to `${self.monte_carlo.profit_confidence_interval[1]:+,.2f}`  ",
                f"**95% Confidence Interval for Sharpe Ratio:** `{self.monte_carlo.sharpe_confidence_interval[0]:.2f}` to `{self.monte_carlo.sharpe_confidence_interval[1]:.2f}`  ",
                f"**Profitable Simulation Runs:** `{self.monte_carlo.profitable_trials_pct:.1f}%`",
                "",
            ])

        if self.calibration:
            lines.extend([
                "## 4. Empirical Stop Loss & Profit Target Calibration",
                f"*Based on MFE/MAE excursion distribution for {self.calibration.pair}*",
                "",
                "| Calibration Parameter | Optimal Value | Recommendation Details |",
                "|:---|:---:|:---|",
                f"| **Optimal Stop Loss (Pips)** | `{self.calibration.optimal_sl_pips:.1f}` pips | Maximizes winner survival vs loss mitigation |",
                f"| **Optimal Take Profit (R)** | `{self.calibration.optimal_tp_r:.1f}R` | Mathematical crest peak maximizing expectancy |",
                f"| **Recommended ATR Multiple (SL)** | `{self.calibration.recommended_atr_sl_multiple:.1f}x ATR` | Dynamic volatility-based stop padding |",
                f"| **Recommended ATR Multiple (TP)** | `{self.calibration.recommended_atr_tp_multiple:.1f}x ATR` | Dynamic target objective |",
                f"| **Expected Performance Lift** | `+{self.calibration.expected_gain_improvement_pct:.1f}%` | Gain from adopting optimal geometry |",
                "",
            ])
            if self.calibration.recommendations:
                lines.append("**Actionable Directives:**")
                for r in self.calibration.recommendations:
                    lines.append(f"- {r}")
                lines.append("")

        if self.ablation:
            lines.extend([
                "## 5. Multi-Agent & Signal Ablation Study",
                f"*Baseline: {self.ablation.baseline_name}*",
                "",
                "| System Variant | Net Profit | Sharpe | Max DD % | Win Rate | Δ Profit | Δ Sharpe | Δ Drawdown |",
                "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
                f"| **{self.ablation.baseline.variant_name}** | `${self.ablation.baseline.total_net_profit:+,.2f}` | `{self.ablation.baseline.sharpe_ratio:.2f}` | `{self.ablation.baseline.max_drawdown_pct:.2f}%` | `{self.ablation.baseline.win_rate_pct:.1f}%` | Baseline | Baseline | Baseline |",
            ])
            for v in self.ablation.variants:
                d_profit_str = f"${v.delta_net_profit:+,.2f}"
                d_sharpe_str = f"{v.delta_sharpe:+.2f}"
                d_dd_str = f"{v.delta_max_drawdown_pct:+.2f}%"
                lines.append(
                    f"| {v.variant_name} | `${v.total_net_profit:+,.2f}` | `{v.sharpe_ratio:.2f}` | `{v.max_drawdown_pct:.2f}%` | `{v.win_rate_pct:.1f}%` | `{d_profit_str}` | `{d_sharpe_str}` | `{d_dd_str}` |"
                )

            if self.ablation.component_importance_ranking:
                lines.extend([
                    "",
                    "**Component Importance Ranking (Contribution to Sharpe / Risk Reduction):**",
                ])
                for idx, (comp, score) in enumerate(self.ablation.component_importance_ranking, start=1):
                    lines.append(f"{idx}. **{comp}**: Importance Score `{score:.2f}`")

            if self.ablation.key_findings:
                lines.extend([
                    "",
                    "**Ablation Study Key Insights:**",
                ])
                for k in self.ablation.key_findings:
                    lines.append(f"- {k}")
                lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert dashboard to dictionary for API endpoints."""
        return self.model_dump()
