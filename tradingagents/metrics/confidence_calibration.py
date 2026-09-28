"""Confidence Calibration & Empirical Win Expectancy Engine (Phase 18).

Provides mathematical evaluation and empirical calibration of AI trade proposal
confidence scores against realized trade outcomes.

Rules & Principles:
- Create discrete confidence buckets: 50-60, 60-70, 70-80, 80-90, 90-100 (plus <50 for low confidence).
- For each bucket compute: trade count, win rate, average R, expectancy.
- Never display model confidence as true probability unless calibrated.
- Enforce explicit small-sample warnings whenever sample sizes fall below statistical thresholds.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Standard 5-tier confidence buckets as defined in Phase 18 specifications
DEFAULT_BUCKET_SPECS: list[tuple[str, float, float]] = [
    ("50-60", 50.0, 60.0),
    ("60-70", 60.0, 70.0),
    ("70-80", 70.0, 80.0),
    ("80-90", 80.0, 90.0),
    ("90-100", 90.0, 100.0),
]

MIN_CALIBRATION_SAMPLES: int = 10


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class ConfidenceBucketMetrics(BaseModel):
    """Empirical performance metrics aggregated within a specific confidence band."""

    bucket_label: str = Field(description="Display label, e.g. '50-60', '70-80'")
    lower_bound: float = Field(description="Inclusive lower bound (0-100 scale)")
    upper_bound: float = Field(description="Exclusive upper bound, or inclusive for 100")
    trade_count: int = Field(default=0, ge=0, description="Total trades in this confidence bucket")
    win_count: int = Field(default=0, ge=0, description="Number of winning trades")
    loss_count: int = Field(default=0, ge=0, description="Number of losing trades")
    breakeven_count: int = Field(default=0, ge=0, description="Number of scratch/breakeven trades")
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Empirical win rate as decimal (0.0 - 1.0)")
    win_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Empirical win rate as percentage")
    average_r: float = Field(default=0.0, description="Average realized R multiple across all bucket trades")
    average_win_r: float = Field(default=0.0, ge=0.0, description="Average R multiple of winning trades")
    average_loss_r: float = Field(default=0.0, description="Average R multiple of losing trades (negative or 0)")
    expectancy: float = Field(
        default=0.0,
        description="Mathematical expectancy in R per trade: (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)",
    )
    calibrated_win_probability: float | None = Field(
        default=None,
        description="Empirical win probability if sample size threshold met; None if uncalibrated",
    )
    is_calibrated: bool = Field(
        default=False,
        description="True ONLY if trade count meets or exceeds minimum calibration sample threshold",
    )
    small_sample_warning: str | None = Field(
        default=None,
        description="Warning message if sample size is insufficient for statistical calibration",
    )


class ConfidenceCalibrationReport(BaseModel):
    """Institutional calibration report comparing model confidence to empirical outcomes."""

    total_trades_analyzed: int = Field(default=0, ge=0)
    trades_with_confidence: int = Field(default=0, ge=0)
    unlabeled_trades: int = Field(default=0, ge=0)
    min_samples_threshold: int = Field(default=MIN_CALIBRATION_SAMPLES, ge=1)
    buckets: dict[str, ConfidenceBucketMetrics] = Field(default_factory=dict)
    expected_calibration_error: float | None = Field(
        default=None,
        description="Expected Calibration Error (ECE): weighted average discrepancy between confidence and empirical win rate",
    )
    brier_score: float | None = Field(
        default=None,
        description="Brier score quadratic calibration error: mean((forecast_prob - empirical_outcome)^2)",
    )
    is_system_calibrated: bool = Field(
        default=False,
        description="True if all populated buckets meet minimum calibration sample threshold",
    )
    warnings: list[str] = Field(default_factory=list)
    summary_markdown: str = Field(default="", description="Rendered institutional Markdown scorecard")


class CalibratedConfidenceResult(BaseModel):
    """Calibration verdict and display text for an individual trade proposal."""

    raw_model_confidence: float = Field(description="Original uncalibrated model confidence (0-100 scale)")
    bucket_label: str = Field(description="Assigned bucket label")
    is_calibrated: bool = Field(description="Whether the bucket has sufficient samples to claim calibrated probability")
    calibrated_win_probability: float | None = Field(
        default=None,
        description="True calibrated empirical probability (0.0 to 1.0); None if uncalibrated",
    )
    display_string: str = Field(
        description="Safe UI/audit string clearly separating model confidence from calibrated probability"
    )
    small_sample_warning: str | None = Field(default=None)
    trade_count_in_bucket: int = Field(default=0, ge=0)
    bucket_win_rate: float | None = Field(default=None)
    bucket_expectancy: float | None = Field(default=None)


# ---------------------------------------------------------------------------
# Calibration Engine
# ---------------------------------------------------------------------------


class ConfidenceCalibrationEngine:
    """Computes calibration tables, bucket statistics, and safe probabilistic representations."""

    def __init__(
        self,
        min_samples: int = MIN_CALIBRATION_SAMPLES,
        bucket_specs: Sequence[tuple[str, float, float]] | None = None,
    ) -> None:
        self.min_samples = max(1, int(min_samples))
        self.bucket_specs = list(bucket_specs or DEFAULT_BUCKET_SPECS)

    @staticmethod
    def normalize_confidence(val: Any) -> float | None:
        """Normalize raw confidence score to standard 0.0 - 100.0 scale.

        If a decimal between 0.0 and 1.0 is passed (e.g. 0.78), it is scaled to 78.0%.
        """
        if val is None:
            return None
        try:
            num = float(val)
            if num < 0.0:
                return 0.0
            if 0.0 < num <= 1.0:
                num = num * 100.0
            return round(min(num, 100.0), 2)
        except (ValueError, TypeError):
            return None

    def assign_bucket(self, confidence: float) -> str:
        """Map normalized confidence score to bucket label."""
        c = max(0.0, min(100.0, confidence))
        if c < 50.0:
            return "<50"
        for label, lower, upper in self.bucket_specs:
            if upper == 100.0:
                if lower <= c <= upper:
                    return label
            else:
                if lower <= c < upper:
                    return label
        return "90-100"

    def compute_calibration(
        self,
        trades: Sequence[Any],
        min_samples: int | None = None,
    ) -> ConfidenceCalibrationReport:
        """Aggregate trade outcomes by confidence bucket and compute empirical metrics.

        Parameters
        ----------
        trades:
            Sequence of trade records (TradeJournalRecord, SkippedProposalSimulation,
            ProposalRecord, dictionaries, or objects with confidence and outcome info).
        min_samples:
            Minimum sample size required in a bucket to declare it calibrated.

        Returns
        -------
        ConfidenceCalibrationReport
            Full calibration report with deterministic bucket statistics and sample warnings.
        """
        eff_min = max(1, int(min_samples if min_samples is not None else self.min_samples))

        # Initialize bucket storage
        buckets: dict[str, dict[str, Any]] = {}
        for label, lower, upper in self.bucket_specs:
            buckets[label] = {
                "lower": lower,
                "upper": upper,
                "trades": [],
                "wins": 0,
                "losses": 0,
                "breakevens": 0,
                "r_multiples": [],
                "win_r": [],
                "loss_r": [],
                "prob_diffs": [],
                "brier_sq_errors": [],
            }
        buckets["<50"] = {
            "lower": 0.0,
            "upper": 50.0,
            "trades": [],
            "wins": 0,
            "losses": 0,
            "breakevens": 0,
            "r_multiples": [],
            "win_r": [],
            "loss_r": [],
            "prob_diffs": [],
            "brier_sq_errors": [],
        }

        total_analyzed = 0
        with_conf = 0
        unlabeled = 0

        for t in trades:
            total_analyzed += 1

            # 1. Extract confidence
            raw_c = getattr(t, "confidence", None)
            if raw_c is None and isinstance(t, dict):
                raw_c = t.get("confidence") or t.get("model_confidence")
            if raw_c is None and hasattr(t, "metadata") and isinstance(t.metadata, dict):
                raw_c = t.metadata.get("confidence") or t.metadata.get("model_confidence")

            norm_c = self.normalize_confidence(raw_c)
            if norm_c is None:
                unlabeled += 1
                continue

            with_conf += 1
            bucket_key = self.assign_bucket(norm_c)
            b = buckets[bucket_key]
            b["trades"].append(t)

            # 2. Extract outcome & realized R
            r_mult = getattr(t, "r_multiple", None)
            if r_mult is None and hasattr(t, "theoretical_r"):
                r_mult = getattr(t, "theoretical_r", None)
            if r_mult is None and isinstance(t, dict):
                r_mult = t.get("r_multiple") or t.get("theoretical_r")

            net_prof = getattr(t, "net_profit", None)
            if net_prof is None and isinstance(t, dict):
                net_prof = t.get("net_profit")

            sim_status = getattr(t, "status", None)
            if hasattr(sim_status, "value"):
                sim_status = sim_status.value
            sim_status_str = str(sim_status).upper()

            # Determine win / loss / breakeven
            is_win = False
            is_loss = False

            if r_mult is not None:
                r_val = float(r_mult)
                if r_val > 0.05:
                    is_win = True
                elif r_val < -0.05:
                    is_loss = True
            elif net_prof is not None:
                np_val = float(net_prof)
                if np_val > 0.0:
                    is_win = True
                    r_val = 1.0  # Fallback R proxy
                elif np_val < 0.0:
                    is_loss = True
                    r_val = -1.0
                else:
                    r_val = 0.0
            elif "TP" in sim_status_str or "WIN" in sim_status_str:
                is_win = True
                r_val = 1.5
            elif "SL" in sim_status_str or "LOSS" in sim_status_str:
                is_loss = True
                r_val = -1.0
            else:
                r_val = 0.0

            b["r_multiples"].append(r_val)
            if is_win:
                b["wins"] += 1
                b["win_r"].append(r_val)
                outcome_numeric = 1.0
            elif is_loss:
                b["losses"] += 1
                b["loss_r"].append(r_val)
                outcome_numeric = 0.0
            else:
                b["breakevens"] += 1
                outcome_numeric = 0.5

            # Forecast probability for Brier Score (0.0 to 1.0)
            forecast_prob = norm_c / 100.0
            b["brier_sq_errors"].append((forecast_prob - outcome_numeric) ** 2)

        # 3. Assemble bucket models & calibration metrics
        bucket_results: dict[str, ConfidenceBucketMetrics] = {}
        all_warnings: list[str] = []
        total_ece_numerator = 0.0
        total_brier_sq = 0.0
        total_brier_count = 0
        has_uncalibrated_populated_bucket = False

        # Only include <50 if there are trades in it, otherwise keep standard 5 buckets
        keys_to_process = [spec[0] for spec in self.bucket_specs]
        if len(buckets["<50"]["trades"]) > 0:
            keys_to_process.insert(0, "<50")

        for key in keys_to_process:
            b_data = buckets[key]
            count = len(b_data["trades"])
            wins = b_data["wins"]
            losses = b_data["losses"]
            bes = b_data["breakevens"]
            r_list = b_data["r_multiples"]
            win_r_list = b_data["win_r"]
            loss_r_list = b_data["loss_r"]

            win_rate = round(wins / count, 4) if count > 0 else 0.0
            win_rate_pct = round(win_rate * 100.0, 1)

            avg_r = round(float(sum(r_list) / count), 2) if count > 0 else 0.0
            avg_win_r = round(float(sum(win_r_list) / len(win_r_list)), 2) if win_r_list else 0.0
            avg_loss_r = round(float(sum(loss_r_list) / len(loss_r_list)), 2) if loss_r_list else 0.0

            # Mathematical Expectancy: (P(Win) * AvgWinR) + (P(Loss) * AvgLossR)
            # which algebraically aligns with overall avg_r
            expectancy = round((win_rate * avg_win_r) + ((1.0 - win_rate) * avg_loss_r), 2) if count > 0 else 0.0

            # Calibration check & warning rules
            is_calib = count >= eff_min
            if is_calib:
                calib_prob: float | None = win_rate
                warn: str | None = None
            else:
                calib_prob = None  # CRITICAL: Never display model confidence as true probability unless calibrated
                if count > 0:
                    warn = (
                        f"Small sample warning: only {count} trade(s) in bucket '{key}' "
                        f"(minimum {eff_min} required for calibration). Do NOT treat model confidence as true probability."
                    )
                    all_warnings.append(warn)
                    has_uncalibrated_populated_bucket = True
                else:
                    warn = f"No trades recorded in confidence bucket '{key}'."

            # ECE contribution
            if count > 0:
                midpoint_prob = ((b_data["lower"] + b_data["upper"]) / 2.0) / 100.0
                total_ece_numerator += count * abs(midpoint_prob - win_rate)
                total_brier_sq += sum(b_data["brier_sq_errors"])
                total_brier_count += count

            bucket_results[key] = ConfidenceBucketMetrics(
                bucket_label=key,
                lower_bound=b_data["lower"],
                upper_bound=b_data["upper"],
                trade_count=count,
                win_count=wins,
                loss_count=losses,
                breakeven_count=bes,
                win_rate=win_rate,
                win_rate_pct=win_rate_pct,
                average_r=avg_r,
                average_win_r=avg_win_r,
                average_loss_r=avg_loss_r,
                expectancy=expectancy,
                calibrated_win_probability=calib_prob,
                is_calibrated=is_calib,
                small_sample_warning=warn,
            )

        # Expected Calibration Error & Brier Score
        ece = round(total_ece_numerator / with_conf, 4) if with_conf > 0 else None
        brier = round(total_brier_sq / total_brier_count, 4) if total_brier_count > 0 else None

        is_system_calib = with_conf >= eff_min and not has_uncalibrated_populated_bucket

        # Render institutional markdown table
        summary_md = self._render_markdown_table(
            buckets=bucket_results,
            total_analyzed=total_analyzed,
            with_conf=with_conf,
            unlabeled=unlabeled,
            min_samples=eff_min,
            ece=ece,
            brier=brier,
            warnings=all_warnings,
        )

        return ConfidenceCalibrationReport(
            total_trades_analyzed=total_analyzed,
            trades_with_confidence=with_conf,
            unlabeled_trades=unlabeled,
            min_samples_threshold=eff_min,
            buckets=bucket_results,
            expected_calibration_error=ece,
            brier_score=brier,
            is_system_calibrated=is_system_calib,
            warnings=all_warnings,
            summary_markdown=summary_md,
        )

    def calibrate_score(
        self,
        raw_confidence: float,
        report: ConfidenceCalibrationReport | None = None,
        trades: Sequence[Any] | None = None,
    ) -> CalibratedConfidenceResult:
        """Calibrate a single model confidence score against empirical outcomes.

        Strictly enforces:
        If sample size in the bucket is insufficient, `calibrated_win_probability` is None,
        and `display_string` includes an explicit warning banner preventing users
        from mistaking model confidence for empirical probability.
        """
        norm_c = self.normalize_confidence(raw_confidence) or 50.0
        active_report = report
        if active_report is None:
            active_report = self.compute_calibration(trades or [])

        bucket_key = self.assign_bucket(norm_c)
        bucket = active_report.buckets.get(bucket_key)

        if bucket is None or not bucket.is_calibrated:
            count = bucket.trade_count if bucket else 0
            req = active_report.min_samples_threshold
            warn = (
                f"Small sample warning: only {count} trade(s) in bucket '{bucket_key}' "
                f"(minimum {req} required). Do NOT treat model confidence as true probability."
            )
            display = (
                f"Model Confidence (UNCALIBRATED): {norm_c:.1f}% "
                f"[⚠️ Insufficient sample: N={count}/{req} in Bucket '{bucket_key}']. "
                f"Do NOT treat as true win probability."
            )
            return CalibratedConfidenceResult(
                raw_model_confidence=norm_c,
                bucket_label=bucket_key,
                is_calibrated=False,
                calibrated_win_probability=None,
                display_string=display,
                small_sample_warning=warn,
                trade_count_in_bucket=count,
                bucket_win_rate=bucket.win_rate if bucket else None,
                bucket_expectancy=bucket.expectancy if bucket else None,
            )

        # Bucket is calibrated
        calib_prob = bucket.calibrated_win_probability
        calib_pct = (calib_prob * 100.0) if calib_prob is not None else 0.0
        display = (
            f"Model Confidence: {norm_c:.1f}% -> Calibrated Win Probability: {calib_pct:.1f}% "
            f"(Bucket '{bucket_key}', N={bucket.trade_count}, Expectancy: {bucket.expectancy:+.2f}R)"
        )
        return CalibratedConfidenceResult(
            raw_model_confidence=norm_c,
            bucket_label=bucket_key,
            is_calibrated=True,
            calibrated_win_probability=calib_prob,
            display_string=display,
            small_sample_warning=None,
            trade_count_in_bucket=bucket.trade_count,
            bucket_win_rate=bucket.win_rate,
            bucket_expectancy=bucket.expectancy,
        )

    def _render_markdown_table(
        self,
        buckets: dict[str, ConfidenceBucketMetrics],
        total_analyzed: int,
        with_conf: int,
        unlabeled: int,
        min_samples: int,
        ece: float | None,
        brier: float | None,
        warnings: list[str],
    ) -> str:
        """Render a clean GitHub-flavored institutional Markdown table."""
        lines = [
            "### Model Confidence Calibration Scorecard",
            "",
            f"- **Trades Evaluated:** `{total_analyzed}` (With Confidence: `{with_conf}`, Unlabeled: `{unlabeled}`)",
            f"- **Calibration Sample Threshold:** `N >= {min_samples}` per bucket",
        ]
        if ece is not None:
            lines.append(f"- **Expected Calibration Error (ECE):** `{ece:.4f}` ({ece * 100.0:.2f}%)")
        if brier is not None:
            lines.append(f"- **Brier Score:** `{brier:.4f}` (lower is better, 0.25 is random)")
        lines.extend([
            "",
            "| Confidence Band | Trade Count | Win Rate | Average R | Expectancy | Calibration Status | Calibrated Win Probability |",
            "|:---|:---:|:---:|:---:|:---:|:---:|:---:|",
        ])

        for _key, b in buckets.items():
            if b.is_calibrated:
                status_badge = "✅ **CALIBRATED**"
                prob_str = f"**{b.win_rate_pct:.1f}%**"
            elif b.trade_count > 0:
                status_badge = "⚠️ *Small Sample*"
                prob_str = "— *(Uncalibrated)*"
            else:
                status_badge = "⚪ *No Data*"
                prob_str = "—"

            avg_r_str = f"{b.average_r:+.2f}R" if b.trade_count > 0 else "—"
            exp_str = f"{b.expectancy:+.2f}R" if b.trade_count > 0 else "—"
            wr_str = f"{b.win_rate_pct:.1f}% ({b.win_count}/{b.trade_count})" if b.trade_count > 0 else "—"

            lines.append(
                f"| **{b.bucket_label}%** | {b.trade_count} | {wr_str} | {avg_r_str} | {exp_str} | {status_badge} | {prob_str} |"
            )

        if warnings:
            lines.extend([
                "",
                "> [!WARNING]",
                "> **Statistical Calibration Notice:**",
            ])
            for w in warnings:
                lines.append(f"> - {w}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module Level Factory & Helpers
# ---------------------------------------------------------------------------

_GLOBAL_CALIBRATOR = ConfidenceCalibrationEngine()


def calibrate_confidence_score(
    raw_confidence: float,
    trades: Sequence[Any] | None = None,
    report: ConfidenceCalibrationReport | None = None,
    min_samples: int = MIN_CALIBRATION_SAMPLES,
) -> CalibratedConfidenceResult:
    """Convenience helper to evaluate and calibrate a single confidence score."""
    engine = ConfidenceCalibrationEngine(min_samples=min_samples)
    return engine.calibrate_score(raw_confidence=raw_confidence, report=report, trades=trades)
