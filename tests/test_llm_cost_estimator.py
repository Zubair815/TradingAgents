from tradingagents.backtest.agent_backtester import (
    AgentBacktestConfig,
    HistoricalForexAgentBacktester,
)
from tradingagents.backtest.cost_estimator import estimate_agent_analyses


def test_debate_rounds_change_bull_bear_and_model_calls():
    standard = estimate_agent_analyses(10, debate_rounds=1)
    deep = estimate_agent_analyses(10, debate_rounds=3)

    assert standard.calls_per_analysis.bull_calls == 1
    assert standard.calls_per_analysis.bear_calls == 1
    assert deep.calls_per_analysis.bull_calls == 3
    assert deep.calls_per_analysis.bear_calls == 3
    assert deep.estimated_llm_calls - standard.estimated_llm_calls == 40


def test_analyst_and_debate_configuration_change_only_real_llm_nodes():
    one = estimate_agent_analyses(5, analyst_count=1, debate_enabled=False)
    all_analysts = estimate_agent_analyses(5, analyst_count=3, debate_enabled=True)

    assert one.calls_per_analysis.total_llm_calls == 4
    assert all_analysts.calls_per_analysis.total_llm_calls == 8
    assert one.calls_per_analysis.deterministic_risk_calls == 0
    assert one.calls_per_analysis.risk_manager_calls == 0


def test_memory_does_not_inflate_llm_calls():
    enabled = estimate_agent_analyses(10, memory_enabled=True)
    disabled = estimate_agent_analyses(10, memory_enabled=False)

    assert enabled.calls_per_analysis.memory_calls == 0
    assert enabled.estimated_llm_calls == disabled.estimated_llm_calls
    assert enabled.estimated_tokens == disabled.estimated_tokens


def test_quick_and_deep_models_have_separate_calls_tokens_and_prices():
    estimate = estimate_agent_analyses(2)

    assert estimate.estimated_quick_model_calls == 12
    assert estimate.estimated_deep_model_calls == 4
    assert estimate.estimated_input_tokens == 17_200
    assert estimate.estimated_output_tokens == 6_000
    assert [item.model for item in estimate.pricing] == ["gpt-4.1-mini", "gpt-4.1"]
    assert estimate.pricing_status == "AVAILABLE"
    assert estimate.estimated_cost_low_usd < estimate.estimated_cost_usd
    assert estimate.estimated_cost_high_usd > estimate.estimated_cost_usd


def test_unknown_model_pricing_is_unavailable_never_zero():
    estimate = estimate_agent_analyses(
        10,
        provider="unknown-provider",
        quick_model="unknown-quick",
        deep_model="unknown-deep",
    )

    assert estimate.pricing_status == "UNAVAILABLE"
    assert estimate.estimated_cost_usd is None
    assert estimate.estimated_cost_low_usd is None
    assert estimate.estimated_cost_high_usd is None


def test_workflow_multiplier_and_analysis_cap_are_applied_once():
    estimate = estimate_agent_analyses(
        total_bars=200,
        sampling_interval=4,
        max_analysis_points=25,
        workflow="ABLATION",
        workflow_multiplier=4,
    )

    assert estimate.expected_analyses_count == 100
    assert estimate.estimated_llm_calls == 800
    assert estimate.workflow == "ABLATION"
    assert estimate.workflow_multiplier == 4


def test_estimate_and_actual_usage_are_explicitly_separate():
    payload = estimate_agent_analyses(1).to_dict()

    assert payload["estimate_kind"] == "ESTIMATE"
    assert payload["actual_usage"] is None
    assert any("Retries are not included" in item for item in payload["disclosures"])
    assert any("Actual usage is recorded separately" in item for item in payload["disclosures"])


def test_risk_debate_is_explicit_when_requested_but_forex_default_is_zero():
    default = estimate_agent_analyses(1)
    configured = estimate_agent_analyses(1, risk_debate_rounds=2)

    assert default.calls_per_analysis.risk_debate_calls == 0
    assert default.calls_per_analysis.risk_manager_calls == 0
    assert configured.calls_per_analysis.risk_debate_calls == 6
    assert configured.calls_per_analysis.risk_manager_calls == 1


def test_backtester_estimate_uses_its_models_and_research_depth():
    backtester = HistoricalForexAgentBacktester(
        AgentBacktestConfig(
            provider="unsupported",
            quick_model="quick-x",
            deep_model="deep-x",
            research_depth="deep",
        )
    )

    estimate = backtester.estimate(2)

    assert estimate.quick_model == "quick-x"
    assert estimate.deep_model == "deep-x"
    assert estimate.debate_rounds == 3
    assert estimate.pricing_status == "UNAVAILABLE"
