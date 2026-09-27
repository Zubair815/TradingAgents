"""Forex Event & News Analyst Agent (Phase 8).

Specialized institutional currency event and news strategist that evaluates:
1. Economic Calendar — Upcoming Tier-1 releases (NFP, CPI, Central Bank decisions, GDP).
2. Pre-News Blackout Windows — Spread expansion, slippage danger, and trading restrictions.
3. Economic Surprise Momentum — Recent actual vs. forecast deviations and currency momentum.
4. Breaking Currency News — Deduplicated macro headlines and central bank commentary.
5. Pair Catalysts & Confluence — Net directional event bias (Base vs. Quote).

Outputs:
- Rich markdown narrative report assigned to ``news_report`` and ``forex_news_report``.
- Structured Pydantic assessment schema (``ForexNewsAssessment``).
- Deterministic report generator (``generate_deterministic_forex_news_report``) for
  testing and zero-cost baseline.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.forex_news_tools import (
    FOREX_NEWS_TOOLS,
)
from tradingagents.forex.calendar import (
    EventRiskRegime,
    TradingActionRecommendation,
    calculate_pair_event_bias,
    evaluate_event_risk_regime,
    format_economic_calendar_table,
    get_calendar_events_for_pair,
)
from tradingagents.forex.domain import get_forex_pair
from tradingagents.llm_clients.base_client import normalize_content

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured Pydantic Assessment Schema
# ---------------------------------------------------------------------------


class ForexNewsAssessment(BaseModel):
    """Structured quantitative and qualitative currency news & event assessment."""

    pair: str = Field(description="Forex pair symbol, e.g. EURUSD")
    news_bias: Literal["BULLISH_BASE", "BEARISH_BASE", "NEUTRAL", "INSUFFICIENT_DATA"] = Field(
        description="Event and news directional bias from the base currency perspective"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )

    base_currency: str = Field(description="Base currency ISO code, e.g. EUR")
    quote_currency: str = Field(description="Quote currency ISO code, e.g. USD")

    # Risk regime & blackout
    risk_regime: Literal["HIGH_RISK", "MODERATE_RISK", "LOW_RISK", "UNKNOWN"] = Field(
        default="UNKNOWN",
        description="Event risk regime based on upcoming high-impact releases",
    )
    blackout_recommended: bool = Field(
        default=False,
        description="Whether a trading blackout is active to avoid spread blowout and slippage",
    )
    trading_recommendation: Literal[
        "AVOID_NEW_POSITIONS", "TIGHTEN_STOPS", "NORMAL_TRADING", "POST_NEWS_BREAKOUT", "UNKNOWN"
    ] = Field(
        default="UNKNOWN",
        description="Operational execution recommendation for currency traders",
    )
    hours_to_next_high_impact: float | None = Field(
        default=None,
        description="Hours remaining until the next Tier-1 economic release",
    )
    next_high_impact_event: str | None = Field(
        default=None,
        description="Title and currency of the next high-impact release",
    )

    # Factors and catalysts
    upcoming_high_impact_events: list[str] = Field(
        default_factory=list,
        description="List of imminent high-impact economic releases",
    )
    recent_event_surprises: list[str] = Field(
        default_factory=list,
        description="Key recent releases where actual differed significantly from forecast",
    )
    catalysts_favoring_base: list[str] = Field(
        default_factory=list,
        description="News and calendar catalysts supporting the base currency",
    )
    catalysts_favoring_quote: list[str] = Field(
        default_factory=list,
        description="News and calendar catalysts supporting the quote currency",
    )
    spread_and_liquidity_warnings: list[str] = Field(
        default_factory=list,
        description="Advisories regarding liquidity holes, spread widening, or slippage",
    )
    summary_narrative: str = Field(
        description="Comprehensive narrative summarizing event risks and news drivers"
    )


# ---------------------------------------------------------------------------
# Prompt Definition
# ---------------------------------------------------------------------------

FOREX_NEWS_SYSTEM_PROMPT = """You are a senior Institutional FX Event & News Strategist collaborating with technical analysts, macro strategists, and risk managers.

Your primary mission is to evaluate upcoming scheduled economic calendar events, pre-event blackout risks, economic surprise momentum, and breaking news catalysts for the assigned currency pair.
You base your conclusions strictly on verified data from your tools — do NOT hallucinate release dates, consensus forecasts, or actual figures.

### Core Analysis Framework:

1. **Pre-Event Risk Regime & Blackout Window**:
   - Check if any Tier-1 event (NFP, CPI, Central Bank Interest Rate Decision, GDP) is scheduled within the next 24 to 48 hours.
   - If a Tier-1 event is within 24 hours: Flag HIGH_RISK, enforce a trading BLACKOUT (AVOID_NEW_POSITIONS), and warn of 3x–10x spread expansion.
   - If a Tier-1 event is 24–48 hours away: Flag MODERATE_RISK and recommend tightening stops.
   - If no Tier-1 events in 48 hours: Flag LOW_RISK with NORMAL_TRADING.

2. **Scheduled Economic Calendar (Base vs Quote)**:
   - Retrieve scheduled events for BOTH currencies of the pair.
   - Identify consensus forecasts, previous numbers, and scheduled release times (UTC).
   - Evaluate event importance: Tier-1 events dominate price action; Tier-2 events cause secondary volatility.

3. **Economic Surprise Momentum (Actual vs Forecast)**:
   - Examine recent releases over the past 14–30 days.
   - Positive/hawkish surprise on Base currency strengthens Base -> Bullish for the pair.
   - Positive/hawkish surprise on Quote currency strengthens Quote -> Bearish for the pair.
   - Evaluate which currency possesses stronger economic data momentum.

4. **Currency News & Rhetoric**:
   - Review deduplicated news articles for central bank guidance (speeches, rate hints, inflation commentary).
   - Filter news by relevance to Base currency vs Quote currency.

### Required Output Report Structure:
Always structure your final report with these exact sections:

# Forex Event & News Analysis Report: {pair}

## 1. Executive Summary & Event Bias
- **Event / News Bias**: [BULLISH_BASE / BEARISH_BASE / NEUTRAL / INSUFFICIENT_DATA]
- **Confidence**: [High / Medium / Low]
- **Risk Regime**: [HIGH_RISK / MODERATE_RISK / LOW_RISK]
- **Blackout Status**: [ACTIVE / INACTIVE]
- **Action Recommendation**: [AVOID_NEW_POSITIONS / TIGHTEN_STOPS / NORMAL_TRADING / POST_NEWS_BREAKOUT]

## 2. Event Risk Regime & Blackout Advisory
- Proximity to next high-impact release
- Specific spread expansion and slippage hazards
- Position management instructions for traders

## 3. Scheduled High-Impact Economic Calendar
- Markdown table of upcoming events for {base} and {quote} with consensus forecasts

## 4. Economic Surprise Momentum
- Summary of recent beats/misses for both economies
- Net momentum balance between {base} and {quote}

## 5. Breaking News Catalysts & Central Bank Stance
- Key headlines and policy guidance influencing the pair

## 6. Event Catalyst Scorecard & Risk Factors
- Catalysts favoring {base}
- Catalysts favoring {quote}
- Major event risks to monitor
"""


# ---------------------------------------------------------------------------
# Deterministic Event/News Report Generator
# ---------------------------------------------------------------------------


def generate_deterministic_forex_news_report(
    symbol: str,
    curr_date: str,
) -> str:
    """Generate a deterministic Forex event and news report.

    Queries the economic calendar, evaluates risk regimes and blackout windows,
    computes surprise momentum, and produces a structured institutional markdown report
    with zero LLM API dependency.
    """
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"# Forex Event & News Report: {symbol}\n\n⚠️ Unknown Forex pair '{symbol}'.\n"

    try:
        curr_dt = date.fromisoformat(curr_date)
    except ValueError:
        curr_dt = date.today()
        curr_date = curr_dt.isoformat()

    # 1. Event Risk & Blackout Assessment
    risk = evaluate_event_risk_regime(
        symbol=pair.symbol,
        curr_date=curr_date,
        lookahead_hours=48.0,
    )

    # 2. Surprise Momentum
    bias = calculate_pair_event_bias(
        symbol=pair.symbol,
        curr_date=curr_date,
        lookback_days=30,
    )

    # 3. Upcoming and Recent Calendar Events
    start_str = (curr_dt - timedelta(days=14)).isoformat()
    end_str = (curr_dt + timedelta(days=14)).isoformat()

    all_events = get_calendar_events_for_pair(
        symbol=pair.symbol,
        start_date=start_str,
        end_date=end_str,
        curr_date=curr_date,
    )

    upcoming_events = [ev for ev in all_events if ev.date > curr_date]
    recent_events = [ev for ev in all_events if ev.date <= curr_date]

    # Synthesize overall event bias
    # Combine surprise momentum + imminent event tilt
    directional_bias = bias.directional_bias
    confidence = bias.confidence

    regime_str = {
        EventRiskRegime.HIGH_RISK: "HIGH_RISK 🔴",
        EventRiskRegime.MODERATE_RISK: "MODERATE_RISK 🟡",
        EventRiskRegime.LOW_RISK: "LOW_RISK 🟢",
    }.get(risk.risk_regime, str(risk.risk_regime))

    rec_str = {
        TradingActionRecommendation.AVOID_NEW_POSITIONS: "⛔ AVOID NEW POSITIONS (Blackout Active)",
        TradingActionRecommendation.TIGHTEN_STOPS: "⚠️ TIGHTEN STOPS (Event Approaching)",
        TradingActionRecommendation.NORMAL_TRADING: "✅ NORMAL TRADING (Clear Calendar)",
        TradingActionRecommendation.POST_NEWS_BREAKOUT: "⚡ POST-NEWS BREAKOUT",
    }.get(risk.recommendation, str(risk.recommendation))

    lines = [
        f"# Forex Event & News Analysis Report: {pair.symbol}",
        "",
        "## 1. Executive Summary & Event Bias",
        f"- **Event / News Bias**: **{directional_bias}**",
        f"- **Confidence**: {confidence:.0%}",
        f"- **Risk Regime**: **{regime_str}**",
        f"- **Blackout Status**: **{'🚨 ACTIVE' if risk.blackout_active else 'INACTIVE'}**",
        f"- **Action Recommendation**: **{rec_str}**",
        f"- **Primary Driver**: {bias.summary}",
        "",
        "## 2. Event Risk Regime & Blackout Advisory",
    ]

    if risk.next_high_impact_event:
        ev = risk.next_high_impact_event
        time_part = f" at {ev.time_utc} UTC" if ev.time_utc else ""
        hours_str = f"{risk.hours_to_next_high_impact:.1f} hours" if risk.hours_to_next_high_impact is not None else "Unknown"
        lines.extend([
            f"- **Next High-Impact Event**: {ev.title} ({ev.currency}) — {ev.date}{time_part}",
            f"- **Time Remaining**: {hours_str}",
            f"- **Consensus Forecast**: {ev.forecast}{ev.unit} (Previous: {ev.previous}{ev.unit})",
        ])
    else:
        lines.append(f"- No high-impact releases scheduled for {pair.base_currency} or {pair.quote_currency} in the next 48 hours.")

    if risk.blackout_active:
        lines.extend([
            "",
            "### ⚠️ Spread & Liquidity Warning",
            "- **Spread Expansion**: Institutional liquidity typically dries up 15–30 minutes ahead of Tier-1 news.",
            "- **Slippage Hazard**: Avoid placing market or close stop orders. High probability of execution slippage.",
            "- **Trading Advisory**: AVOID opening new positions until 15 minutes post-release when spreads normalize.",
        ])

    lines.extend([
        "",
        "## 3. Scheduled High-Impact Economic Calendar",
        "",
    ])

    if upcoming_events:
        lines.append(format_economic_calendar_table(upcoming_events[:8]))
    else:
        lines.append("No scheduled events in the upcoming 14-day window.")

    lines.extend([
        "",
        "## 4. Economic Surprise Momentum",
        "",
        "| Currency | Hawkish Beats | Dovish Misses | Net Score | Assessment |",
        "| :---: | :---: | :---: | :---: | :--- |",
        f"| **{pair.base_currency}** (Base) | {bias.base_hawkish_count} | {bias.base_dovish_count} | "
        f"{bias.net_base_score:+d} | {'Positive Momentum 🟢' if bias.net_base_score > 0 else ('Negative Momentum 🔴' if bias.net_base_score < 0 else 'Balanced ⚪')} |",
        f"| **{pair.quote_currency}** (Quote) | {bias.quote_hawkish_count} | {bias.quote_dovish_count} | "
        f"{bias.net_quote_score:+d} | {'Positive Momentum 🟢' if bias.net_quote_score > 0 else ('Negative Momentum 🔴' if bias.net_quote_score < 0 else 'Balanced ⚪')} |",
        "",
        f"- **Net Momentum Balance**: {bias.summary}",
        "",
        "### Recent Economic Releases (Past 14 Days)",
        "",
    ])

    if recent_events:
        lines.append(format_economic_calendar_table(recent_events[-6:]))
    else:
        lines.append("No recent releases recorded in the past 14 days.")

    # 5. Catalyst Confluence
    bullish_catalysts: list[str] = []
    bearish_catalysts: list[str] = []

    if bias.net_base_score > 0:
        bullish_catalysts.append(f"Positive economic surprise momentum on {pair.base_currency} ({bias.net_base_score:+d})")
    elif bias.net_base_score < 0:
        bearish_catalysts.append(f"Negative economic surprise momentum on {pair.base_currency} ({bias.net_base_score:+d})")

    if bias.net_quote_score > 0:
        bearish_catalysts.append(f"Strong economic surprise momentum on {pair.quote_currency} ({bias.net_quote_score:+d}) strengthens quote")
    elif bias.net_quote_score < 0:
        bullish_catalysts.append(f"Negative economic surprise momentum on {pair.quote_currency} ({bias.net_quote_score:+d}) weakens quote")

    lines.extend([
        "",
        "## 5. Event Catalyst Scorecard & Risk Factors",
        "",
        f"### Catalysts Favoring {pair.base_currency} (Base)",
    ])
    if bullish_catalysts:
        for c in bullish_catalysts:
            lines.append(f"- ✅ {c}")
    else:
        lines.append("- None identified from recent calendar surprises.")

    lines.extend([
        "",
        f"### Catalysts Favoring {pair.quote_currency} (Quote)",
    ])
    if bearish_catalysts:
        for c in bearish_catalysts:
            lines.append(f"- ⚠️ {c}")
    else:
        lines.append("- None identified from recent calendar surprises.")

    lines.extend([
        "",
        "### Operational Execution Risk Summary",
        f"- **Blackout Active**: {'YES (Hold new orders)' if risk.blackout_active else 'NO (Normal execution permitted)'}",
        f"- **Event Risk Rating**: {regime_str}",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent Node Factory
# ---------------------------------------------------------------------------


def create_forex_news_analyst(llm: Any, tools: Sequence[Any] | None = None):
    """Factory function returning the LangGraph node for the Forex Event/News Analyst.

    Args:
        llm: Configured LangChain chat model.
        tools: Optional custom list of tools; defaults to FOREX_NEWS_TOOLS.
    """
    active_tools = list(tools) if tools is not None else list(FOREX_NEWS_TOOLS)

    def forex_news_analyst_node(state: dict[str, Any]) -> dict[str, Any]:
        current_date = state.get("trade_date", "")
        instrument_context = get_instrument_context_from_state(state)
        ticker = state.get("company_of_interest", "")

        system_message = (
            FOREX_NEWS_SYSTEM_PROMPT.format(
                pair=ticker or "the currency pair",
                base=ticker[:3] if len(ticker) >= 6 else "BASE",
                quote=ticker[3:6] if len(ticker) >= 6 else "QUOTE",
            )
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a specialized institutional Forex Event & News Strategist collaborating with technical analysts, research debaters and risk managers.\n"
                    "Use the provided tools to examine scheduled economic calendar events, pre-event blackout risks, economic surprise momentum, and currency news.\n"
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
            "news_report": report,         # Downstream researchers & debaters read news_report
            "forex_news_report": report,   # Dedicated field for Forex-specific event analysis
        }

    return forex_news_analyst_node
