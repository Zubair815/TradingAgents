"""Forex-specific macro data tools for currency macro analysis (Phase 7).

Exposes LangChain agent tools that retrieve Forex-relevant macroeconomic data:
- ``get_central_bank_rates``: Policy rates for base/quote currency central banks.
- ``get_rate_differential``: Interest rate differential between two currencies.
- ``get_risk_regime_indicators``: VIX + Dollar Index for risk-on / risk-off regime.
- ``get_treasury_yield_curve``: US yield curve shape for USD-pair monetary context.

All tools route through the existing FRED vendor and respect point-in-time ``trade_date``.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.date_window import as_of
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.forex.domain import get_forex_pair

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Currency → FRED series mapping for central bank policy rates
# ---------------------------------------------------------------------------

# Maps ISO 4217 currency codes to the FRED series that best represents the
# prevailing policy rate (overnight / target rate) for that economy.  Not every
# currency has a FRED series — for those we return a clear "unavailable" message.
CURRENCY_POLICY_RATE_SERIES: dict[str, tuple[str, str]] = {
    "USD": ("FEDFUNDS", "US Federal Funds Rate"),
    "EUR": ("ECBDFR", "ECB Deposit Facility Rate"),
    "GBP": ("IUDSOIA", "Bank of England Bank Rate (SONIA proxy)"),
    "JPY": ("IRSTCI01JPM156N", "Bank of Japan Policy Rate"),
    "CHF": ("IR3TIB01CHM156N", "SNB Policy Rate (3M LIBOR proxy)"),
    "AUD": ("IRSTCB01AUM156N", "RBA Cash Rate"),
    "CAD": ("IRSTCB01CAM156N", "Bank of Canada Overnight Rate"),
    "NZD": ("IRSTCB01NZM156N", "RBNZ Official Cash Rate"),
}


def _fetch_macro_safe(indicator: str, curr_date: str, look_back_days: int = 365) -> str:
    """Fetch a FRED macro series, returning a user-friendly message on failure."""
    from tradingagents.dataflows.forex_context import historical_market_context
    if historical_market_context() is not None:
        return "MACRO_UNAVAILABLE: no verified intraday historical observation archive for this series."
    try:
        return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)
    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", indicator, exc)
        return f"⚠️ Data unavailable for '{indicator}': {exc}"


# ---------------------------------------------------------------------------
# Tool: Central Bank Policy Rates
# ---------------------------------------------------------------------------


@tool
def get_central_bank_rates(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """Retrieve central bank policy rates for both currencies of a Forex pair.

    For EURUSD this returns the ECB Deposit Facility Rate (EUR) and the
    US Federal Funds Rate (USD).  The data comes from FRED with point-in-time
    vintage pinning — a historical run sees the values known as of that date.
    """
    pit_date = as_of(curr_date, trade_date)
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"ERROR: Unknown Forex pair '{symbol}'. Use canonical symbols like EURUSD, USDJPY."

    sections: list[str] = [f"## Central Bank Policy Rates: {pair.symbol}\n"]

    for currency, label in [(pair.base_currency, "Base"), (pair.quote_currency, "Quote")]:
        entry = CURRENCY_POLICY_RATE_SERIES.get(currency)
        if entry is None:
            sections.append(
                f"### {label} Currency: {currency}\n"
                f"⚠️ No FRED policy rate series available for {currency}.\n"
            )
            continue
        series_id, description = entry
        sections.append(f"### {label} Currency: {currency} — {description}\n")
        report = _fetch_macro_safe(series_id, pit_date, look_back_days=365)
        sections.append(report + "\n")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Tool: Interest Rate Differential
# ---------------------------------------------------------------------------


@tool
def get_rate_differential(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """Compute the interest rate differential between the base and quote currencies.

    A positive differential (base rate > quote rate) generally supports the base
    currency via carry-trade flows.  A negative differential supports the quote
    currency.  Uses the most recent observations from FRED.

    Returns a markdown report with both rates and the differential.
    """
    pit_date = as_of(curr_date, trade_date)
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"ERROR: Unknown Forex pair '{symbol}'."

    base_entry = CURRENCY_POLICY_RATE_SERIES.get(pair.base_currency)
    quote_entry = CURRENCY_POLICY_RATE_SERIES.get(pair.quote_currency)

    if base_entry is None or quote_entry is None:
        missing = []
        if base_entry is None:
            missing.append(pair.base_currency)
        if quote_entry is None:
            missing.append(pair.quote_currency)
        return (
            f"## Rate Differential: {pair.symbol}\n"
            f"⚠️ Policy rate data unavailable for: {', '.join(missing)}. "
            f"Cannot compute differential.\n"
        )

    base_report = _fetch_macro_safe(base_entry[0], pit_date, look_back_days=180)
    quote_report = _fetch_macro_safe(quote_entry[0], pit_date, look_back_days=180)

    # Try to extract the latest numeric values from the reports for the differential
    base_rate = _extract_latest_value(base_report)
    quote_rate = _extract_latest_value(quote_report)

    lines = [
        f"## Interest Rate Differential: {pair.symbol}",
        "",
        f"### {pair.base_currency} Policy Rate ({base_entry[1]})",
        f"- Latest: {f'{base_rate:.2f}%' if base_rate is not None else 'N/A'}",
        "",
        f"### {pair.quote_currency} Policy Rate ({quote_entry[1]})",
        f"- Latest: {f'{quote_rate:.2f}%' if quote_rate is not None else 'N/A'}",
        "",
    ]

    if base_rate is not None and quote_rate is not None:
        diff = base_rate - quote_rate
        direction = "POSITIVE (favors base)" if diff > 0 else ("NEGATIVE (favors quote)" if diff < 0 else "NEUTRAL")
        carry_label = pair.base_currency if diff > 0 else pair.quote_currency
        lines.extend([
            f"### Rate Differential: {diff:+.2f}% ({direction})",
            f"- Carry-trade flows favor **{carry_label}** (higher-yielding currency)",
            f"- A {'widening' if abs(diff) > 1.0 else 'narrow'} differential "
            f"{'strongly supports' if abs(diff) > 2.0 else 'provides modest support for'} directional bias",
        ])
    else:
        lines.append("### Rate Differential: Unable to compute (missing rate data)")

    return "\n".join(lines)


def _extract_latest_value(fred_report: str) -> float | None:
    """Best-effort extraction of the latest numeric value from a FRED report string.

    Looks for the ``**Latest:** <value>`` pattern that ``fred.get_macro_data`` produces.
    Returns ``None`` if parsing fails (the LLM will still see the full report text).
    """
    import re

    match = re.search(r"\*\*Latest:\*\*\s+([\d.,\-]+)", fred_report)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Tool: Risk Regime Indicators (VIX + Dollar Index)
# ---------------------------------------------------------------------------


@tool
def get_risk_regime_indicators(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """Retrieve VIX and US Dollar Index (DXY) to assess macro risk regime.

    - VIX < 15: Low volatility / complacent — risk-on (favors high-beta, commodity currencies).
    - VIX 15-25: Normal — mixed.
    - VIX > 25: Elevated fear — risk-off (favors safe havens: USD, JPY, CHF).

    - Rising DXY: Broad USD strength — headwind for EUR, GBP, commodity currencies.
    - Falling DXY: USD weakness — tailwind for non-USD pairs.

    Returns both series with interpretation guidance.
    """
    pit_date = as_of(curr_date, trade_date)

    vix_report = _fetch_macro_safe("vix", pit_date, look_back_days=90)
    dxy_report = _fetch_macro_safe("dollar_index", pit_date, look_back_days=90)

    vix_val = _extract_latest_value(vix_report)
    dxy_val = _extract_latest_value(dxy_report)

    # Classify risk regime
    if vix_val is not None:
        if vix_val < 15:
            vix_regime = "LOW_VOL / RISK_ON 🟢"
        elif vix_val <= 25:
            vix_regime = "NORMAL / MIXED 🟡"
        else:
            vix_regime = "ELEVATED / RISK_OFF 🔴"
    else:
        vix_regime = "UNKNOWN"

    lines = [
        "## Macro Risk Regime Assessment",
        "",
        f"### VIX (CBOE Volatility Index): {f'{vix_val:.1f}' if vix_val else 'N/A'} — {vix_regime}",
        "",
        "**VIX Interpretation:**",
        "- < 15: Risk-On (favors AUD, NZD, CAD, GBP; risk appetite)",
        "- 15-25: Normal range; directional bias from technicals/fundamentals",
        "- > 25: Risk-Off (favors USD, JPY, CHF; safe-haven flows)",
        "",
        f"### US Dollar Index (DXY): {f'{dxy_val:.2f}' if dxy_val else 'N/A'}",
        "",
        "**DXY Interpretation:**",
        "- Rising DXY → Broad USD strength → headwind for EURUSD, GBPUSD, AUDUSD longs",
        "- Falling DXY → USD weakness → tailwind for non-USD pair longs",
        "",
        "---",
        "",
        "### Raw Data",
        "",
        vix_report,
        "",
        dxy_report,
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: US Treasury Yield Curve
# ---------------------------------------------------------------------------


@tool
def get_treasury_yield_curve(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """Retrieve US Treasury yield curve data (2Y, 10Y, 30Y, and 10Y-2Y spread).

    An inverted yield curve (negative 10Y-2Y spread) signals recession expectations
    and generally strengthens safe-haven currencies (USD, JPY, CHF).  A steepening
    curve signals growth optimism and favors risk/commodity currencies.

    Essential context for any USD-denominated Forex pair.
    """
    pit_date = as_of(curr_date, trade_date)

    y2_report = _fetch_macro_safe("2y_treasury", pit_date, look_back_days=180)
    y10_report = _fetch_macro_safe("10y_treasury", pit_date, look_back_days=180)
    spread_report = _fetch_macro_safe("yield_curve", pit_date, look_back_days=180)

    y2_val = _extract_latest_value(y2_report)
    y10_val = _extract_latest_value(y10_report)
    spread_val = _extract_latest_value(spread_report)

    # Classify curve shape
    if spread_val is not None:
        if spread_val < -0.1:
            curve_shape = "INVERTED 🔴 (recession signal; safe-haven support)"
        elif spread_val < 0.3:
            curve_shape = "FLAT / NEAR-INVERSION 🟡 (caution)"
        else:
            curve_shape = "NORMAL / STEEPENING 🟢 (growth optimism; risk-on)"
    else:
        curve_shape = "UNKNOWN"

    lines = [
        "## US Treasury Yield Curve",
        "",
        "| Maturity | Yield |",
        "| --- | --- |",
        f"| 2-Year | {f'{y2_val:.2f}%' if y2_val is not None else 'N/A'} |",
        f"| 10-Year | {f'{y10_val:.2f}%' if y10_val is not None else 'N/A'} |",
        f"| 10Y-2Y Spread | {f'{spread_val:+.2f}%' if spread_val is not None else 'N/A'} |",
        "",
        f"**Curve Shape**: {curve_shape}",
        "",
        "**Interpretation:**",
        "- Inverted curve → recession fears → risk-off → USD/JPY/CHF strength",
        "- Normal/steepening curve → growth optimism → risk-on → AUD/NZD/GBP/CAD strength",
        "- Rapid steepening → rate cut expectations → watch for USD weakness",
        "",
        "---",
        "",
        "### Raw Series Data",
        "",
        y2_report,
        "",
        y10_report,
        "",
        spread_report,
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consolidated toolset for the Forex Macro Analyst
# ---------------------------------------------------------------------------

FOREX_MACRO_TOOLS = [
    get_central_bank_rates,
    get_rate_differential,
    get_risk_regime_indicators,
    get_treasury_yield_curve,
]
