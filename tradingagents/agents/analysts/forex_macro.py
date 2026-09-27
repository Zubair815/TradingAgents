"""Forex Currency Macro Analyst Agent (Phase 7).

Specialized institutional currency macro analyst that synthesizes:
1. Central Bank Policy Rates — ECB, Fed, BoJ, BoE, RBA, etc. via FRED.
2. Interest Rate Differentials — Carry-trade flow direction between base/quote.
3. Risk Regime (VIX / DXY) — Risk-on vs risk-off macro environment.
4. Yield Curve Shape — Recession signal, growth optimism, rate expectations.
5. Macro Factor Confluence — Aggregate bias from all macro drivers.

Outputs:
- Rich markdown narrative report assigned to ``fundamentals_report`` and ``forex_macro_report``.
- Structured Pydantic assessment schema (``ForexMacroAssessment``).
- Deterministic report generator (``generate_deterministic_forex_macro_report``) for
  testing and zero-cost baseline.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.forex_macro_tools import (
    CURRENCY_POLICY_RATE_SERIES,
    FOREX_MACRO_TOOLS,
    _extract_latest_value,
    _fetch_macro_safe,
)
from tradingagents.forex.domain import get_forex_pair
from tradingagents.llm_clients.base_client import normalize_content

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured Pydantic Assessment Schema
# ---------------------------------------------------------------------------


class ForexMacroAssessment(BaseModel):
    """Structured quantitative currency macro assessment."""

    pair: str = Field(description="Forex pair symbol, e.g. EURUSD")
    macro_bias: Literal["BULLISH_BASE", "BEARISH_BASE", "NEUTRAL", "INSUFFICIENT_DATA"] = Field(
        description="Macro directional bias from the base currency perspective"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )

    # Central bank rates
    base_currency: str = Field(description="Base currency ISO code, e.g. EUR")
    quote_currency: str = Field(description="Quote currency ISO code, e.g. USD")
    base_rate: float | None = Field(
        default=None, description="Base currency central bank policy rate (%)"
    )
    quote_rate: float | None = Field(
        default=None, description="Quote currency central bank policy rate (%)"
    )
    rate_differential: float | None = Field(
        default=None, description="Base rate minus quote rate (positive favors base)"
    )
    rate_differential_direction: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"] = Field(
        default="UNKNOWN", description="Whether carry trade favors base or quote"
    )

    # Risk regime
    vix_level: float | None = Field(
        default=None, description="CBOE VIX level"
    )
    risk_regime: Literal["RISK_ON", "RISK_OFF", "NORMAL", "UNKNOWN"] = Field(
        default="UNKNOWN", description="Macro risk regime classification"
    )
    dxy_level: float | None = Field(
        default=None, description="US Dollar Index level"
    )

    # Yield curve
    yield_curve_spread: float | None = Field(
        default=None, description="US 10Y-2Y Treasury spread (%)"
    )
    yield_curve_shape: Literal["INVERTED", "FLAT", "NORMAL", "UNKNOWN"] = Field(
        default="UNKNOWN", description="Yield curve shape classification"
    )

    # Factors
    bullish_factors: list[str] = Field(
        default_factory=list,
        description="Macro factors supporting the base currency",
    )
    bearish_factors: list[str] = Field(
        default_factory=list,
        description="Macro factors weighing against the base currency",
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="Macro risks and uncertainties to monitor",
    )
    summary_narrative: str = Field(
        description="Comprehensive macro analysis narrative"
    )


# ---------------------------------------------------------------------------
# Prompt Definition
# ---------------------------------------------------------------------------

FOREX_MACRO_SYSTEM_PROMPT = """You are a senior Currency Macro Strategist at an institutional FX trading desk.

Your primary mission is to evaluate the macroeconomic environment influencing the assigned currency pair.
You base your conclusions strictly on verified data from your tools — do NOT hallucinate rate levels, VIX values, or policy stances.

### Core Analysis Framework:

1. **Central Bank Policy Rates & Differentials**:
   - Retrieve the current policy rate for BOTH the base and quote currencies.
   - Compute the rate differential: base rate minus quote rate.
   - A positive differential generally supports the base currency via carry-trade flows.
   - Note the trajectory: is the differential widening or narrowing? Stable or shifting?
   - Consider the market's expectations for upcoming rate decisions.

2. **Risk Regime Classification (VIX / DXY)**:
   - Retrieve the current VIX level and US Dollar Index.
   - Classify the environment:
     * VIX < 15: Risk-On — favors high-beta currencies (AUD, NZD, CAD, GBP).
     * VIX 15-25: Normal — directional bias from fundamentals.
     * VIX > 25: Risk-Off — favors safe havens (USD, JPY, CHF).
   - Note the DXY trend: rising DXY = broad USD strength; falling = USD weakness.

3. **US Treasury Yield Curve**:
   - Retrieve 2Y, 10Y yields and the 10Y-2Y spread.
   - Classify the curve shape: Inverted (recession signal), Flat, Normal/Steepening.
   - An inverted curve strengthens safe havens; a steepening curve favors risk currencies.
   - Consider the rate of change: rapid moves amplify the signal.

4. **Currency-Specific Macro Factors**:
   - **Commodity currencies** (AUD, NZD, CAD): sensitive to risk sentiment and commodity prices.
   - **Safe-haven currencies** (USD, JPY, CHF): benefit from risk-off flows.
   - **EUR / GBP**: sensitive to ECB/BoE policy divergence vs Fed.
   - Note any pair-specific macro drivers (e.g., oil for CAD, dairy for NZD).

### Required Output Report Structure:
Always structure your final report with these exact sections:

# Forex Macro Analysis Report: {pair}

## 1. Executive Summary & Macro Bias
- **Macro Bias**: [BULLISH_BASE / BEARISH_BASE / NEUTRAL / INSUFFICIENT_DATA]
- **Conviction**: [High / Medium / Low]
- **Key Driver**: [Single most important macro factor]

## 2. Central Bank Policy & Rate Differential
- Base currency rate, quote currency rate, differential, trajectory
- Carry-trade flow direction and magnitude

## 3. Risk Regime Assessment
- VIX level and classification (Risk-On / Normal / Risk-Off)
- Dollar Index level and trend
- Implications for this specific pair

## 4. Yield Curve & Monetary Outlook
- Current curve shape and interpretation
- Rate expectations and their currency impact

## 5. Macro Factor Scorecard
| Factor | Assessment | Impact on {base}/{quote} |
|--------|-----------|--------------------------|
| Rate Differential | ... | ... |
| Risk Regime | ... | ... |
| Yield Curve | ... | ... |
| Currency Classification | ... | ... |

## 6. Macro Risks & Considerations
- Key uncertainties, upcoming events, data dependencies
"""


# ---------------------------------------------------------------------------
# Deterministic Macro Report Generator
# ---------------------------------------------------------------------------


def generate_deterministic_forex_macro_report(
    symbol: str,
    curr_date: str,
) -> str:
    """Generate a deterministic Forex macro report from FRED data.

    Fetches central bank rates, VIX, DXY, and yield curve data for the pair
    and produces a structured markdown report with zero LLM API dependency.
    Falls back gracefully when FRED data is unavailable (no API key, etc.).
    """
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"# Forex Macro Report: {symbol}\n\n⚠️ Unknown Forex pair '{symbol}'.\n"

    lines = [
        f"# Forex Macro Analysis Report: {pair.symbol}",
        "",
    ]

    # 1. Central Bank Rates & Differential
    base_entry = CURRENCY_POLICY_RATE_SERIES.get(pair.base_currency)
    quote_entry = CURRENCY_POLICY_RATE_SERIES.get(pair.quote_currency)

    base_rate: float | None = None
    quote_rate: float | None = None

    lines.append("## 1. Central Bank Policy & Rate Differential")
    lines.append("")

    if base_entry:
        base_report = _fetch_macro_safe(base_entry[0], curr_date, look_back_days=365)
        base_rate = _extract_latest_value(base_report)
        lines.append(f"- **{pair.base_currency}** ({base_entry[1]}): "
                      f"{f'{base_rate:.2f}%' if base_rate is not None else 'N/A'}")
    else:
        lines.append(f"- **{pair.base_currency}**: No FRED policy rate series available")

    if quote_entry:
        quote_report = _fetch_macro_safe(quote_entry[0], curr_date, look_back_days=365)
        quote_rate = _extract_latest_value(quote_report)
        lines.append(f"- **{pair.quote_currency}** ({quote_entry[1]}): "
                      f"{f'{quote_rate:.2f}%' if quote_rate is not None else 'N/A'}")
    else:
        lines.append(f"- **{pair.quote_currency}**: No FRED policy rate series available")

    # Rate differential
    rate_diff: float | None = None
    if base_rate is not None and quote_rate is not None:
        rate_diff = base_rate - quote_rate
        diff_dir = "POSITIVE (favors base)" if rate_diff > 0 else (
            "NEGATIVE (favors quote)" if rate_diff < 0 else "NEUTRAL"
        )
        carry_currency = pair.base_currency if rate_diff > 0 else pair.quote_currency
        lines.extend([
            f"- **Rate Differential**: {rate_diff:+.2f}% ({diff_dir})",
            f"- **Carry-trade flows favor**: {carry_currency}",
        ])
    else:
        lines.append("- **Rate Differential**: Unable to compute (missing data)")

    lines.append("")

    # 2. Risk Regime (VIX + DXY)
    lines.append("## 2. Risk Regime Assessment")
    lines.append("")

    vix_report = _fetch_macro_safe("vix", curr_date, look_back_days=90)
    dxy_report = _fetch_macro_safe("dollar_index", curr_date, look_back_days=90)
    vix_val = _extract_latest_value(vix_report)
    dxy_val = _extract_latest_value(dxy_report)

    if vix_val is not None:
        if vix_val < 15:
            vix_regime = "LOW_VOL / RISK_ON 🟢"
        elif vix_val <= 25:
            vix_regime = "NORMAL / MIXED 🟡"
        else:
            vix_regime = "ELEVATED / RISK_OFF 🔴"
        lines.append(f"- **VIX**: {vix_val:.1f} — {vix_regime}")
    else:
        vix_regime = "UNKNOWN"
        lines.append("- **VIX**: N/A")

    if dxy_val is not None:
        lines.append(f"- **US Dollar Index (DXY)**: {dxy_val:.2f}")
    else:
        lines.append("- **US Dollar Index (DXY)**: N/A")

    # Currency classification and risk regime implications
    safe_havens = {"USD", "JPY", "CHF"}
    risk_currencies = {"AUD", "NZD", "CAD"}
    base_is_safe = pair.base_currency in safe_havens
    quote_is_safe = pair.quote_currency in safe_havens
    base_is_risk = pair.base_currency in risk_currencies

    if vix_val is not None and vix_val > 25:
        if base_is_safe and not quote_is_safe:
            lines.append(f"- Risk-off environment **favors {pair.base_currency}** (safe-haven)")
        elif quote_is_safe and not base_is_safe:
            lines.append(f"- Risk-off environment **favors {pair.quote_currency}** (safe-haven)")
    elif vix_val is not None and vix_val < 15:
        if base_is_risk:
            lines.append(f"- Risk-on environment **favors {pair.base_currency}** (high-beta)")
        elif pair.quote_currency in risk_currencies:
            lines.append(f"- Risk-on environment **favors {pair.quote_currency}** (high-beta)")

    lines.append("")

    # 3. Yield Curve
    lines.append("## 3. Yield Curve & Monetary Outlook")
    lines.append("")

    spread_report = _fetch_macro_safe("yield_curve", curr_date, look_back_days=180)
    y10_report = _fetch_macro_safe("10y_treasury", curr_date, look_back_days=180)
    spread_val = _extract_latest_value(spread_report)
    y10_val = _extract_latest_value(y10_report)

    if spread_val is not None:
        if spread_val < -0.1:
            curve_shape = "INVERTED 🔴"
        elif spread_val < 0.3:
            curve_shape = "FLAT 🟡"
        else:
            curve_shape = "NORMAL / STEEPENING 🟢"
        lines.append(f"- **10Y-2Y Spread**: {spread_val:+.2f}% — {curve_shape}")
    else:
        curve_shape = "UNKNOWN"
        lines.append("- **10Y-2Y Spread**: N/A")

    if y10_val is not None:
        lines.append(f"- **10Y Treasury Yield**: {y10_val:.2f}%")

    lines.append("")

    # 4. Macro Bias Determination
    lines.append("## 4. Executive Summary & Macro Bias")
    lines.append("")

    bullish_factors: list[str] = []
    bearish_factors: list[str] = []

    # Rate differential factor
    if rate_diff is not None:
        if rate_diff > 0:
            bullish_factors.append(f"Positive rate differential ({rate_diff:+.2f}%) favors {pair.base_currency}")
        elif rate_diff < 0:
            bearish_factors.append(f"Negative rate differential ({rate_diff:+.2f}%) favors {pair.quote_currency}")

    # Risk regime factor
    if vix_val is not None:
        if vix_val > 25:
            if base_is_safe:
                bullish_factors.append(f"Risk-off VIX ({vix_val:.0f}) favors safe-haven {pair.base_currency}")
            elif quote_is_safe:
                bearish_factors.append(f"Risk-off VIX ({vix_val:.0f}) favors safe-haven {pair.quote_currency}")
        elif vix_val < 15:
            if base_is_risk:
                bullish_factors.append(f"Risk-on VIX ({vix_val:.0f}) favors high-beta {pair.base_currency}")
            elif pair.quote_currency in risk_currencies:
                bearish_factors.append(f"Risk-on VIX ({vix_val:.0f}) favors high-beta {pair.quote_currency}")

    # DXY factor (for USD pairs)
    if dxy_val is not None and (pair.base_currency == "USD" or pair.quote_currency == "USD"):
        if pair.quote_currency == "USD":
            # e.g., EURUSD — rising DXY is bearish for the pair
            bearish_factors.append(f"Dollar Index at {dxy_val:.1f} — monitor for USD strength")
        elif pair.base_currency == "USD":
            # e.g., USDCAD — rising DXY is bullish for the pair
            bullish_factors.append(f"Dollar Index at {dxy_val:.1f} — monitor for USD strength support")

    # Determine overall bias
    if not bullish_factors and not bearish_factors:
        macro_bias = "INSUFFICIENT_DATA"
        confidence = 0.0
    elif len(bullish_factors) > len(bearish_factors):
        macro_bias = "BULLISH_BASE"
        confidence = min(0.3 + 0.2 * (len(bullish_factors) - len(bearish_factors)), 0.9)
    elif len(bearish_factors) > len(bullish_factors):
        macro_bias = "BEARISH_BASE"
        confidence = min(0.3 + 0.2 * (len(bearish_factors) - len(bullish_factors)), 0.9)
    else:
        macro_bias = "NEUTRAL"
        confidence = 0.3

    lines.append(f"- **Macro Bias**: **{macro_bias}**")
    lines.append(f"- **Confidence**: {confidence:.0%}")
    lines.append("")

    if bullish_factors:
        lines.append("### Bullish Factors (favoring base currency)")
        for f in bullish_factors:
            lines.append(f"- ✅ {f}")
        lines.append("")

    if bearish_factors:
        lines.append("### Bearish Factors (favoring quote currency)")
        for f in bearish_factors:
            lines.append(f"- ⚠️ {f}")
        lines.append("")

    # 5. Macro Factor Scorecard Table
    lines.extend([
        "## 5. Macro Factor Scorecard",
        "",
        "| Factor | Value | Assessment |",
        "| --- | --- | --- |",
        f"| Rate Differential | {f'{rate_diff:+.2f}%' if rate_diff is not None else 'N/A'} | "
        f"{'Favors ' + pair.base_currency if rate_diff and rate_diff > 0 else ('Favors ' + pair.quote_currency if rate_diff and rate_diff < 0 else 'Neutral/Unknown')} |",
        f"| VIX | {f'{vix_val:.1f}' if vix_val is not None else 'N/A'} | {vix_regime} |",
        f"| Dollar Index | {f'{dxy_val:.2f}' if dxy_val is not None else 'N/A'} | Monitor trend |",
        f"| Yield Curve | {f'{spread_val:+.2f}%' if spread_val is not None else 'N/A'} | {curve_shape} |",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent Node Factory
# ---------------------------------------------------------------------------


def create_forex_macro_analyst(llm: Any, tools: Sequence[Any] | None = None):
    """Factory function returning the LangGraph node for the Forex Macro Analyst.

    Args:
        llm: Configured LangChain chat model.
        tools: Optional custom list of tools; defaults to FOREX_MACRO_TOOLS.
    """
    active_tools = list(tools) if tools is not None else list(FOREX_MACRO_TOOLS)

    def forex_macro_analyst_node(state: dict[str, Any]) -> dict[str, Any]:
        current_date = state.get("trade_date", "")
        instrument_context = get_instrument_context_from_state(state)
        ticker = state.get("company_of_interest", "")

        system_message = (
            FOREX_MACRO_SYSTEM_PROMPT.format(
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
                    "You are a specialized institutional Currency Macro Strategist collaborating with technical analysts, research debaters and risk managers.\n"
                    "Use the provided tools to analyze central bank policy rates, rate differentials, risk regime indicators (VIX/DXY), and yield curve data.\n"
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
            "fundamentals_report": report,    # Downstream researchers & managers read fundamentals_report
            "forex_macro_report": report,     # Dedicated field for Forex-specific macro analysis
        }

    return forex_macro_analyst_node
