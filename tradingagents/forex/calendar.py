"""Forex Economic Calendar & High-Impact Event Engine (Phase 8).

Institutional FX trading requires continuous awareness of scheduled macroeconomic
data releases (NFP, CPI, Central Bank rate decisions, GDP, PMIs) and pre-event
blackout windows. High-impact economic news releases trigger severe spread expansion,
slippage, and volatility spikes.

This module provides:
1. Economic calendar domain models (``EconomicEvent``, ``EventImpact``, ``EventSurpriseDirection``).
2. Sourced Trading Economics events and immutable observed-time archives.
3. Point-in-time (PIT) safety — events beyond ``curr_date`` mask actual values to prevent future leakage.
4. Pair-aware event filtering (matching base and quote currencies).
5. Pre-event / post-event risk regime classification and blackout recommendations.
6. Economic surprise momentum and pair directional bias calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import math
from typing import Sequence

from tradingagents.forex.domain import get_forex_pair
from tradingagents.forex.symbols import ForexSymbolMap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Classifications
# ---------------------------------------------------------------------------


class EventImpact(str, Enum):
    """Impact level of an economic event on currency volatility."""

    HIGH = "HIGH"       # Tier 1: NFP, CPI, Central Bank Rate Decisions, GDP
    MEDIUM = "MEDIUM"   # Tier 2: Retail Sales, PMIs, Employment, Trade Balance
    LOW = "LOW"         # Tier 3: Minor surveys, speech appearances, secondary data


class EventSurpriseDirection(str, Enum):
    """Market direction implied by the event outcome relative to expectations."""

    HAWKISH = "HAWKISH"   # Better-than-expected data supporting currency appreciation
    DOVISH = "DOVISH"     # Worse-than-expected data triggering currency depreciation
    INLINE = "INLINE"     # Outcome matches consensus forecast within tolerance
    NEUTRAL = "NEUTRAL"   # No consensus forecast, qualitative event, or unreleased


class EventRiskRegime(str, Enum):
    """Risk regime classification based on upcoming high-impact events."""

    HIGH_RISK = "HIGH_RISK"         # Tier 1 event < 24h away or today (spread expansion danger)
    MODERATE_RISK = "MODERATE_RISK" # Tier 1 event 24h-48h away or Tier 2 event today
    LOW_RISK = "LOW_RISK"           # Clear calendar; no high-impact releases within 48h


class TradingActionRecommendation(str, Enum):
    """Execution recommendation for Forex traders based on economic calendar."""

    AVOID_NEW_POSITIONS = "AVOID_NEW_POSITIONS" # Blackout active: high probability of slippage
    TIGHTEN_STOPS = "TIGHTEN_STOPS"             # Manage open risk before approaching volatility
    NORMAL_TRADING = "NORMAL_TRADING"           # Favorable spread & liquidity conditions
    POST_NEWS_BREAKOUT = "POST_NEWS_BREAKOUT"   # Post-release momentum follow-through


# ---------------------------------------------------------------------------
# Economic Event Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EconomicEvent:
    """Institutional economic release representation."""

    event_id: str
    currency: str
    title: str
    impact: EventImpact
    date: str                          # YYYY-MM-DD
    time_utc: str | None = None        # HH:MM
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    unit: str = "%"
    higher_is_bullish: bool = True     # True for GDP/CPI/NFP; False for Unemployment Rate
    description: str = ""
    country: str = ""
    source: str = "unknown"
    source_url: str = ""
    published_at_utc: datetime | None = None
    retrieved_at_utc: datetime | None = None
    known_at_utc: datetime | None = None
    revision_at_utc: datetime | None = None
    revised_previous: float | None = None
    previous_before_revision: float | None = None

    @property
    def surprise(self) -> float | None:
        """Surprise amount: actual minus forecast."""
        if self.actual is not None and self.forecast is not None:
            return round(self.actual - self.forecast, 4)
        return None

    @property
    def surprise_direction(self) -> EventSurpriseDirection:
        """Classify surprise as HAWKISH, DOVISH, INLINE, or NEUTRAL."""
        if self.actual is None or self.forecast is None:
            return EventSurpriseDirection.NEUTRAL

        diff = self.actual - self.forecast
        # Relative tolerance based on magnitude or small epsilon
        tol = 0.01 if abs(self.forecast) <= 10.0 else 0.5
        if abs(diff) <= tol:
            return EventSurpriseDirection.INLINE

        if self.higher_is_bullish:
            return EventSurpriseDirection.HAWKISH if diff > 0 else EventSurpriseDirection.DOVISH
        else:
            return EventSurpriseDirection.HAWKISH if diff < 0 else EventSurpriseDirection.DOVISH

    def is_released_as_of(self, curr_date, curr_time_utc=None) -> bool:
        from tradingagents.dataflows.forex_quality import utc_timestamp
        cutoff = _calendar_cutoff(curr_date, curr_time_utc)
        return (self.published_at_utc is not None
                and utc_timestamp(self.published_at_utc) <= cutoff
                and (self.known_at_utc is None or utc_timestamp(self.known_at_utc) <= cutoff))

    def clamp_to_as_of(self, curr_date, curr_time_utc=None):
        from tradingagents.dataflows.forex_quality import utc_timestamp
        cutoff = _calendar_cutoff(curr_date, curr_time_utc)
        actual = self.actual if self.is_released_as_of(curr_date, curr_time_utc) else None
        revised = self.revised_previous
        previous = self.previous
        if self.revision_at_utc is not None and utc_timestamp(self.revision_at_utc) > cutoff:
            revised = None
            previous = self.previous_before_revision
        if self.known_at_utc is not None and utc_timestamp(self.known_at_utc) > cutoff:
            return replace(self, actual=None, forecast=None, previous=None, revised_previous=None)
        return replace(self, actual=actual, previous=previous, revised_previous=revised)


def _calendar_cutoff(curr_date, curr_time_utc=None):
    """A date without an intraday time means UTC midnight, never end of day."""
    if isinstance(curr_date, datetime):
        if curr_date.tzinfo is None:
            raise ValueError("calendar cutoff requires a timezone")
        return curr_date.astimezone(timezone.utc)
    text = str(curr_date)
    if len(text) == 10:
        text += "T" + (curr_time_utc or "00:00") + "+00:00"
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("calendar cutoff requires a timezone")
    return stamp.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Calendar Risk & Assessment Data Structures
# ---------------------------------------------------------------------------


@dataclass
class EventRiskAssessment:
    """Pre-event and post-event risk evaluation for a currency pair."""

    pair: str
    base_currency: str
    quote_currency: str
    as_of_date: str
    risk_regime: EventRiskRegime
    recommendation: TradingActionRecommendation
    blackout_active: bool
    hours_to_next_high_impact: float | None
    next_high_impact_event: EconomicEvent | None
    upcoming_events: list[EconomicEvent]
    recent_events: list[EconomicEvent]
    timing_blackout: bool = False


@dataclass
class PairEventBias:
    """Net directional economic momentum bias for a currency pair."""

    pair: str
    base_currency: str
    quote_currency: str
    as_of_date: str
    base_hawkish_count: int
    base_dovish_count: int
    quote_hawkish_count: int
    quote_dovish_count: int
    net_base_score: int             # base_hawkish - base_dovish
    net_quote_score: int            # quote_hawkish - quote_dovish
    directional_bias: str           # "BULLISH_BASE", "BEARISH_BASE", "NEUTRAL"
    confidence: float
    summary: str


# ---------------------------------------------------------------------------
# Calendar Queries, Filtering & Point-in-Time Clamping
# ---------------------------------------------------------------------------


def get_calendar_events_for_pair(
    symbol: str,
    start_date: str,
    end_date: str,
    curr_date: str | None = None,
    min_impact: EventImpact | None = None,
    curr_time_utc: str | None = None,
) -> list[EconomicEvent]:
    """Query economic calendar events relevant to a Forex pair within a date window.

    Args:
        symbol: Forex pair symbol (e.g. "EURUSD", "GBP/JPY", "AUDCAD").
        start_date: Window start date (YYYY-MM-DD).
        end_date: Window end date (YYYY-MM-DD).
        curr_date: Point-in-time reference date. If provided, any event with
            date > curr_date has its ``actual`` value masked to prevent lookahead leakage.
        min_impact: Optional minimum impact threshold (e.g. EventImpact.HIGH).

    Returns:
        List of relevant ``EconomicEvent`` instances, chronologically ordered and PIT-safe.
    """
    pair = get_forex_pair(symbol)
    if pair is not None:
        base_curr = pair.base_currency
        quote_curr = pair.quote_currency
    else:
        # Fallback to symbol extraction
        clean = ForexSymbolMap.clean_symbol(symbol)
        if len(clean) >= 6:
            base_curr = clean[:3].upper()
            quote_curr = clean[3:6].upper()
        else:
            base_curr = clean.upper()
            quote_curr = ""

    relevant_currencies = {base_curr, quote_curr} if quote_curr else {base_curr}

    impact_order = {
        EventImpact.LOW: 1,
        EventImpact.MEDIUM: 2,
        EventImpact.HIGH: 3,
    }
    min_val = impact_order.get(min_impact, 1) if min_impact else 1

    from tradingagents.dataflows.trading_economics import TradingEconomicsCalendar
    cutoff = _calendar_cutoff(curr_date, curr_time_utc) if curr_date is not None else None
    all_events = TradingEconomicsCalendar().query(symbol, start_date, end_date, as_of=cutoff)
    matched: list[EconomicEvent] = []

    for ev in all_events:
        if ev.currency not in relevant_currencies:
            continue
        if not (start_date <= ev.date <= end_date):
            continue
        if impact_order.get(ev.impact, 1) < min_val:
            continue

        # Point-in-time clamping
        if curr_date is not None:
            ev = ev.clamp_to_as_of(curr_date, curr_time_utc)

        matched.append(ev)

    return sorted(matched, key=lambda ev: (ev.date, ev.time_utc or "", ev.event_id))


# ---------------------------------------------------------------------------
# Pre-News / Post-News Risk Regime & Blackout Window Logic
# ---------------------------------------------------------------------------


def evaluate_event_risk_regime(
    symbol: str,
    curr_date: str,
    curr_time_utc: str | None = None,
    lookahead_hours: float = 48.0,
    lookback_hours: float = 24.0,
) -> EventRiskAssessment:
    """Evaluate economic event risk regime and blackout conditions for a pair.

    Rules:
    - High-impact event (Tier-1) today or within 24 hours:
      * Regime = HIGH_RISK
      * Blackout = Active
      * Recommendation = AVOID_NEW_POSITIONS / TIGHTEN_STOPS
      * Spread expansion warning flagged.
    - Tier-1 event 24h-48h away or Medium-impact event today:
      * Regime = MODERATE_RISK
      * Blackout = Inactive (Approaching)
      * Recommendation = TIGHTEN_STOPS
    - No high-impact events within 48h:
      * Regime = LOW_RISK
      * Recommendation = NORMAL_TRADING

    Args:
        symbol: Forex pair symbol.
        curr_date: Current trade date (YYYY-MM-DD).
        curr_time_utc: Optional current UTC time (HH:MM).
        lookahead_hours: Forward window in hours (default 48.0).
        lookback_hours: Historical window in hours (default 24.0).
    """
    pair = get_forex_pair(symbol)
    base_curr = pair.base_currency if pair else symbol[:3].upper()
    quote_curr = pair.quote_currency if pair else symbol[3:6].upper()

    curr_dt = _calendar_cutoff(curr_date, curr_time_utc)
    start_dt = curr_dt - timedelta(hours=lookback_hours)
    end_dt = curr_dt + timedelta(hours=lookahead_hours)

    start_date_str = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")

    events = get_calendar_events_for_pair(
        symbol=symbol,
        start_date=start_date_str,
        end_date=end_date_str,
        curr_date=curr_date,
        curr_time_utc=curr_time_utc,
    )

    upcoming_events: list[EconomicEvent] = []
    recent_events: list[EconomicEvent] = []

    hours_to_next_high: float | None = None
    next_high_event: EconomicEvent | None = None

    for ev in events:
        ev_dt_str = f"{ev.date}T{ev.time_utc or '00:00'}+00:00"
        try:
            ev_dt = datetime.fromisoformat(ev_dt_str)
        except ValueError:
            ev_dt = datetime.fromisoformat(f"{ev.date}T12:00:00+00:00")

        delta_hours = (ev_dt - curr_dt).total_seconds() / 3600.0

        if delta_hours > lookahead_hours or delta_hours < -lookback_hours:
            continue
        if delta_hours >= 0:
            upcoming_events.append(ev)
            if ev.impact == EventImpact.HIGH and (hours_to_next_high is None or delta_hours < hours_to_next_high):
                hours_to_next_high = delta_hours
                next_high_event = ev
        else:
            recent_events.append(ev)

    unknown_high_today = any(ev.impact == EventImpact.HIGH and ev.date == curr_dt.date().isoformat() and ev.time_utc is None for ev in events)
    recent_high = any(ev.impact == EventImpact.HIGH and ev.time_utc is not None
                      and 0 <= (curr_dt - _calendar_cutoff(ev.date, ev.time_utc)).total_seconds() <= 15 * 60
                      for ev in recent_events)
    # Classify Risk Regime
    blackout_active = False
    if unknown_high_today or recent_high or (hours_to_next_high is not None and hours_to_next_high <= 24.0):
        risk_regime = EventRiskRegime.HIGH_RISK
        blackout_active = True
        recommendation = TradingActionRecommendation.AVOID_NEW_POSITIONS
    elif (hours_to_next_high is not None and hours_to_next_high <= 48.0) or any(
        ev.date == curr_dt.date().isoformat() and ev.impact == EventImpact.MEDIUM for ev in upcoming_events
    ):
        risk_regime = EventRiskRegime.MODERATE_RISK
        recommendation = TradingActionRecommendation.TIGHTEN_STOPS
    else:
        risk_regime = EventRiskRegime.LOW_RISK
        recommendation = TradingActionRecommendation.NORMAL_TRADING

    return EventRiskAssessment(
        pair=pair.symbol if pair else symbol.upper(),
        base_currency=base_curr,
        quote_currency=quote_curr,
        as_of_date=curr_date,
        risk_regime=risk_regime,
        recommendation=recommendation,
        blackout_active=blackout_active,
        hours_to_next_high_impact=hours_to_next_high,
        next_high_impact_event=next_high_event,
        upcoming_events=upcoming_events,
        recent_events=recent_events,
        timing_blackout=unknown_high_today or recent_high,
    )


# ---------------------------------------------------------------------------
# Economic Surprise Momentum & Directional Bias
# ---------------------------------------------------------------------------


def calculate_pair_event_bias(
    symbol: str,
    curr_date: str,
    lookback_days: int = 30,
) -> PairEventBias:
    """Calculate economic surprise momentum and net pair directional bias.

    Examines recent released events over the last ``lookback_days`` up to ``curr_date``.
    - Hawkish surprise on Base currency supports pair appreciation (Bullish Base).
    - Hawkish surprise on Quote currency supports pair depreciation (Bearish Base).
    - Dovish surprise on Base currency weakens pair (Bearish Base).
    - Dovish surprise on Quote currency supports pair (Bullish Base).

    Combines momentum to produce a deterministic bias: BULLISH_BASE, BEARISH_BASE, or NEUTRAL.
    """
    pair = get_forex_pair(symbol)
    base_curr = pair.base_currency if pair else symbol[:3].upper()
    quote_curr = pair.quote_currency if pair else symbol[3:6].upper()

    curr_dt = _calendar_cutoff(curr_date).date()
    start_dt = curr_dt - timedelta(days=lookback_days)
    start_date_str = start_dt.isoformat()

    events = get_calendar_events_for_pair(
        symbol=symbol,
        start_date=start_date_str,
        end_date=curr_dt.isoformat(),
        curr_date=curr_date,
    )

    base_hawkish = 0
    base_dovish = 0
    quote_hawkish = 0
    quote_dovish = 0

    for ev in events:
        if ev.actual is None or ev.forecast is None:
            continue
        direction = ev.surprise_direction
        if ev.currency == base_curr:
            if direction == EventSurpriseDirection.HAWKISH:
                base_hawkish += 1
            elif direction == EventSurpriseDirection.DOVISH:
                base_dovish += 1
        elif ev.currency == quote_curr:
            if direction == EventSurpriseDirection.HAWKISH:
                quote_hawkish += 1
            elif direction == EventSurpriseDirection.DOVISH:
                quote_dovish += 1

    net_base = base_hawkish - base_dovish
    net_quote = quote_hawkish - quote_dovish
    # Pair net score: positive favors base, negative favors quote
    pair_net_score = net_base - net_quote

    if pair_net_score > 0:
        directional_bias = "BULLISH_BASE"
        confidence = min(0.4 + 0.15 * pair_net_score, 0.85)
        summary = (
            f"Net positive surprise momentum on {base_curr} (+{net_base}) relative to "
            f"{quote_curr} (+{net_quote}) supports {base_curr} strength."
        )
    elif pair_net_score < 0:
        directional_bias = "BEARISH_BASE"
        confidence = min(0.4 + 0.15 * abs(pair_net_score), 0.85)
        summary = (
            f"Net positive surprise momentum on {quote_curr} (+{net_quote}) relative to "
            f"{base_curr} (+{net_base}) supports {quote_curr} strength (bearish for {base_curr}/{quote_curr})."
        )
    else:
        directional_bias = "NEUTRAL"
        confidence = 0.35
        summary = f"Economic surprise momentum is balanced between {base_curr} and {quote_curr}."

    return PairEventBias(
        pair=pair.symbol if pair else symbol.upper(),
        base_currency=base_curr,
        quote_currency=quote_curr,
        as_of_date=curr_date,
        base_hawkish_count=base_hawkish,
        base_dovish_count=base_dovish,
        quote_hawkish_count=quote_hawkish,
        quote_dovish_count=quote_dovish,
        net_base_score=net_base,
        net_quote_score=net_quote,
        directional_bias=directional_bias,
        confidence=round(confidence, 2),
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Formatting Helpers
# ---------------------------------------------------------------------------


def format_economic_calendar_table(events: Sequence[EconomicEvent]) -> str:
    """Format a list of economic events as a clean Markdown table."""
    if not events:
        return "No scheduled economic events found for the requested period.\n"

    lines = [
        "| Date (UTC) | Time | Curr | Impact | Event | Actual | Forecast | Previous | Surprise | Direction | Source | Observed UTC | Published UTC | Revision UTC |",
        "| :--- | :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :--- | :--- |",
    ]

    for ev in events:
        time_str = ev.time_utc or "All Day"
        impact_icon = {
            EventImpact.HIGH: "🔴 HIGH",
            EventImpact.MEDIUM: "🟡 MED",
            EventImpact.LOW: "⚪ LOW",
        }.get(ev.impact, str(ev.impact))

        act_str = f"{ev.actual}{ev.unit}" if ev.actual is not None else "Pending"
        fc_str = f"{ev.forecast}{ev.unit}" if ev.forecast is not None else "-"
        prev_str = f"{ev.previous}{ev.unit}" if ev.previous is not None else "-"

        if ev.surprise is not None:
            surp_sign = "+" if ev.surprise > 0 else ""
            surp_str = f"{surp_sign}{ev.surprise:.2f}{ev.unit}"
        else:
            surp_str = "-"

        dir_icon = {
            EventSurpriseDirection.HAWKISH: "🟢 Hawkish",
            EventSurpriseDirection.DOVISH: "🔴 Dovish",
            EventSurpriseDirection.INLINE: "⚪ Inline",
            EventSurpriseDirection.NEUTRAL: "—",
        }.get(ev.surprise_direction, "—")

        lines.append(
            f"| {ev.date} | {time_str} | **{ev.currency}** | {impact_icon} | "
            f"{ev.title} | {act_str} | {fc_str} | {prev_str} | {surp_str} | {dir_icon} | "
            f"[{ev.source}]({ev.source_url}) | {ev.known_at_utc or 'unknown'} | "
            f"{ev.published_at_utc or 'unpublished'} | {ev.revision_at_utc or 'none'} |"
        )

    return "\n".join(lines)
