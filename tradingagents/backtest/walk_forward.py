"""Institutional Walk-Forward and Out-of-Sample Validation Engine (Phase 21).

Provides:
- Strict separation of evaluation periods:
    * DEVELOPMENT: In-sample strategy hypothesis and historical baseline.
    * VALIDATION: In-sample parameter tuning and calibration.
    * OUT_OF_SAMPLE: Unseen historical test segment strictly isolated from optimization.
    * FORWARD_DEMO: Forward paper / simulated execution.
- Anti-overfitting safeguards:
    * Taint tracking: Prevents and marks any optimization attempted on OOS data.
    * Independent performance reporting per period without metric cross-contamination.
- Configuration and strategy versioning per evaluation period:
    * Records strategy version, hyperparameters, model choices, and deterministic config hashes.
- Institutional walk-forward analytics:
    * Anchored and rolling split generators.
    * Walk-Forward Efficiency (WFE) ratios.
    * Performance degradation metrics and institutional robustness verdict.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from tradingagents.agents.schemas_forex import ForexTraderProposal
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestEngine,
    ForexBacktestResult,
)
from tradingagents.database.models import TradeExitReason
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex.domain import normalize_forex_pair

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Period Types & Configuration
# ---------------------------------------------------------------------------


class EvaluationPeriodType(str, Enum):
    """Distinct evaluation periods for institutional strategy lifecycle."""

    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    FORWARD_DEMO = "FORWARD_DEMO"

    @classmethod
    def from_str(cls, val: Any) -> EvaluationPeriodType:
        if isinstance(val, EvaluationPeriodType):
            return val
        s = str(val).strip().upper()
        for member in cls:
            if member.value == s:
                return member
        if "OOS" in s or "OUT" in s:
            return cls.OUT_OF_SAMPLE
        if "VAL" in s:
            return cls.VALIDATION
        if "DEV" in s:
            return cls.DEVELOPMENT
        if "DEMO" in s or "FORWARD" in s:
            return cls.FORWARD_DEMO
        return cls.DEVELOPMENT


@dataclass
class PeriodWindow:
    """Temporal boundary definition for an evaluation period."""

    period_type: EvaluationPeriodType
    start_time: datetime
    end_time: datetime
    description: str = ""

    def contains(self, ts: datetime) -> bool:
        return self.start_time <= ts <= self.end_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_type": self.period_type.value,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "description": self.description,
        }


@dataclass
class WalkForwardSplit:
    """A single sequential walk-forward split partition."""

    split_index: int
    split_id: str
    development: PeriodWindow
    validation: PeriodWindow | None
    out_of_sample: PeriodWindow
    forward_demo: PeriodWindow | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_index": self.split_index,
            "split_id": self.split_id,
            "development": self.development.to_dict(),
            "validation": self.validation.to_dict() if self.validation else None,
            "out_of_sample": self.out_of_sample.to_dict(),
            "forward_demo": self.forward_demo.to_dict() if self.forward_demo else None,
        }


# ---------------------------------------------------------------------------
# Performance Reports & Versioning
# ---------------------------------------------------------------------------


def compute_config_hash(config_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 fingerprint for a configuration dictionary."""
    serialized = json.dumps(config_dict, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


@dataclass
class PeriodPerformanceReport:
    """Independent performance metrics and versioning snapshot for an evaluation period."""

    period_type: EvaluationPeriodType
    start_date: str
    end_date: str
    strategy_version: str
    config_snapshot: dict[str, Any]
    config_hash: str
    result: ForexBacktestResult
    is_tainted: bool = False
    taint_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        res_dict = {
            "total_trades": self.result.total_trades,
            "winning_trades": self.result.winning_trades,
            "losing_trades": self.result.losing_trades,
            "win_rate_pct": self.result.win_rate_pct,
            "profit_factor": self.result.profit_factor,
            "expectancy_r": self.result.expectancy_r,
            "expectancy_cash": self.result.expectancy_cash,
            "total_net_profit": self.result.total_net_profit,
            "total_return_pct": self.result.total_return_pct,
            "max_drawdown_pct": self.result.max_drawdown_pct,
            "sharpe_ratio": self.result.sharpe_ratio,
            "sortino_ratio": self.result.sortino_ratio,
            "total_friction_usd": self.result.total_friction_usd,
        }
        return {
            "period_type": self.period_type.value,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "strategy_version": self.strategy_version,
            "config_snapshot": self.config_snapshot,
            "config_hash": self.config_hash,
            "is_tainted": self.is_tainted,
            "taint_reason": self.taint_reason,
            "metrics": res_dict,
        }


@dataclass
class WalkForwardValidationReport:
    """Comprehensive walk-forward out-of-sample validation audit report."""

    validation_id: str
    pair: str
    timeframe: str
    splits: list[WalkForwardSplit]
    period_reports: dict[str, PeriodPerformanceReport]
    walk_forward_efficiency_ratio: float
    profit_factor_degradation: float
    robustness_verdict: str  # ROBUST, MARGINAL, OVERFITTED, TAINTED
    markdown_summary: str
    source_provenance: dict[str, Any] = field(default_factory=dict)
    validation_status: str = "PARTIALLY_VALIDATED"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "validated_strategy_performance": False,
            "validation_status": self.validation_status,
            "validation_reasons": ["Descriptive split comparison; statistical significance and predictive profitability are not established."],
            "pair": self.pair,
            "timeframe": self.timeframe,
            "source_provenance": self.source_provenance,
            "split_count": len(self.splits),
            "splits": [s.to_dict() for s in self.splits],
            "period_reports": {k: v.to_dict() for k, v in self.period_reports.items()},
            "walk_forward_efficiency_ratio": self.walk_forward_efficiency_ratio,
            "profit_factor_degradation": self.profit_factor_degradation,
            "robustness_verdict": self.robustness_verdict,
            "markdown_summary": self.markdown_summary,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Walk-Forward Validator Engine
# ---------------------------------------------------------------------------


class ForexWalkForwardValidator:
    """Orchestrates multi-period walk-forward and out-of-sample backtesting."""

    def __init__(
        self,
        strategy_version: str = "v1.0.0",
        config_snapshot: dict[str, Any] | None = None,
        strict_oos_guard: bool = True,
    ) -> None:
        self.strategy_version = strategy_version
        self.config_snapshot = config_snapshot or {}
        self.config_hash = compute_config_hash(self.config_snapshot)
        self.strict_oos_guard = strict_oos_guard

    def generate_splits(
        self,
        candles: Sequence[ForexBar],
        n_splits: int = 1,
        dev_ratio: float = 0.50,
        val_ratio: float = 0.20,
        oos_ratio: float = 0.30,
        include_forward_demo: bool = False,
    ) -> list[WalkForwardSplit]:
        """Generate chronologically sequential walk-forward evaluation partitions."""
        if not candles:
            return []

        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        n = len(sorted_candles)
        if n < 10:
            raise ValueError(f"Insufficient candles ({n}) to construct valid walk-forward splits.")

        tot_ratio = dev_ratio + val_ratio + oos_ratio
        dev_w = dev_ratio / tot_ratio
        val_w = val_ratio / tot_ratio

        splits: list[WalkForwardSplit] = []

        if n_splits <= 1:
            # Single split partition
            dev_end_idx = max(2, int(n * dev_w))
            val_end_idx = max(dev_end_idx + 1, int(n * (dev_w + val_w))) if val_ratio > 0 else dev_end_idx
            oos_end_idx = n

            dev_p = PeriodWindow(
                period_type=EvaluationPeriodType.DEVELOPMENT,
                start_time=sorted_candles[0].timestamp,
                end_time=sorted_candles[dev_end_idx - 1].timestamp,
                description="Development In-Sample",
            )
            val_p = (
                PeriodWindow(
                    period_type=EvaluationPeriodType.VALIDATION,
                    start_time=sorted_candles[dev_end_idx].timestamp,
                    end_time=sorted_candles[val_end_idx - 1].timestamp,
                    description="Validation In-Sample",
                )
                if val_ratio > 0 and val_end_idx > dev_end_idx
                else None
            )
            oos_start = val_end_idx if val_p else dev_end_idx
            oos_p = PeriodWindow(
                period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
                start_time=sorted_candles[oos_start].timestamp,
                end_time=sorted_candles[oos_end_idx - 1].timestamp,
                description="Strict Out-of-Sample Independent Test",
            )

            demo_p = None
            if include_forward_demo and oos_end_idx > oos_start + 4:
                # Carve out last portion as Forward Demo
                demo_start = oos_end_idx - max(2, int((oos_end_idx - oos_start) * 0.25))
                demo_p = PeriodWindow(
                    period_type=EvaluationPeriodType.FORWARD_DEMO,
                    start_time=sorted_candles[demo_start].timestamp,
                    end_time=sorted_candles[-1].timestamp,
                    description="Forward Demo Paper Simulation",
                )
                oos_p.end_time = sorted_candles[demo_start - 1].timestamp

            splits.append(
                WalkForwardSplit(
                    split_index=0,
                    split_id=f"wf_split_{uuid.uuid4().hex[:8]}",
                    development=dev_p,
                    validation=val_p,
                    out_of_sample=oos_p,
                    forward_demo=demo_p,
                )
            )
        else:
            # Rolling window walk-forward splits
            window_size = int(n / (n_splits + 1))
            step_size = max(1, int((n - window_size) / n_splits))

            for s in range(n_splits):
                start_idx = s * step_size
                split_candles = sorted_candles[start_idx : start_idx + window_size]
                if len(split_candles) < 6:
                    continue

                sub_n = len(split_candles)
                sub_dev_end = max(2, int(sub_n * dev_w))
                sub_val_end = max(sub_dev_end + 1, int(sub_n * (dev_w + val_w))) if val_ratio > 0 else sub_dev_end
                sub_oos_end = sub_n

                dev_win = PeriodWindow(
                    period_type=EvaluationPeriodType.DEVELOPMENT,
                    start_time=split_candles[0].timestamp,
                    end_time=split_candles[sub_dev_end - 1].timestamp,
                    description=f"Split {s + 1} Development",
                )
                val_win = (
                    PeriodWindow(
                        period_type=EvaluationPeriodType.VALIDATION,
                        start_time=split_candles[sub_dev_end].timestamp,
                        end_time=split_candles[sub_val_end - 1].timestamp,
                        description=f"Split {s + 1} Validation",
                    )
                    if val_ratio > 0 and sub_val_end > sub_dev_end
                    else None
                )
                oos_s_idx = sub_val_end if val_win else sub_dev_end
                oos_win = PeriodWindow(
                    period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
                    start_time=split_candles[oos_s_idx].timestamp,
                    end_time=split_candles[sub_oos_end - 1].timestamp,
                    description=f"Split {s + 1} Out-of-Sample",
                )

                splits.append(
                    WalkForwardSplit(
                        split_index=s,
                        split_id=f"wf_split_{s}_{uuid.uuid4().hex[:6]}",
                        development=dev_win,
                        validation=val_win,
                        out_of_sample=oos_win,
                        forward_demo=None,
                    )
                )

        return splits

    def estimate_analysis_structure(
        self,
        candles: Sequence[ForexBar],
        *,
        n_splits: int = 1,
        sampling_interval: int = 1,
        max_analysis_points: int | None = None,
        dev_ratio: float = 0.50,
        val_ratio: float = 0.20,
        oos_ratio: float = 0.30,
        include_forward_demo: bool = True,
    ) -> dict[str, int]:
        """Count the exact periods and callback points used by ``validate``."""
        splits = self.generate_splits(
            candles,
            n_splits=n_splits,
            dev_ratio=dev_ratio,
            val_ratio=val_ratio,
            oos_ratio=oos_ratio,
            include_forward_demo=include_forward_demo,
        )
        interval = max(1, int(sampling_interval))
        period_count = 0
        analysis_points = 0
        for split in splits:
            periods = [split.development]
            if split.validation is not None:
                periods.append(split.validation)
            periods.append(split.out_of_sample)
            if split.forward_demo is not None:
                periods.append(split.forward_demo)
            for period in periods:
                bars = sum(period.contains(candle.timestamp) for candle in candles)
                points = (bars + interval - 1) // interval
                if max_analysis_points is not None:
                    points = min(points, int(max_analysis_points))
                analysis_points += points
                period_count += 1
        return {
            "split_count": len(splits),
            "period_count": period_count,
            "expected_analyses_count": analysis_points,
        }

    def run_period_backtest(
        self,
        candles: Sequence[ForexBar],
        period_window: PeriodWindow,
        backtest_config: ForexBacktestConfig | None = None,
        agent_pipeline_callable: Callable[[str, datetime, list[ForexBar]], ForexTraderProposal | None] | None = None,
        strategy_version: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        is_optimization_run: bool = False,
        pair: str = "EURUSD",
    ) -> PeriodPerformanceReport:
        """Run backtesting strictly isolated to a single period window with invariant checks."""
        p_type = period_window.period_type
        strat_ver = strategy_version or self.strategy_version
        cfg_snap = config_snapshot or self.config_snapshot
        cfg_hash = compute_config_hash(cfg_snap)

        # Anti-overfitting guardrail: Never optimize using OOS results and then call them OOS
        is_tainted = False
        taint_reason = ""
        if p_type == EvaluationPeriodType.OUT_OF_SAMPLE and is_optimization_run:
            if self.strict_oos_guard:
                raise ValueError(
                    "CRITICAL INVARIANT VIOLATION: Cannot execute optimization or parameter fitting on an OUT_OF_SAMPLE period. OOS data must remain strictly untouched."
                )
            is_tainted = True
            taint_reason = "Optimization attempted on Out-of-Sample period; results are TAINTED and no longer strictly OOS."
            logger.error("Walk-Forward Integrity Alert: %s", taint_reason)

        # Evaluation is restricted to this window, but the agent may use real bars
        # that closed before the window as warm-up context.  Warm-up bars are never
        # submitted to the execution engine, so they cannot create period trades.
        sorted_candles = sorted(candles, key=lambda candle: candle.timestamp)
        period_candles = [c for c in sorted_candles if period_window.contains(c.timestamp)]
        if not period_candles:
            raise ValueError(f"No candles found within period window {period_window.to_dict()}")

        cfg = backtest_config or ForexBacktestConfig()
        engine = ForexBacktestEngine(config=cfg)

        norm_pair = normalize_forex_pair(pair)
        history = [c for c in sorted_candles if c.timestamp < period_window.start_time]
        interval = max(1, int(cfg_snap.get("sampling_interval") or 1))
        max_points = cfg_snap.get("max_analysis_points")
        analyses = 0

        for index, candle in enumerate(period_candles):
            # First process the newly-arrived bar.  This permits pending orders
            # created by an earlier decision to trigger, but never lets a decision
            # made after this bar closed inspect this bar retroactively for fills.
            engine.step(candle=candle, pair=norm_pair)
            history.append(candle)

            if agent_pipeline_callable is None:
                continue
            if index % interval or (max_points is not None and analyses >= int(max_points)):
                continue

            analyses += 1
            decision_time = candle.close_time or candle.timestamp
            snapshot_setter = getattr(agent_pipeline_callable, "set_account_snapshot", None)
            if callable(snapshot_setter):
                snapshot_setter(engine.account_snapshot())
            proposal = agent_pipeline_callable(norm_pair, decision_time, list(history))
            if proposal is None:
                continue

            # MARKET orders use the first price that exists at the decision time:
            # the completed bar close.  LIMIT/STOP orders are only queued here and
            # cannot inspect this completed bar's high/low.
            execution_bar = replace(
                candle,
                timestamp=decision_time,
                open=candle.close,
                high=candle.close,
                low=candle.close,
            )
            engine.execute_proposal(proposal, execution_bar)

        if engine.open_trades:
            last_bar = period_candles[-1]
            exit_time = last_bar.close_time or last_bar.timestamp
            for trade in list(engine.open_trades):
                engine._settle_trade(trade, last_bar.close, exit_time, TradeExitReason.MANUAL)
            engine.open_trades = []
            engine.equity = engine.balance

        start_str = period_candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        end_time = period_candles[-1].close_time or period_candles[-1].timestamp
        result = engine._build_result(start_str, end_time.strftime("%Y-%m-%d %H:%M"))

        end_str = end_time.strftime("%Y-%m-%d %H:%M")

        return PeriodPerformanceReport(
            period_type=p_type,
            start_date=start_str,
            end_date=end_str,
            strategy_version=strat_ver,
            config_snapshot=cfg_snap,
            config_hash=cfg_hash,
            result=result,
            is_tainted=is_tainted,
            taint_reason=taint_reason,
        )

    def validate(
        self,
        candles: Sequence[ForexBar],
        pair: str = "EURUSD",
        timeframe: str = "H1",
        backtest_config: ForexBacktestConfig | None = None,
        agent_pipeline_callable: Callable[[str, datetime, list[ForexBar]], ForexTraderProposal | None] | None = None,
        n_splits: int = 1,
        dev_ratio: float = 0.50,
        val_ratio: float = 0.20,
        oos_ratio: float = 0.30,
        include_forward_demo: bool = True,
        source_provenance: dict[str, Any] | None = None,
        validation_status: str = "PARTIALLY_VALIDATED",
    ) -> WalkForwardValidationReport:
        """Execute complete walk-forward and out-of-sample validation matrix."""
        norm_pair = normalize_forex_pair(pair)
        validation_id = f"wf_val_{uuid.uuid4().hex[:10]}"

        splits = self.generate_splits(
            candles=candles,
            n_splits=n_splits,
            dev_ratio=dev_ratio,
            val_ratio=val_ratio,
            oos_ratio=oos_ratio,
            include_forward_demo=include_forward_demo,
        )
        if not splits:
            raise ValueError("Failed generating walk-forward splits from provided candle sequence.")

        period_reports: dict[str, PeriodPerformanceReport] = {}

        if agent_pipeline_callable is None:
            # Backward-compatible engine-only exploratory mode. Production routes
            # must inject the real pipeline and label this path DEMO.
            def empty_pipeline(_pair, _cutoff, _history):
                return None

            agent_pipeline_callable = empty_pipeline
            validation_status = "DEMO"

        for split in splits:
            periods_to_test = [split.development]
            if split.validation:
                periods_to_test.append(split.validation)
            periods_to_test.append(split.out_of_sample)
            if split.forward_demo:
                periods_to_test.append(split.forward_demo)

            for p_win in periods_to_test:
                rep = self.run_period_backtest(
                    candles=candles,
                    period_window=p_win,
                    backtest_config=backtest_config,
                    agent_pipeline_callable=agent_pipeline_callable,
                    strategy_version=self.strategy_version,
                    config_snapshot=self.config_snapshot,
                    is_optimization_run=False,
                    pair=norm_pair,
                )
                period_reports[f"{split.split_id}:{p_win.period_type.value}"] = rep

        # Preserve the single-split lookup contract while multi-split keys remain unique.
        if len(splits) == 1:
            prefix = f"{splits[0].split_id}:"
            period_reports.update({k.removeprefix(prefix): v for k, v in list(period_reports.items())})

        # Compute Institutional Walk-Forward Efficiency (WFE) and Robustness
        dev_reports = [
            period_reports[f"{s.split_id}:{EvaluationPeriodType.DEVELOPMENT.value}"]
            for s in splits
        ]
        oos_reports = [
            period_reports[f"{s.split_id}:{EvaluationPeriodType.OUT_OF_SAMPLE.value}"]
            for s in splits
        ]
        dev_rep = dev_reports[0] if dev_reports else None
        oos_rep = oos_reports[0] if oos_reports else None

        wfe_ratio = 0.0
        pf_degradation = 0.0
        robustness = "OVERFITTED"

        if dev_rep and oos_rep:
            if any(r.is_tainted for r in oos_reports):
                robustness = "TAINTED"
            else:
                dev_ret = max(0.0001, sum(r.result.total_return_pct for r in dev_reports))
                oos_ret = sum(r.result.total_return_pct for r in oos_reports)
                wfe_ratio = round((oos_ret / dev_ret) if dev_ret > 0 else 0.0, 3)

                dev_pf = sum(r.result.profit_factor for r in dev_reports) / len(dev_reports)
                oos_pf = sum(r.result.profit_factor for r in oos_reports) / len(oos_reports)
                pf_degradation = round(((dev_pf - oos_pf) / dev_pf) * 100.0, 1) if dev_pf > 0 else 0.0

                # Classification rules
                oos_expectancy = sum(r.result.expectancy_r for r in oos_reports) / len(oos_reports)
                if oos_pf >= 1.3 and wfe_ratio >= 0.60 and oos_expectancy > 0.15:
                    robustness = "ROBUST"
                elif oos_pf >= 1.05 and wfe_ratio >= 0.40:
                    robustness = "MARGINAL"
                else:
                    robustness = "OVERFITTED"

        md_summary = self._render_markdown_summary(
            validation_id=validation_id,
            pair=norm_pair,
            timeframe=timeframe,
            period_reports=period_reports,
            wfe=wfe_ratio,
            pf_deg=pf_degradation,
            verdict=robustness,
        )

        return WalkForwardValidationReport(
            validation_id=validation_id,
            pair=norm_pair,
            timeframe=timeframe,
            splits=splits,
            period_reports=period_reports,
            walk_forward_efficiency_ratio=wfe_ratio,
            profit_factor_degradation=pf_degradation,
            robustness_verdict=robustness,
            markdown_summary=md_summary,
            source_provenance=dict(source_provenance or {}),
            validation_status=validation_status,
        )

    def _render_markdown_summary(
        self,
        validation_id: str,
        pair: str,
        timeframe: str,
        period_reports: dict[str, PeriodPerformanceReport],
        wfe: float,
        pf_deg: float,
        verdict: str,
    ) -> str:
        """Render institutional walk-forward summary dashboard."""
        verdict_badge = {
            "ROBUST": "🟢 **ROBUST** (Heuristic thresholds met in this sample; not statistical validation)",
            "MARGINAL": "🟡 **MARGINAL** (Degradation observed, sizing dampening advised)",
            "OVERFITTED": "🔴 **OVERFITTED** (Performance deteriorates significantly out-of-sample)",
            "TAINTED": "🚨 **TAINTED** (OOS data was accessed during optimization)",
        }.get(verdict, verdict)

        lines = [
            f"# Institutional Walk-Forward Validation Report: `{validation_id}`",
            "",
            f"**Currency Pair**: `{pair}` | **Timeframe**: `{timeframe}` | **Strategy Version**: `{self.strategy_version}`",
            f"**Config Hash**: `{self.config_hash}`",
            "",
            f"### Robustness Assessment: {verdict_badge}",
            f"- **Walk-Forward Efficiency (WFE)**: `{wfe:.2f}`",
            f"- **Profit Factor Degradation**: `{pf_deg:.1f}%`",
            "",
            "## Independent Period Performance Breakdown",
            "",
            "| Period Type | Date Range | Net PnL ($) | Win Rate (%) | Profit Factor | Expectancy (R) | Max DD (%) | Sharpe | Friction ($) | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        rows = [
            (key, rep) for key, rep in period_reports.items()
            if ":" in key or len(period_reports) <= 4
        ]
        for p_name, rep in rows:
            r = rep.result
            status_txt = "⚠️ TAINTED" if rep.is_tainted else "✅ VALID"
            date_range = f"`{rep.start_date}` to `{rep.end_date}`"
            lines.append(
                f"| **{p_name}** | {date_range} | `${r.total_net_profit:,.2f}` | `{r.win_rate_pct:.1f}%` | `{r.profit_factor:.2f}` | `{r.expectancy_r:+.2f}` | `{r.max_drawdown_pct:.1f}%` | `{r.sharpe_ratio:.2f}` | `${r.total_friction_usd:,.2f}` | {status_txt} |"
            )

        lines.extend([
            "",
            "## Methodological Invariants",
            "- **Zero-Lookahead Isolation**: Every bar within each evaluation period is processed strictly chronologically.",
            "- **Out-of-Sample Sanctity**: Out-of-Sample results were never tuned against or utilized for hyperparameter search.",
            "- **Independent Ledger**: Trade statistics and metrics are computed completely independently per period.",
            "",
        ])
        return "\n".join(lines)
