"""Forex Technical Analyst Agent (Phase 6).

Specialized institutional Forex technical analyst that synthesizes:
1. Multi-Timeframe Market Structure (D1/H4 macro trend, H1 intermediate swing, M15 entry structure).
2. Price Action Footprints (Higher-Highs / Higher-Lows, BOS continuation, CHoCH reversal, FVGs, Order Blocks).
3. Quantitative Indicators (EMA stacks: 8, 21, 34, 50, 200; Wilder RSI; MACD; ADX trend strength; Bollinger Bands).
4. Volatility & Transaction Cost Drag (ATR in pips, Spread/ATR friction ratio, favorable cost filter).
5. Trading Session Context (active sessions, London-NY overlap, prime pair liquidity).

Outputs:
- Rich markdown narrative report assigned to ``market_report`` and ``forex_technical_report``.
- Structured Pydantic assessment schema (``ForexTechnicalAssessment``).
- Deterministic report generator (``generate_deterministic_forex_technical_report``) for testing and zero-cost baseline.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.forex_tools import (
    get_forex_candles_tool,
    get_forex_indicators_tool,
    get_market_structure_tool,
    get_multi_timeframe_data_tool,
    get_multi_timeframe_indicators_tool,
    get_multi_timeframe_market_structure_tool,
)
from tradingagents.dataflows.forex_data import resolve_timeframe
from tradingagents.forex.domain import Timeframe, get_forex_pair
from tradingagents.forex.indicators import (
    compute_multi_timeframe_indicators,
    format_multi_timeframe_indicators_summary,
)
from tradingagents.forex.market_structure import (
    analyze_multi_timeframe_structure,
    format_multi_timeframe_structure_summary,
)
from tradingagents.forex.sessions import (
    get_active_sessions_for_pair,
    is_market_open,
    is_pair_in_prime_session,
)
from tradingagents.llm_clients.base_client import normalize_content

if TYPE_CHECKING:
    from tradingagents.dataflows.forex_data import MultiTimeframeData

logger = logging.getLogger(__name__)

# Standard toolset exposed to the Forex Technical Analyst
FOREX_TECHNICAL_TOOLS = [
    get_forex_candles_tool,
    get_multi_timeframe_data_tool,
    get_forex_indicators_tool,
    get_multi_timeframe_indicators_tool,
    get_market_structure_tool,
    get_multi_timeframe_market_structure_tool,
]


# ---------------------------------------------------------------------------
# Structured Pydantic Assessment Schema
# ---------------------------------------------------------------------------


class ForexTechnicalAssessment(BaseModel):
    """Structured quantitative and price-action technical assessment."""

    pair: str = Field(description="Forex pair symbol, e.g. EURUSD")
    bias: Literal["LONG", "SHORT", "NEUTRAL", "NO_TRADE"] = Field(
        description="Directional technical bias"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    market_regime: str = Field(
        description="Current market regime: TRENDING_BULLISH, TRENDING_BEARISH, RANGING, CHOPPY, VOLATILE"
    )
    macro_trend: str = Field(
        description="Higher-timeframe macro trend summary (D1/H4)"
    )
    tactical_structure: str = Field(
        description="Tactical execution structure summary (H1/M15)"
    )
    nearest_support: float | None = Field(
        default=None, description="Nearest key support level price"
    )
    nearest_resistance: float | None = Field(
        default=None, description="Nearest key resistance level price"
    )
    support_distance_pips: float | None = Field(
        default=None, description="Distance to nearest support in pips"
    )
    resistance_distance_pips: float | None = Field(
        default=None, description="Distance to nearest resistance in pips"
    )
    atr_14_pips: float | None = Field(
        default=None, description="ATR(14) in pips"
    )
    spread_atr_ratio: float | None = Field(
        default=None, description="Ratio of broker spread to ATR(14)"
    )
    is_cost_favorable: bool = Field(
        default=True,
        description="True if spread drag is acceptable relative to candle volatility",
    )
    confluence_factors: list[str] = Field(
        default_factory=list,
        description="Key technical confluence factors supporting the bias",
    )
    warning_factors: list[str] = Field(
        default_factory=list,
        description="Technical risks, warning signs, or structural friction",
    )
    invalidation_level: float | None = Field(
        default=None,
        description="Price level where the current technical thesis is invalidated",
    )
    summary_narrative: str = Field(
        description="Comprehensive institutional technical analysis narrative"
    )


# ---------------------------------------------------------------------------
# Prompt Definition
# ---------------------------------------------------------------------------


FOREX_TECHNICAL_SYSTEM_PROMPT = """You are a senior quantitative Forex Technical Analyst at an institutional FX trading desk.

Your primary mission is to conduct rigorous, multi-timeframe price action and quantitative indicator analysis on the assigned currency pair.
You base your conclusions strictly on verified empirical tool outputs: do NOT hallucinate price levels, indicator values, or market regimes.

### Core Analysis Framework:
1. **Multi-Timeframe Structure & Alignment (Top-Down)**:
   - **Macro Horizon (D1 / H4)**: Overall trend direction, position relative to 200 EMA, and major swing highs/lows.
   - **Intermediate Horizon (H1)**: Key market structure, recent Break of Structure (BOS continuation) vs Change of Character (CHoCH reversal), active Order Blocks (OBs) and Fair Value Gaps (FVGs).
   - **Tactical Horizon (M15)**: Execution timing, pullback completion, local CHoCH, and precision support/resistance boundaries.
   - Check multi-timeframe confluence: Are Macro and Tactical structures aligned (CONFLUENT_BULLISH / CONFLUENT_BEARISH), or is Tactical in a counter-trend retracement?

2. **Quantitative Indicators & Momentum**:
   - **EMA Stack Alignment**: Classify 8, 21, 34, 50, and 200 EMAs into STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, or STRONG_BEARISH.
   - **Distance to 200 EMA**: Evaluate whether price is healthy in-trend or over-extended (mean reversion risk).
   - **RSI(14)**: Identify momentum regimes (OVERBOUGHT >= 70, BULLISH 55-70, NEUTRAL 45-55, BEARISH 30-45, OVERSOLD <= 30).
   - **ADX(14) Trend Strength**: EXTREME_TREND (>=40), STRONG_TREND (25-40), or RANGING (<25).
   - **Bollinger Bands**: Observe bandwidth (%B) and band compression (squeeze) vs expansion.

3. **Transaction Cost Drag (Spread / ATR Ratio)**:
   - Always evaluate the ratio of broker spread to ATR(14) in pips.
   - If spread eats > 12-15% of the bar's expected range, issue an explicit warning regarding cost friction.

4. **Session & Liquidity Confluence**:
   - Note the current trading session (London, New York, Tokyo, Sydney) and whether current market hours represent prime liquidity for this specific pair.

### Required Output Report Structure:
Always structure your final report with these exact sections:
# Forex Technical Analysis Report: {pair}
## 1. Executive Summary & Directional Bias
- **Technical Bias**: [LONG / SHORT / NEUTRAL / NO_TRADE]
- **Conviction / Confidence**: [High (0.8-1.0) / Medium (0.5-0.79) / Low (<0.5)]
- **Market Regime**: [Trending Bullish / Trending Bearish / Ranging / Choppy / Volatile]
- **Structural Invalidation Level**: [Exact price where setup is invalidated]

## 2. Multi-Timeframe Market Structure Matrix
[Include the multi-timeframe structure table showing Trend, Swings, BOS/CHoCH, and active FVGs/OBs]

## 3. Quantitative Indicator & Volatility Matrix
[Include the multi-timeframe technical indicator table showing EMAs, RSI, ATR pips, Spread/ATR ratio, and ADX]

## 4. Key Support & Resistance Zones
- **Key Resistance**: Price level and distance in pips
- **Key Support**: Price level and distance in pips
- **Active Institutional Footprints**: Relevant unmitigated Order Blocks or Fair Value Gaps

## 5. Technical Confluence & Warning Factors
- **Bullish / Confluence Factors**: [List]
- **Bearish / Warning Factors**: [List]

## 6. Execution Guidance & Trade Location
- Detailed evaluation of whether the current location offers favorable risk-to-reward or if the trader should await a pullback/retest.
"""


# ---------------------------------------------------------------------------
# Deterministic Technical Report Generator
# ---------------------------------------------------------------------------


def generate_deterministic_forex_technical_report(
    symbol: str,
    bundle: MultiTimeframeData,
    spread_pips: float | None = None,
    execution_timeframe: Timeframe | str | None = None,
    context_timeframes: Sequence[Timeframe | str] | None = None,
) -> str:
    """Generate a 100% deterministic Forex technical report from market data.

    Computes multi-timeframe indicators, market structure, cost friction, and session
    context in pure Python with zero LLM API dependency.
    """
    pair = get_forex_pair(symbol)
    sp_pips = spread_pips if spread_pips is not None else 1.0

    # 1. Multi-Timeframe Indicators
    indicator_snapshots = compute_multi_timeframe_indicators(bundle, spread_pips=sp_pips)
    indicator_summary = format_multi_timeframe_indicators_summary(indicator_snapshots)

    # 2. Multi-Timeframe Structure
    structure_alignment = analyze_multi_timeframe_structure(
        bundle,
        lookback=2,
        lookforward=2,
        execution_timeframe=execution_timeframe,
        context_timeframes=context_timeframes,
    )
    structure_summary = format_multi_timeframe_structure_summary(structure_alignment)

    # 3. Session Context
    as_of = bundle.as_of or bundle.latest_candle_time or datetime.now(timezone.utc)
    session_open = is_market_open(as_of)
    active_sessions = get_active_sessions_for_pair(symbol, as_of) if session_open else []
    prime_session = is_pair_in_prime_session(symbol, as_of) if session_open else False

    # 4. Extract latest snapshot for executive summary
    exec_tf_resolved = resolve_timeframe(execution_timeframe) if execution_timeframe else None
    if exec_tf_resolved and exec_tf_resolved in indicator_snapshots:
        pref_tf = exec_tf_resolved
    else:
        pref_tf = (
            Timeframe.H1 if Timeframe.H1 in indicator_snapshots
            else (Timeframe.H4 if Timeframe.H4 in indicator_snapshots else list(indicator_snapshots.keys())[0] if indicator_snapshots else None)
        )
    latest_ind = indicator_snapshots.get(pref_tf) if pref_tf else None
    latest_struct = structure_alignment.structures.get(pref_tf) if pref_tf else None

    # Bias determination
    bias = "NEUTRAL"
    if structure_alignment.is_aligned or "PULLBACK" in structure_alignment.alignment_bias.value:
        bias = "LONG" if "BULLISH" in structure_alignment.alignment_bias.value else "SHORT"

    digits = pair.digits if pair else 5
    p_fmt = f"{{:.{digits}f}}"

    lines = [
        f"# Forex Technical Analysis Report: {symbol}",
        "",
        "## 1. Executive Summary & Directional Bias",
        f"- **Instrument**: {pair.symbol if pair else symbol} (Broker: {pair.broker_symbol if pair else symbol})",
        f"- **Technical Bias**: **{bias}** (Confluence: {structure_alignment.alignment_bias.value})",
        f"- **Multi-Timeframe Alignment**: {'ALIGNED ✅' if structure_alignment.is_aligned else 'COUNTER-TREND / MIXED ⚠️'}",
        f"- **Active Trading Sessions**: {', '.join(s.name for s in active_sessions) if active_sessions else 'Market Closed / Weekend'}",
        f"- **Session Prime Liquidity**: {'YES (High Volume Window) ✅' if prime_session else 'NO (Normal/Off-Peak Window) ⚠️'}",
    ]

    if latest_ind:
        lines.append(f"- **Current Close Price**: {p_fmt.format(latest_ind.close)}")
        lines.append(
            f"- **ATR(14)**: {latest_ind.atr_14_pips:.1f} pips ({latest_ind.atr_percent:.2f}%) | "
            f"Spread/ATR: {latest_ind.spread_atr_ratio:.1%} "
            f"[{'FAVORABLE COST ✅' if latest_ind.is_spread_acceptable else 'HIGH SPREAD FRICTION ⚠️'}]"
        )

    if latest_struct:
        inv_price = (
            latest_struct.nearest_support_price if bias == "LONG"
            else (latest_struct.nearest_resistance_price if bias == "SHORT" else None)
        )
        if inv_price is not None:
            lines.append(f"- **Key Technical Invalidation Level**: {p_fmt.format(inv_price)}")

    lines.extend([
        "",
        "## 2. Multi-Timeframe Market Structure",
        structure_summary,
        "",
        "## 3. Quantitative Indicators & Volatility Matrix",
        indicator_summary,
        "",
        "## 4. Key Support & Resistance Boundaries",
    ])

    if latest_struct:
        sup_str = (
            f"{p_fmt.format(latest_struct.nearest_support_price)} ({latest_struct.nearest_support_pips:.1f} pips below)"
            if latest_struct.nearest_support_price is not None and latest_struct.nearest_support_pips is not None
            else "None detected"
        )
        res_str = (
            f"{p_fmt.format(latest_struct.nearest_resistance_price)} ({latest_struct.nearest_resistance_pips:.1f} pips above)"
            if latest_struct.nearest_resistance_price is not None and latest_struct.nearest_resistance_pips is not None
            else "None detected"
        )
        lines.append(f"- **Nearest Key Support**: {sup_str}")
        lines.append(f"- **Nearest Key Resistance**: {res_str}")

        if latest_struct.active_fvgs:
            fvg_strs = [
                f"{f.fvg_type.value} [{p_fmt.format(f.bottom)} - {p_fmt.format(f.top)} | {f.size_pips:.1f}p]"
                for f in latest_struct.active_fvgs
            ]
            lines.append(f"- **Active Unmitigated FVGs**: {', '.join(fvg_strs)}")

        if latest_struct.active_order_blocks:
            ob_strs = [
                f"{ob.ob_type.value} [{p_fmt.format(ob.bottom)} - {p_fmt.format(ob.top)} | {ob.size_pips:.1f}p]"
                for ob in latest_struct.active_order_blocks
            ]
            lines.append(f"- **Active Order Blocks**: {', '.join(ob_strs)}")

    lines.extend([
        "",
        "## 5. Technical Confluence & Warning Factors",
        f"- Confluence: Macro {structure_alignment.macro_trend.value} + Tactical {structure_alignment.tactical_trend.value}",
    ])
    if latest_ind:
        lines.append(f"- Momentum: RSI={latest_ind.rsi_14:.1f} [{latest_ind.rsi_regime.value}] | ADX={latest_ind.adx_14:.1f} [{latest_ind.adx_strength.value}]")
        if not latest_ind.is_spread_acceptable:
            lines.append(f"- ⚠️ WARNING: High spread friction ({latest_ind.spread_atr_ratio:.1%} of ATR)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent Node Factory
# ---------------------------------------------------------------------------


def create_forex_technical_analyst(llm: Any, tools: Sequence[Any] | None = None):
    """Factory function returning the LangGraph node for the Forex Technical Analyst.

    Args:
        llm: Configured LangChain chat model.
        tools: Optional custom list of tools; defaults to FOREX_TECHNICAL_TOOLS.
    """
    active_tools = list(tools) if tools is not None else list(FOREX_TECHNICAL_TOOLS)

    def forex_technical_analyst_node(state: dict[str, Any]) -> dict[str, Any]:
        current_date = state.get("trade_date", "")
        instrument_context = get_instrument_context_from_state(state)
        ticker = state.get("company_of_interest", "")

        system_message = (
            FOREX_TECHNICAL_SYSTEM_PROMPT.format(pair=ticker or "the currency pair")
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a specialized institutional Forex Technical Analyst collaborating with research debaters and risk managers.\n"
                    "Use the provided tools to analyze multi-timeframe price action, market structure, and quantitative indicators.\n"
                    "Today's date is {current_date}; treat it as 'now' for all analysis and tool calls. {instrument_context}\n\n"
                    "You have access to the following tools: {tool_names}.\n\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in active_tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(active_tools)

        result = normalize_content(chain.invoke(state["messages"]))

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "market_report": report,           # Downstream researchers & managers read market_report
            "forex_technical_report": report, # Dedicated field for Forex-specific graphs
        }

    return forex_technical_analyst_node
