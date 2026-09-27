"""Forex-specific economic calendar and news tools for event analysis (Phase 8).

Exposes LangChain agent tools that retrieve Forex-relevant economic events and news:
- ``get_forex_economic_calendar``: Scheduled economic releases (NFP, CPI, Rates, GDP) for pair currencies.
- ``get_economic_event_risk``: Pre-event blackout window, spread expansion warnings, and risk regime.
- ``get_forex_currency_news``: Point-in-time deduplicated news articles for pair currencies.
- ``get_event_surprise_history``: Recent economic surprises (actual vs forecast) and currency momentum bias.

All tools respect point-in-time ``trade_date`` and mask future actuals to prevent lookahead leakage.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.forex_context import resolve_forex_cutoff
from tradingagents.dataflows.forex_news import fetch_forex_news
from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex.calendar import (
    EconomicEvent,
    EventRiskRegime,
    TradingActionRecommendation,
    calculate_pair_event_bias,
    evaluate_event_risk_regime,
    format_economic_calendar_table,
    get_calendar_events_for_pair,
)
from tradingagents.forex.domain import get_forex_pair

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool: Economic Calendar
# ---------------------------------------------------------------------------


@tool
def get_forex_economic_calendar(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, GBPJPY, USDCAD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    days_ahead: Annotated[int, "Number of days ahead to look for upcoming events (default: 7)"] = 7,
    days_behind: Annotated[int, "Number of days behind to look for past releases (default: 7)"] = 7,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Retrieve scheduled economic calendar events for the currencies of a Forex pair.

    Returns upcoming high-impact and medium-impact releases (CPI, Central Bank decisions,
    NFP, GDP, PMIs) as well as recent releases with actual vs. forecast surprises.

    Point-in-time safe: events after curr_date have actual values masked.
    """
    pit_cutoff = resolve_forex_cutoff(curr_date, trade_date, forex_as_of_utc)
    pit_date = pit_cutoff.date().isoformat() if pit_cutoff else ""
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"ERROR: Unknown Forex pair '{symbol}'. Use canonical pairs like EURUSD, GBPJPY."

    try:
        curr_dt = date.fromisoformat(pit_date)
    except ValueError:
        return f"ERROR: Invalid curr_date '{pit_date}'. Expected YYYY-MM-DD."

    start_date_str = (curr_dt - timedelta(days=days_behind)).isoformat()
    end_date_str = (curr_dt + timedelta(days=days_ahead)).isoformat()

    events = get_calendar_events_for_pair(
        symbol=pair.symbol,
        start_date=start_date_str,
        end_date=end_date_str,
        curr_date=pit_cutoff,
    )

    upcoming: list[EconomicEvent] = []
    recent: list[EconomicEvent] = []

    for ev in events:
        if not ev.is_released_as_of(pit_cutoff):
            upcoming.append(ev)
        else:
            recent.append(ev)

    lines = [
        f"# Economic Calendar: {pair.symbol} ({pair.base_currency}/{pair.quote_currency})",
        f"**As of Date:** {pit_date} | **Window:** {start_date_str} to {end_date_str}",
        "",
        f"## 1. Upcoming Scheduled Events (Next {days_ahead} Days)",
        "",
    ]

    if upcoming:
        lines.append(format_economic_calendar_table(upcoming))
    else:
        lines.append(f"No high/medium impact events scheduled for {pair.base_currency} or {pair.quote_currency} in the next {days_ahead} days.\n")

    lines.extend([
        "",
        f"## 2. Recent Economic Releases (Past {days_behind} Days)",
        "",
    ])

    if recent:
        lines.append(format_economic_calendar_table(recent))
    else:
        lines.append(f"No recent economic releases recorded for {pair.base_currency} or {pair.quote_currency} in the past {days_behind} days.\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Economic Event Risk & Blackout Regime
# ---------------------------------------------------------------------------


@tool
def get_economic_event_risk(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, USDJPY, AUDUSD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    lookahead_hours: Annotated[float, "Hours ahead to inspect for event risk (default: 48.0)"] = 48.0,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Evaluate event risk regime and pre-news blackout conditions for a Forex pair.

    Institutional FX desks enforce strict trading rules around Tier-1 news (NFP, CPI,
    Rate Decisions) due to severe spread expansion (3x-10x) and execution slippage.

    Returns:
    - Risk regime (HIGH_RISK, MODERATE_RISK, LOW_RISK)
    - Blackout status (Active vs. Inactive)
    - Time until next high-impact release
    - Operational recommendation (Avoid new entries, tighten stops, normal trading)
    """
    pit_cutoff = resolve_forex_cutoff(curr_date, trade_date, forex_as_of_utc)
    pit_date = pit_cutoff.date().isoformat() if pit_cutoff else ""
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"ERROR: Unknown Forex pair '{symbol}'."

    risk = evaluate_event_risk_regime(
        symbol=pair.symbol,
        curr_date=pit_cutoff,
        lookahead_hours=lookahead_hours,
    )

    regime_display = {
        EventRiskRegime.HIGH_RISK: "HIGH RISK 🔴 (Tier-1 Event Imminent / Today)",
        EventRiskRegime.MODERATE_RISK: "MODERATE RISK 🟡 (Tier-1 Event Approaching / Tier-2 Today)",
        EventRiskRegime.LOW_RISK: "LOW RISK 🟢 (Clear Calendar / Favorable Liquidity)",
    }.get(risk.risk_regime, str(risk.risk_regime))

    rec_display = {
        TradingActionRecommendation.AVOID_NEW_POSITIONS: "⛔ AVOID NEW POSITIONS — High probability of spread expansion & slippage",
        TradingActionRecommendation.TIGHTEN_STOPS: "⚠️ TIGHTEN STOPS / REDUCE EXPOSURE — Manage existing trades before volatility",
        TradingActionRecommendation.NORMAL_TRADING: "✅ NORMAL TRADING — Standard execution parameters apply",
        TradingActionRecommendation.POST_NEWS_BREAKOUT: "⚡ POST-NEWS BREAKOUT — Wait for initial 15-minute spike to settle before entering",
    }.get(risk.recommendation, str(risk.recommendation))

    lines = [
        f"## Event Risk Regime Assessment: {pair.symbol}",
        "",
        f"- **Current Date:** {pit_date}",
        f"- **Risk Regime:** {regime_display}",
        f"- **Blackout Window:** {'🚨 ACTIVE' if risk.blackout_active else 'Inactive'}",
        f"- **Trading Recommendation:** {rec_display}",
    ]

    if risk.next_high_impact_event:
        ev = risk.next_high_impact_event
        time_part = f" at {ev.time_utc} UTC" if ev.time_utc else ""
        hours_str = f"{risk.hours_to_next_high_impact:.1f} hours" if risk.hours_to_next_high_impact is not None else "Unknown"
        lines.extend([
            "",
            "### Next High-Impact Event",
            f"- **Event:** {ev.title} ({ev.currency})",
            f"- **Scheduled Date/Time:** {ev.date}{time_part}",
            f"- **Time Remaining:** {hours_str}",
            f"- **Consensus Forecast:** {ev.forecast}{ev.unit} (Previous: {ev.previous}{ev.unit})",
            f"- **Impact Rating:** 🔴 Tier-1 ({ev.currency})",
        ])
    else:
        lines.extend([
            "",
            "### Next High-Impact Event",
            f"- None scheduled within the next {lookahead_hours:.0f} hours for {pair.base_currency} or {pair.quote_currency}.",
        ])

    if risk.blackout_active:
        lines.extend([
            "",
            "### ⚠️ Institutional Blackout Advisory",
            "- Spreads typically widen 3x–8x normal levels within 15–30 minutes before Tier-1 news.",
            "- Avoid placing resting stop or limit orders close to market price.",
            "- Trailing stops may be triggered prematurely by artificial spread blowout.",
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Currency News with Deduplication
# ---------------------------------------------------------------------------


def _clean_headline(text: str) -> str:
    """Normalize headline for deduplication."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


@tool
def get_forex_currency_news(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, GBPUSD, USDJPY"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Days to look back for news articles (default: 5)"] = 5,
    limit: Annotated[int, "Max number of articles to return (default: 8)"] = 8,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Retrieve point-in-time deduplicated news articles for a Forex pair and its currencies.

    Filters news specifically relevant to the base and quote economies, tagging articles
    with currency impact relevance (Base, Quote, or Global FX).
    """
    pit_cutoff = resolve_forex_cutoff(curr_date, trade_date, forex_as_of_utc)
    try:
        articles = fetch_forex_news(symbol, as_of=pit_cutoff,
                                    lookback_days=look_back_days, limit=limit)
    except DataInsufficientError as exc:
        return f"NEWS_UNAVAILABLE: {exc}. News confidence must be reduced; absence of headlines is not established."
    lines = [f"# Forex Currency News: {symbol}",
             "Coverage: observed headlines only; completeness of the news window is not established.", ""]
    for article in articles:
        lines.extend([
            f"### {article.headline}",
            f"Publisher: {article.publisher} | Source: {article.source}",
            f"Published UTC: {article.published_at_utc.isoformat()}",
            f"Retrieved UTC: {article.retrieved_at_utc.isoformat()}",
            f"Currencies: {', '.join(article.currencies)} | Category: {article.category} | Relevance: {article.relevance}",
            f"URL: {article.url}", article.summary, "",
        ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Economic Surprise Momentum History
# ---------------------------------------------------------------------------


@tool
def get_event_surprise_history(
    symbol: Annotated[str, "Forex pair symbol, e.g. EURUSD, GBPUSD, AUDUSD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    lookback_days: Annotated[int, "Lookback days for surprise history (default: 30)"] = 30,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Analyze economic surprise momentum (actual vs forecast) for base vs quote currency.

    Institutional currency traders track surprise indexes (e.g. Citi Economic Surprise Index).
    A currency whose economic releases consistently beat forecasts attracts institutional inflows.

    Returns:
    - Base vs quote hawkish/dovish surprise counts
    - Net surprise momentum score
    - Directional bias (BULLISH_BASE, BEARISH_BASE, NEUTRAL)
    - Detailed breakdown of recent surprise events
    """
    pit_cutoff = resolve_forex_cutoff(curr_date, trade_date, forex_as_of_utc)
    pit_date = pit_cutoff.date().isoformat() if pit_cutoff else ""
    pair = get_forex_pair(symbol)
    if pair is None:
        return f"ERROR: Unknown Forex pair '{symbol}'."

    bias = calculate_pair_event_bias(
        symbol=pair.symbol,
        curr_date=pit_cutoff,
        lookback_days=lookback_days,
    )

    lines = [
        f"## Economic Surprise Momentum Analysis: {pair.symbol}",
        f"**Lookback Window:** {lookback_days} days (up to {pit_date})",
        "",
        "### 1. Surprise Momentum Scorecard",
        "| Currency | Hawkish Beats | Dovish Misses | Net Score | Assessment |",
        "| :---: | :---: | :---: | :---: | :--- |",
        f"| **{bias.base_currency}** (Base) | {bias.base_hawkish_count} | {bias.base_dovish_count} | "
        f"{bias.net_base_score:+d} | {'Positive Momentum 🟢' if bias.net_base_score > 0 else ('Negative Momentum 🔴' if bias.net_base_score < 0 else 'Balanced ⚪')} |",
        f"| **{bias.quote_currency}** (Quote) | {bias.quote_hawkish_count} | {bias.quote_dovish_count} | "
        f"{bias.net_quote_score:+d} | {'Positive Momentum 🟢' if bias.net_quote_score > 0 else ('Negative Momentum 🔴' if bias.net_quote_score < 0 else 'Balanced ⚪')} |",
        "",
        "### 2. Synthesized Economic Momentum Bias",
        f"- **Directional Bias:** **{bias.directional_bias}**",
        f"- **Confidence:** {bias.confidence:.0%}",
        f"- **Analysis:** {bias.summary}",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consolidated Toolset
# ---------------------------------------------------------------------------

FOREX_NEWS_TOOLS = [
    get_forex_economic_calendar,
    get_economic_event_risk,
    get_forex_currency_news,
    get_event_surprise_history,
]
