"""Institutional Verification Suite for Walk-Forward / Out-of-Sample Validation (Phase 21).

Validates:
1. Evaluation period types: DEVELOPMENT, VALIDATION, OUT_OF_SAMPLE, FORWARD_DEMO.
2. Chronological split generation (single and rolling multi-split).
3. Independent period performance reporting with no metric cross-contamination.
4. Out-of-sample anti-overfitting guardrail (strict_oos_guard).
5. Strategy/configuration versioning and deterministic config hashing.
6. Walk-Forward Efficiency (WFE) and robustness verdict.
7. Full validate() pipeline producing institutional WalkForwardValidationReport.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
)
from tradingagents.backtest.forex_engine import ForexBacktestConfig
from tradingagents.backtest.walk_forward import (
    EvaluationPeriodType,
    ForexWalkForwardValidator,
    PeriodWindow,
    WalkForwardValidationReport,
    compute_config_hash,
)
from tradingagents.dataflows.forex_data import ForexBar

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_candle(ts: datetime, o: float, h: float, low: float, c: float) -> ForexBar:
    return ForexBar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


def make_trending_candles(n: int, start: datetime, direction: str = "up") -> list[ForexBar]:
    """Generate n candles with a mild upward or downward trend."""
    candles: list[ForexBar] = []
    price = 1.1000
    for i in range(n):
        ts = start + timedelta(hours=i)
        step = 0.0005 if direction == "up" else -0.0005
        o = price
        c = price + step
        h = max(o, c) + 0.0003
        low = min(o, c) - 0.0003
        candles.append(make_candle(ts, round(o, 5), round(h, 5), round(low, 5), round(c, 5)))
        price = c
    return candles


# ---------------------------------------------------------------------------
# 1. Period Types
# ---------------------------------------------------------------------------


class TestEvaluationPeriodTypes:
    def test_enum_members_exist(self):
        assert EvaluationPeriodType.DEVELOPMENT.value == "DEVELOPMENT"
        assert EvaluationPeriodType.VALIDATION.value == "VALIDATION"
        assert EvaluationPeriodType.OUT_OF_SAMPLE.value == "OUT_OF_SAMPLE"
        assert EvaluationPeriodType.FORWARD_DEMO.value == "FORWARD_DEMO"

    def test_from_str_aliases(self):
        assert EvaluationPeriodType.from_str("oos") == EvaluationPeriodType.OUT_OF_SAMPLE
        assert EvaluationPeriodType.from_str("VALIDATION") == EvaluationPeriodType.VALIDATION
        assert EvaluationPeriodType.from_str("dev") == EvaluationPeriodType.DEVELOPMENT
        assert EvaluationPeriodType.from_str("forward_demo") == EvaluationPeriodType.FORWARD_DEMO


# ---------------------------------------------------------------------------
# 2. Period Window
# ---------------------------------------------------------------------------


class TestPeriodWindow:
    def test_contains_boundary(self):
        t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t1 = datetime(2025, 6, 30, tzinfo=timezone.utc)
        pw = PeriodWindow(
            period_type=EvaluationPeriodType.DEVELOPMENT,
            start_time=t0,
            end_time=t1,
        )
        assert pw.contains(t0)
        assert pw.contains(t1)
        assert pw.contains(datetime(2025, 3, 15, tzinfo=timezone.utc))
        assert not pw.contains(datetime(2024, 12, 31, tzinfo=timezone.utc))
        assert not pw.contains(datetime(2025, 7, 1, tzinfo=timezone.utc))

    def test_to_dict_serializes(self):
        pw = PeriodWindow(
            period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
            start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2025, 6, 30, tzinfo=timezone.utc),
            description="Test OOS window",
        )
        d = pw.to_dict()
        assert d["period_type"] == "OUT_OF_SAMPLE"
        assert "start_time" in d
        assert d["description"] == "Test OOS window"


# ---------------------------------------------------------------------------
# 3. Config Hashing
# ---------------------------------------------------------------------------


class TestConfigHash:
    def test_deterministic_hash(self):
        cfg = {"model": "gpt-4.1", "temperature": 0.7, "pair": "EURUSD"}
        h1 = compute_config_hash(cfg)
        h2 = compute_config_hash(cfg)
        assert h1 == h2
        assert len(h1) == 16  # truncated SHA-256

    def test_different_configs_different_hashes(self):
        h1 = compute_config_hash({"model": "gpt-4.1"})
        h2 = compute_config_hash({"model": "gpt-4.1-mini"})
        assert h1 != h2


# ---------------------------------------------------------------------------
# 4. Split Generation
# ---------------------------------------------------------------------------


class TestSplitGeneration:
    def test_single_split_produces_dev_val_oos(self):
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        splits = validator.generate_splits(candles, n_splits=1, dev_ratio=0.5, val_ratio=0.2, oos_ratio=0.3)

        assert len(splits) == 1
        s = splits[0]
        assert s.development.period_type == EvaluationPeriodType.DEVELOPMENT
        assert s.validation is not None
        assert s.validation.period_type == EvaluationPeriodType.VALIDATION
        assert s.out_of_sample.period_type == EvaluationPeriodType.OUT_OF_SAMPLE

        # Development must end before OOS starts (chronological ordering)
        assert s.development.end_time < s.out_of_sample.start_time

    def test_no_validation_when_val_ratio_zero(self):
        candles = make_trending_candles(50, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        splits = validator.generate_splits(candles, n_splits=1, dev_ratio=0.6, val_ratio=0.0, oos_ratio=0.4)
        assert splits[0].validation is None

    def test_multi_split_produces_multiple(self):
        candles = make_trending_candles(200, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        splits = validator.generate_splits(candles, n_splits=3, dev_ratio=0.5, val_ratio=0.2, oos_ratio=0.3)
        assert len(splits) == 3

        timestamp_to_index = {candle.timestamp: index for index, candle in enumerate(candles)}
        oos_ranges = [
            range(
                timestamp_to_index[split.out_of_sample.start_time],
                timestamp_to_index[split.out_of_sample.end_time] + 1,
            )
            for split in splits
        ]
        assert all(
            set(left).isdisjoint(right)
            for left, right in zip(oos_ranges, oos_ranges[1:], strict=False)
        )
        assert oos_ranges[-1].stop == len(candles)

        initial_prefix = set(
            range(
                timestamp_to_index[splits[0].development.start_time],
                timestamp_to_index[splits[0].out_of_sample.start_time],
            )
        )
        accounted = initial_prefix | set().union(*(set(indices) for indices in oos_ranges))
        assert accounted == set(range(len(candles)))

        for split in splits:
            in_sample_end = split.validation.end_time if split.validation else split.development.end_time
            assert in_sample_end < split.out_of_sample.start_time

        assert splits[1].development.start_time == splits[0].development.start_time
        assert splits[1].out_of_sample.start_time > splits[0].out_of_sample.end_time

    def test_insufficient_candles_raises(self):
        candles = make_trending_candles(5, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        with pytest.raises(ValueError, match="Insufficient candles"):
            validator.generate_splits(candles, n_splits=1)

    def test_split_serializes(self):
        candles = make_trending_candles(50, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        splits = validator.generate_splits(candles, n_splits=1)
        d = splits[0].to_dict()
        assert "split_id" in d
        assert "development" in d
        assert "out_of_sample" in d


# ---------------------------------------------------------------------------
# 5. Out-of-Sample Anti-Overfitting Guardrail
# ---------------------------------------------------------------------------


class TestOOSGuardrail:
    def test_optimization_on_oos_raises_when_strict(self):
        candles = make_trending_candles(50, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator(strict_oos_guard=True)
        oos_window = PeriodWindow(
            period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )
        with pytest.raises(ValueError, match="CRITICAL INVARIANT VIOLATION"):
            validator.run_period_backtest(
                candles=candles,
                period_window=oos_window,
                is_optimization_run=True,
            )

    def test_optimization_on_oos_taints_when_not_strict(self):
        candles = make_trending_candles(50, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator(strict_oos_guard=False)
        oos_window = PeriodWindow(
            period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )
        report = validator.run_period_backtest(
            candles=candles,
            period_window=oos_window,
            is_optimization_run=True,
        )
        assert report.is_tainted is True
        assert "TAINTED" in report.taint_reason

    def test_normal_oos_run_is_clean(self):
        candles = make_trending_candles(50, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        oos_window = PeriodWindow(
            period_type=EvaluationPeriodType.OUT_OF_SAMPLE,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )
        report = validator.run_period_backtest(
            candles=candles,
            period_window=oos_window,
            is_optimization_run=False,
        )
        assert report.is_tainted is False


# ---------------------------------------------------------------------------
# 6. Period Performance Reporting and Versioning
# ---------------------------------------------------------------------------


class TestPeriodPerformance:
    def test_independent_period_report_with_versioning(self):
        candles = make_trending_candles(30, datetime(2025, 1, 1, tzinfo=timezone.utc))
        cfg_snap = {"model": "gpt-4.1", "temperature": 0.7}
        validator = ForexWalkForwardValidator(
            strategy_version="v2.1.0",
            config_snapshot=cfg_snap,
        )
        dev_window = PeriodWindow(
            period_type=EvaluationPeriodType.DEVELOPMENT,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )
        report = validator.run_period_backtest(
            candles=candles,
            period_window=dev_window,
        )
        assert report.strategy_version == "v2.1.0"
        assert report.config_snapshot == cfg_snap
        assert report.config_hash == compute_config_hash(cfg_snap)
        assert report.period_type == EvaluationPeriodType.DEVELOPMENT

    def test_report_to_dict_serializes(self):
        candles = make_trending_candles(30, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator(strategy_version="v1.0.0")
        dev_window = PeriodWindow(
            period_type=EvaluationPeriodType.DEVELOPMENT,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )
        report = validator.run_period_backtest(candles=candles, period_window=dev_window)
        d = report.to_dict()
        assert d["period_type"] == "DEVELOPMENT"
        assert d["strategy_version"] == "v1.0.0"
        assert "metrics" in d
        assert "config_hash" in d


# ---------------------------------------------------------------------------
# 7. Full Walk-Forward Validation Pipeline
# ---------------------------------------------------------------------------


class TestFullValidation:
    def test_validate_produces_complete_report(self):
        """validate() must produce a WalkForwardValidationReport with all mandatory fields."""
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator(
            strategy_version="v1.0.0",
            config_snapshot={"model": "gpt-4.1"},
        )
        report = validator.validate(
            candles=candles,
            pair="EURUSD",
            timeframe="H1",
            n_splits=1,
        )
        assert isinstance(report, WalkForwardValidationReport)
        assert report.pair == "EURUSD"
        assert report.timeframe == "H1"
        assert len(report.splits) == 1
        assert EvaluationPeriodType.DEVELOPMENT.value in report.period_reports
        assert EvaluationPeriodType.OUT_OF_SAMPLE.value in report.period_reports
        assert report.robustness_verdict in (
            "ROBUST", "MARGINAL", "OVERFITTED", "UNAVAILABLE", "TAINTED"
        )

    def test_wfe_and_degradation_computed(self):
        """Zero-return exploratory runs must not fabricate WFE."""
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        report = validator.validate(candles=candles, pair="EURUSD", timeframe="H1")
        assert report.walk_forward_efficiency_ratio is None
        assert report.walk_forward_efficiency_status == "UNAVAILABLE_NONPOSITIVE_DEVELOPMENT_RETURN"
        assert report.robustness_verdict == "UNAVAILABLE"
        assert isinstance(report.profit_factor_degradation, float)

    @pytest.mark.parametrize("development_return", [0.0, -5.0])
    def test_nonpositive_development_return_makes_wfe_unavailable(self, development_return):
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))

        class ControlledValidator(ForexWalkForwardValidator):
            def run_period_backtest(self, *args, **kwargs):
                report = super().run_period_backtest(*args, **kwargs)
                if report.period_type == EvaluationPeriodType.DEVELOPMENT:
                    report.result.total_return_pct = development_return
                    report.result.profit_factor = 1.5
                elif report.period_type == EvaluationPeriodType.OUT_OF_SAMPLE:
                    report.result.total_return_pct = 5.0
                    report.result.profit_factor = 1.4
                    report.result.expectancy_r = 0.2
                return report

        report = ControlledValidator().validate(candles, include_forward_demo=False)
        assert report.walk_forward_efficiency_ratio is None
        assert report.walk_forward_efficiency_status == "UNAVAILABLE_NONPOSITIVE_DEVELOPMENT_RETURN"
        assert report.robustness_verdict == "UNAVAILABLE"
        assert "Unavailable" in report.markdown_summary

    def test_positive_development_return_produces_descriptive_wfe(self):
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))

        class ControlledValidator(ForexWalkForwardValidator):
            def run_period_backtest(self, *args, **kwargs):
                report = super().run_period_backtest(*args, **kwargs)
                if report.period_type == EvaluationPeriodType.DEVELOPMENT:
                    report.result.total_return_pct = 10.0
                    report.result.profit_factor = 1.5
                elif report.period_type == EvaluationPeriodType.OUT_OF_SAMPLE:
                    report.result.total_return_pct = 7.0
                    report.result.profit_factor = 1.4
                    report.result.expectancy_r = 0.2
                return report

        report = ControlledValidator().validate(candles, include_forward_demo=False)
        assert report.walk_forward_efficiency_ratio == pytest.approx(0.7)
        assert report.walk_forward_efficiency_status == "AVAILABLE"
        assert report.robustness_verdict == "ROBUST"
        assert "not statistical validation" in report.markdown_summary

    def test_markdown_summary_rendered(self):
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator(strategy_version="v1.0.0")
        report = validator.validate(candles=candles, pair="EURUSD", timeframe="H1")
        assert "Walk-Forward" in report.markdown_summary
        assert "EURUSD" in report.markdown_summary

    def test_to_dict_serializes(self):
        candles = make_trending_candles(100, datetime(2025, 1, 1, tzinfo=timezone.utc))
        validator = ForexWalkForwardValidator()
        report = validator.validate(candles=candles, pair="EURUSD", timeframe="H1")
        d = report.to_dict()
        assert "validation_id" in d
        assert "splits" in d
        assert "period_reports" in d
        assert "robustness_verdict" in d

    def test_validate_with_agent_pipeline(self):
        """validate() should accept an agent_pipeline_callable to produce proposals."""
        candles = make_trending_candles(60, datetime(2025, 1, 1, tzinfo=timezone.utc))

        call_count = 0

        def simple_agent(pair, ts, pit_candles):
            nonlocal call_count
            call_count += 1
            if len(pit_candles) > 3:
                return ForexTraderProposal(
                    pair=pair,
                    action=ForexAction.LONG,
                    order_type=OrderType.MARKET,
                    stop_loss=pit_candles[-1].low - 0.0020,
                    take_profit_1=pit_candles[-1].high + 0.0020,
                    reasoning="Walk-forward test agent",
                )
            return None

        validator = ForexWalkForwardValidator()
        report = validator.validate(
            candles=candles,
            pair="EURUSD",
            timeframe="H1",
            agent_pipeline_callable=simple_agent,
        )
        assert isinstance(report, WalkForwardValidationReport)
        assert call_count > 0  # Agent was invoked


@pytest.mark.parametrize(
    ("order_type", "entry_price"),
    [
        (OrderType.MARKET, None),
        (OrderType.BUY_LIMIT, 1.1050),
        (OrderType.BUY_STOP, 1.1160),
    ],
)
def test_completed_bar_decision_never_executes_retroactively(order_type, entry_price):
    """Close-known OHLC cannot produce an earlier same-bar fill."""
    start = datetime(2025, 1, 1, 9, tzinfo=timezone.utc)
    decision_bar = ForexBar(
        timestamp=start,
        close_time=start + timedelta(hours=1),
        open=1.1000,
        high=1.1200,
        low=1.0990,
        close=1.1150,
        volume=100,
        is_closed=True,
    )
    next_bar = ForexBar(
        timestamp=start + timedelta(hours=1),
        close_time=start + timedelta(hours=2),
        open=1.1150,
        high=1.1180,
        low=1.1040,
        close=1.1170,
        volume=100,
        is_closed=True,
    )
    seen = []

    def agent(pair, cutoff, history):
        seen.append((cutoff, max(bar.close_time or bar.timestamp for bar in history)))
        return ForexTraderProposal(
            pair=pair,
            action=ForexAction.LONG,
            order_type=order_type,
            entry_price=entry_price,
            stop_loss=1.0900,
            take_profit_1=1.1300,
            suggested_lot_size=0.01,
            reasoning="Causality regression",
        )

    report = ForexWalkForwardValidator(config_snapshot={"max_analysis_points": 1}).run_period_backtest(
        [decision_bar, next_bar],
        PeriodWindow(EvaluationPeriodType.OUT_OF_SAMPLE, start, next_bar.timestamp),
        backtest_config=ForexBacktestConfig(
            default_spread_pips=0,
            default_slippage_pips=0,
            commission_per_lot_usd=0,
        ),
        agent_pipeline_callable=agent,
    )

    assert all(last_information <= decision for decision, last_information in seen)
    assert report.result.trades
    trade = report.result.trades[0]
    decision_time = decision_bar.close_time
    assert trade.entry_time >= decision_time
    if order_type == OrderType.MARKET:
        assert trade.entry_price == decision_bar.close
    else:
        assert trade.entry_time == next_bar.timestamp


def test_period_warmup_is_context_only_and_never_traded():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = [
        ForexBar(
            timestamp=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            open=1.1,
            high=1.11,
            low=1.09,
            close=1.1,
            is_closed=True,
        )
        for index in range(4)
    ]
    histories = []

    def no_trade(_pair, _cutoff, history):
        histories.append(list(history))
        return None

    ForexWalkForwardValidator(config_snapshot={"max_analysis_points": 1}).run_period_backtest(
        bars,
        PeriodWindow(EvaluationPeriodType.OUT_OF_SAMPLE, bars[2].timestamp, bars[3].timestamp),
        agent_pipeline_callable=no_trade,
    )
    assert [bar.timestamp for bar in histories[0]] == [bar.timestamp for bar in bars[:3]]
