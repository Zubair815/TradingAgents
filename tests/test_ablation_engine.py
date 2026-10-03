"""Institutional Verification Suite for Forex Multi-Agent Ablation Testing (Phase 22).

Validates:
1. Standard ablation matrix composition:
   - Technical only, Tech+Macro, Tech+News, Tech+Macro+News
   - With debate vs Without debate
   - With memory vs Without memory
   - Quick Model A vs Quick Model B
2. Execution over identical candle sequences (PIT safe).
3. Metric computation:
   - Expectancy (R), Profit factor, Drawdown, Average R, Trade count, LLM cost, Latency.
   - Relative deltas against baseline.
   - Component importance ranking.
4. Tiny sample size guard:
   - Refuses to declare winners when sample size is below threshold.
   - Declares winner when sample size threshold is met.
5. Injected pipeline factories and custom evaluators.
6. Serialization to dictionary / JSON.
7. Validation on empty or malformed inputs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
)
from tradingagents.backtest.ablation import (
    AblationConfigVariant,
    ForexAblationRunner,
    create_standard_ablation_matrix,
)
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestResult,
)
from tradingagents.dataflows.forex_data import ForexBar

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sample_candles(n: int = 100, base_price: float = 1.1000) -> list[ForexBar]:
    """Generate deterministic synthetic candles."""
    start_time = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles: list[ForexBar] = []
    p = base_price
    for i in range(n):
        t = start_time + timedelta(hours=i)
        # Gentle oscillation
        change = 0.0008 if i % 2 == 0 else -0.0006
        open_p = p
        close_p = p + change
        high_p = max(open_p, close_p) + 0.0005
        low_p = min(open_p, close_p) - 0.0005
        candles.append(
            ForexBar(
                timestamp=t,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=1000.0,
            )
        )
        p = close_p
    return candles


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_standard_ablation_matrix_composition() -> None:
    """Matrix contains all required Phase 22 dimensions."""
    matrix = create_standard_ablation_matrix(
        quick_model_a="gpt-4.1-mini",
        quick_model_b="claude-3-5-haiku",
    )
    v_ids = {v.variant_id for v in matrix}

    # Baseline
    assert "baseline_full" in v_ids
    # Analyst selection
    assert "tech_only" in v_ids
    assert "tech_macro" in v_ids
    assert "tech_news" in v_ids
    assert "tech_macro_news" in v_ids
    # Debate
    assert "with_debate" in v_ids
    assert "without_debate" in v_ids
    # Memory
    assert "with_memory" in v_ids
    assert "without_memory" in v_ids
    # Models
    assert "model_quick_a" in v_ids
    assert "model_quick_b" in v_ids

    # Specific flag checks
    tech_only = next(v for v in matrix if v.variant_id == "tech_only")
    assert tech_only.analyst_selection == ["forex_technical"]

    without_debate = next(v for v in matrix if v.variant_id == "without_debate")
    assert without_debate.enable_debate is False

    without_memory = next(v for v in matrix if v.variant_id == "without_memory")
    assert without_memory.enable_memory is False

    model_b = next(v for v in matrix if v.variant_id == "model_quick_b")
    assert "claude" in model_b.quick_model


def test_ablation_variant_calls_estimation() -> None:
    """Verifies LLM API calls estimation formula per evaluation point."""
    full = AblationConfigVariant(
        variant_id="test_full",
        name="Test Full",
        description="test",
        analyst_selection=["forex_technical", "forex_macro", "forex_news"],
        enable_debate=True,
        enable_memory=True,
    )
    # 3 analysts + 2 debate + manager + trader + portfolio manager = 8.
    # Memory retrieval is deterministic and does not add an LLM call.
    assert full.estimate_calls_per_point() == 8

    minimal = AblationConfigVariant(
        variant_id="test_min",
        name="Test Min",
        description="test",
        analyst_selection=["forex_technical"],
        enable_debate=False,
        enable_memory=False,
    )
    # 1 analyst + manager + trader + portfolio manager = 4.
    assert minimal.estimate_calls_per_point() == 4


def test_ablation_empty_candles_raises() -> None:
    """Ablation runner rejects empty candle sequence."""
    runner = ForexAblationRunner(pair="EURUSD")
    with pytest.raises(ValueError, match="No historical candles"):
        runner.run_ablation([])


def test_ablation_empty_variants_raises() -> None:
    """Ablation runner rejects empty variant list."""
    candles = _make_sample_candles(20)
    runner = ForexAblationRunner(pair="EURUSD")
    with pytest.raises(ValueError, match="No ablation variants specified"):
        runner.run_ablation(candles, variants=[])


def test_ablation_tiny_sample_guards_against_winner_declaration() -> None:
    """Enforces rule: 'Do not declare a winner based on tiny samples'."""
    candles = _make_sample_candles(40)
    runner = ForexAblationRunner(
        pair="EURUSD",
        min_sample_size=30,  # Requiring 30 trades for valid winner
    )

    variants = [
        AblationConfigVariant(
            variant_id="baseline_full",
            name="Full System",
            description="baseline",
            sampling_interval=5,
        ),
        AblationConfigVariant(
            variant_id="tech_only",
            name="Technical Only",
            description="ablation",
            sampling_interval=5,
        ),
    ]

    report = runner.run_ablation(candles, variants=variants)

    # 40 candles with step 8 generates fewer than 10 trades
    assert all(v.trade_count < 30 for v in report.variants)
    assert report.winner_declared is False
    assert report.winning_variant_id is None
    assert report.sample_size_warning is not None
    assert "below statistical significance threshold" in report.sample_size_warning
    assert "STATISTICAL GUARD" in report.markdown_summary
    assert "NO WINNER DECLARED" in report.markdown_summary


def test_ablation_adequate_sample_declares_winner() -> None:
    """When sample size reaches significance threshold, winner is declared."""
    candles = _make_sample_candles(30)
    runner = ForexAblationRunner(pair="EURUSD", min_sample_size=10)

    # Use a custom evaluator that simulates adequate trades
    def mock_evaluator(
        variant: AblationConfigVariant,
        c_list: list[ForexBar],
        _lower: list[ForexBar] | None,
    ) -> tuple[ForexBacktestResult, int, float]:
        cfg = ForexBacktestConfig(initial_balance=10000.0)
        res = ForexBacktestResult(
            config=cfg,
            start_date="2025-01-01",
            end_date="2025-01-02",
            initial_balance=10000.0,
            final_balance=10500.0 if variant.variant_id == "high_alpha" else 10100.0,
            final_equity=10500.0,
            total_net_profit=500.0 if variant.variant_id == "high_alpha" else 100.0,
            total_return_pct=5.0 if variant.variant_id == "high_alpha" else 1.0,
            total_trades=20,  # >= min_sample_size 10
            winning_trades=14 if variant.variant_id == "high_alpha" else 10,
            losing_trades=6,
            breakeven_trades=0,
            win_rate_pct=70.0 if variant.variant_id == "high_alpha" else 50.0,
            loss_rate_pct=30.0,
            gross_profit=700.0,
            gross_loss=200.0,
            profit_factor=3.5 if variant.variant_id == "high_alpha" else 1.2,
            avg_r_multiple=0.8,
            expectancy_r=0.45 if variant.variant_id == "high_alpha" else 0.05,
            expectancy_cash=25.0,
            avg_trade_pips=12.0,
            total_pips=240.0,
            max_drawdown_cash=150.0,
            max_drawdown_pct=1.5,
            sharpe_ratio=2.1,
            sortino_ratio=2.8,
            total_spread_cost_usd=10.0,
            total_slippage_cost_usd=5.0,
            total_commission_cost_usd=10.0,
        )
        return res, 20, 0.15

    variants = [
        AblationConfigVariant(
            variant_id="baseline_full",
            name="Baseline",
            description="base",
        ),
        AblationConfigVariant(
            variant_id="high_alpha",
            name="High Alpha Variant",
            description="best variant",
        ),
    ]

    report = runner.run_ablation(
        candles,
        variants=variants,
        custom_variant_evaluator=mock_evaluator,
    )

    assert report.winner_declared is True
    assert report.winning_variant_id == "high_alpha"
    assert report.winning_variant_name == "High Alpha Variant"
    assert report.sample_size_warning is None
    assert "**Highest scoring variant in this sample:** **High Alpha Variant**" in report.markdown_summary
    assert "statistical significance not established" in report.markdown_summary
    assert report.to_dict()["validated_strategy_performance"] is False


def test_ablation_pipeline_factory_invocation() -> None:
    """Verifies that pipeline_factory creates variant-specific callables."""
    candles = _make_sample_candles(25)
    runner = ForexAblationRunner(pair="EURUSD", min_sample_size=5)

    created_variants: list[str] = []

    def factory(variant: AblationConfigVariant):
        created_variants.append(variant.variant_id)

        def pipeline(pair: str, ts: datetime, history: list[ForexBar]):
            # Return valid proposal on 5th bar
            if len(history) % 5 == 0:
                return ForexTraderProposal(
                    proposal_id=f"p_{variant.variant_id}_{len(history)}",
                    pair=pair,
                    action=ForexAction.LONG,
                    order_type=OrderType.MARKET,
                    entry_price=history[-1].close,
                    stop_loss=history[-1].close - 0.0020,
                    take_profit=history[-1].close + 0.0040,
                    risk_reward_ratio=2.0,
                    risk_percent=1.0,
                    setup_type="TEST",
                    execution_timeframe="H1",
                    confidence=70.0,
                    reasoning="Test reasoning",
                    risk_action=ForexRiskDecisionAction.APPROVE,
                    approved=True,
                )
            return None

        return pipeline

    variants = [
        AblationConfigVariant(
            variant_id="var_a",
            name="Variant A",
            description="var a",
            sampling_interval=1,
        ),
        AblationConfigVariant(
            variant_id="var_b",
            name="Variant B",
            description="var b",
            sampling_interval=1,
        ),
    ]

    report = runner.run_ablation(
        candles,
        variants=variants,
        baseline_variant_id="var_a",
        pipeline_factory=factory,
    )

    assert "var_a" in created_variants
    assert "var_b" in created_variants
    assert len(report.variants) == 2


def test_ablation_metrics_delta_and_importance() -> None:
    """Verifies deltas and component importance calculations."""
    candles = _make_sample_candles(20)
    runner = ForexAblationRunner(pair="EURUSD", min_sample_size=5)

    def mock_evaluator(
        variant: AblationConfigVariant,
        _c: list[ForexBar],
        _lower: list[ForexBar] | None,
    ) -> tuple[ForexBacktestResult, int, float]:
        cfg = ForexBacktestConfig()
        is_base = variant.variant_id == "base"
        res = ForexBacktestResult(
            config=cfg,
            start_date="2025-01-01",
            end_date="2025-01-02",
            initial_balance=10000.0,
            final_balance=10800.0 if is_base else 10200.0,
            final_equity=10800.0 if is_base else 10200.0,
            total_net_profit=800.0 if is_base else 200.0,
            total_return_pct=8.0 if is_base else 2.0,
            total_trades=10,
            winning_trades=7 if is_base else 5,
            losing_trades=3 if is_base else 5,
            breakeven_trades=0,
            win_rate_pct=70.0 if is_base else 50.0,
            loss_rate_pct=30.0,
            gross_profit=1000.0 if is_base else 500.0,
            gross_loss=200.0 if is_base else 300.0,
            profit_factor=5.0 if is_base else 1.67,
            avg_r_multiple=0.75 if is_base else 0.20,
            expectancy_r=0.40 if is_base else 0.10,
            expectancy_cash=80.0 if is_base else 20.0,
            avg_trade_pips=15.0,
            total_pips=150.0,
            max_drawdown_cash=200.0 if is_base else 500.0,
            max_drawdown_pct=2.0 if is_base else 5.0,
            sharpe_ratio=2.5 if is_base else 1.0,
            sortino_ratio=3.0,
            total_spread_cost_usd=10.0,
            total_slippage_cost_usd=5.0,
            total_commission_cost_usd=10.0,
        )
        return res, 10, 0.05 if is_base else 0.02

    variants = [
        AblationConfigVariant(variant_id="base", name="Base", description="base"),
        AblationConfigVariant(variant_id="ablated", name="Ablated", description="ablated"),
    ]

    report = runner.run_ablation(
        candles,
        variants=variants,
        baseline_variant_id="base",
        custom_variant_evaluator=mock_evaluator,
    )

    abl_m = next(m for m in report.variants if m.variant_id == "ablated")
    # delta = variant - base
    assert abl_m.delta_expectancy == pytest.approx(0.10 - 0.40, rel=1e-2)
    assert abl_m.delta_drawdown == pytest.approx(5.0 - 2.0, rel=1e-2)
    assert abl_m.delta_net_profit == pytest.approx(200.0 - 800.0, rel=1e-2)

    # Component importance: Base Expectancy (0.40) - Ablated Expectancy (0.10) + 0.05*(5.0-2.0)
    # 0.30 + 0.15 = 0.45
    assert len(report.component_rankings) == 1
    comp_name, score = report.component_rankings[0]
    assert comp_name == "Ablated"
    assert score == pytest.approx(0.45, rel=1e-2)


def test_ablation_report_to_dict_serialization() -> None:
    """Report and metric objects serialize cleanly to dict."""
    candles = _make_sample_candles(20)
    runner = ForexAblationRunner(pair="EURUSD", min_sample_size=5)

    variants = [
        AblationConfigVariant(variant_id="base", name="Base", description="base"),
    ]

    report = runner.run_ablation(candles, variants=variants)
    d = report.to_dict()

    assert d["pair"] == "EURUSD"
    assert d["baseline_variant_id"] == "base"
    assert isinstance(d["variants"], list)
    assert len(d["variants"]) == 1
    assert "trade_count" in d["variants"][0]
    assert "delta_expectancy" in d["variants"][0]
    assert "markdown_summary" in d
