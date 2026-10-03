"""Deterministic, graph-aware LLM cost estimates for Forex validation workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PricingStatus = Literal["AVAILABLE", "UNAVAILABLE"]


@dataclass(frozen=True)
class ModelPricing:
    """Pinned per-million-token pricing used only for pre-run estimates."""

    provider: str
    model: str
    input_usd_per_million: float | None
    output_usd_per_million: float | None
    status: PricingStatus
    source: str


@dataclass(frozen=True)
class CallBreakdown:
    """Expected graph calls for one analysis point."""

    analyst_calls: int
    bull_calls: int
    bear_calls: int
    research_manager_calls: int
    trader_calls: int
    risk_debate_calls: int
    risk_manager_calls: int
    portfolio_manager_calls: int
    memory_calls: int
    deterministic_risk_calls: int = 0

    @property
    def quick_model_calls(self) -> int:
        return self.analyst_calls + self.bull_calls + self.bear_calls + self.trader_calls

    @property
    def deep_model_calls(self) -> int:
        return self.research_manager_calls + self.portfolio_manager_calls

    @property
    def total_llm_calls(self) -> int:
        return self.quick_model_calls + self.deep_model_calls + self.risk_debate_calls + self.risk_manager_calls


@dataclass(frozen=True)
class AgentBacktestEstimate:
    """Typed pre-launch estimate; never contains actual provider usage."""

    total_bars: int
    sampling_interval: int
    max_analysis_points: int | None
    expected_analyses_count: int
    workflow: str
    workflow_multiplier: int
    analyst_count: int
    debate_rounds: int
    debate_enabled: bool
    memory_enabled: bool
    quick_model: str
    deep_model: str
    calls_per_analysis: CallBreakdown
    estimated_llm_calls: int
    estimated_quick_model_calls: int
    estimated_deep_model_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_tokens: int
    pricing_status: PricingStatus
    pricing: tuple[ModelPricing, ...]
    estimated_cost_usd: float | None
    estimated_cost_low_usd: float | None
    estimated_cost_high_usd: float | None
    estimate_kind: Literal["ESTIMATE"] = "ESTIMATE"
    actual_usage: None = None
    disclosures: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["calls_per_analysis"]["quick_model_calls"] = self.calls_per_analysis.quick_model_calls
        result["calls_per_analysis"]["deep_model_calls"] = self.calls_per_analysis.deep_model_calls
        result["calls_per_analysis"]["total_llm_calls"] = self.calls_per_analysis.total_llm_calls
        result["pricing"] = [asdict(item) for item in self.pricing]
        # Compatibility with existing clients while making estimate/actual explicit.
        result["multiplier"] = self.workflow_multiplier
        result["cost_kind"] = self.estimate_kind
        return result


# Pinned estimates, not a live pricing feed. Unknown models must remain unavailable.
DEFAULT_PRICING: dict[tuple[str, str], ModelPricing] = {
    ("openai", "gpt-4.1-mini"): ModelPricing(
        provider="openai",
        model="gpt-4.1-mini",
        input_usd_per_million=0.40,
        output_usd_per_million=1.60,
        status="AVAILABLE",
        source="https://developers.openai.com/api/docs/models/gpt-4.1-mini (verified 2026-10-02)",
    ),
    ("openai", "gpt-4.1"): ModelPricing(
        provider="openai",
        model="gpt-4.1",
        input_usd_per_million=2.00,
        output_usd_per_million=8.00,
        status="AVAILABLE",
        source="https://developers.openai.com/api/docs/models/gpt-4.1 (verified 2026-10-02)",
    ),
}


def _pricing_for(
    provider: str,
    model: str,
    catalog: dict[tuple[str, str], ModelPricing],
) -> ModelPricing:
    key = (provider.strip().lower(), model.strip().lower())
    price = catalog.get(key)
    if price is not None:
        return price
    return ModelPricing(
        provider=provider,
        model=model,
        input_usd_per_million=None,
        output_usd_per_million=None,
        status="UNAVAILABLE",
        source="No pinned pricing entry; consult the provider's current pricing",
    )


def estimate_agent_analyses(
    total_bars: int,
    sampling_interval: int = 1,
    max_analysis_points: int | None = None,
    analyst_count: int = 3,
    *,
    provider: str = "openai",
    quick_model: str = "gpt-4.1-mini",
    deep_model: str = "gpt-4.1",
    debate_rounds: int = 1,
    debate_enabled: bool = True,
    memory_enabled: bool = True,
    risk_debate_rounds: int = 0,
    workflow: str = "BACKTEST",
    workflow_multiplier: int = 1,
    quick_input_tokens_per_call: int = 900,
    quick_output_tokens_per_call: int = 300,
    deep_input_tokens_per_call: int = 1600,
    deep_output_tokens_per_call: int = 600,
    pricing_catalog: dict[tuple[str, str], ModelPricing] | None = None,
    avg_tokens_per_analysis: int | None = None,
    cost_per_1k_tokens: float | None = None,
) -> AgentBacktestEstimate:
    """Estimate the actual Forex graph shape without counting deterministic nodes.

    ``avg_tokens_per_analysis`` and ``cost_per_1k_tokens`` are accepted only for
    source compatibility. They no longer override graph-aware token or model
    pricing calculations.
    """

    del avg_tokens_per_analysis, cost_per_1k_tokens
    interval = max(1, int(sampling_interval))
    raw_points = max(0, int(total_bars)) // interval
    expected_per_workflow = (
        min(raw_points, max_analysis_points)
        if max_analysis_points is not None and max_analysis_points > 0
        else raw_points
    )
    multiplier = max(1, int(workflow_multiplier))
    expected = expected_per_workflow * multiplier
    rounds = max(0, int(debate_rounds)) if debate_enabled else 0
    risk_rounds = max(0, int(risk_debate_rounds))

    # Forex risk evaluation is deterministic. Risk LLM fields remain explicit
    # zeros so it cannot be silently counted as a model call.
    calls = CallBreakdown(
        analyst_calls=max(1, int(analyst_count)),
        bull_calls=rounds,
        bear_calls=rounds,
        research_manager_calls=1,
        trader_calls=1,
        risk_debate_calls=risk_rounds * 3,
        risk_manager_calls=1 if risk_rounds else 0,
        portfolio_manager_calls=1,
        memory_calls=0,
        deterministic_risk_calls=0,
    )
    quick_calls = expected * calls.quick_model_calls
    deep_calls = expected * (calls.deep_model_calls + calls.risk_debate_calls + calls.risk_manager_calls)
    total_calls = quick_calls + deep_calls
    input_tokens = quick_calls * max(0, quick_input_tokens_per_call) + deep_calls * max(0, deep_input_tokens_per_call)
    output_tokens = quick_calls * max(0, quick_output_tokens_per_call) + deep_calls * max(0, deep_output_tokens_per_call)

    catalog = pricing_catalog or DEFAULT_PRICING
    quick_price = _pricing_for(provider, quick_model, catalog)
    deep_price = _pricing_for(provider, deep_model, catalog)
    pricing = (quick_price, deep_price)
    pricing_available = all(item.status == "AVAILABLE" for item in pricing)
    cost = None
    low = None
    high = None
    if pricing_available:
        quick_cost = (
            quick_calls * quick_input_tokens_per_call * float(quick_price.input_usd_per_million) / 1_000_000
            + quick_calls * quick_output_tokens_per_call * float(quick_price.output_usd_per_million) / 1_000_000
        )
        deep_cost = (
            deep_calls * deep_input_tokens_per_call * float(deep_price.input_usd_per_million) / 1_000_000
            + deep_calls * deep_output_tokens_per_call * float(deep_price.output_usd_per_million) / 1_000_000
        )
        cost = round(quick_cost + deep_cost, 6)
        low = round(cost * 0.75, 6)
        high = round(cost * 1.50, 6)

    return AgentBacktestEstimate(
        total_bars=max(0, int(total_bars)),
        sampling_interval=interval,
        max_analysis_points=max_analysis_points,
        expected_analyses_count=expected,
        workflow=workflow,
        workflow_multiplier=multiplier,
        analyst_count=max(1, int(analyst_count)),
        debate_rounds=rounds,
        debate_enabled=debate_enabled,
        memory_enabled=memory_enabled,
        quick_model=quick_model,
        deep_model=deep_model,
        calls_per_analysis=calls,
        estimated_llm_calls=total_calls,
        estimated_quick_model_calls=quick_calls,
        estimated_deep_model_calls=deep_calls,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_tokens=input_tokens + output_tokens,
        pricing_status="AVAILABLE" if pricing_available else "UNAVAILABLE",
        pricing=pricing,
        estimated_cost_usd=cost,
        estimated_cost_low_usd=low,
        estimated_cost_high_usd=high,
        disclosures=(
            "This is a pre-run estimate, not actual provider-reported usage.",
            "Retries, tool loops, caching, prompt size, and provider price changes can alter actual cost.",
            "Retries are not included as guaranteed calls.",
            "Memory retrieval and deterministic risk/sizing are not counted as LLM calls.",
            "Actual usage is recorded separately from provider response metadata when available.",
        ),
    )
