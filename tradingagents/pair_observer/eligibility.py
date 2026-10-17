"""Fail-closed eligibility gates for technically qualified setups."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from tradingagents.pair_observer.models import EligibilityResult, NewsStatus, ReasonCode

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")


def in_observation_session(at_utc: datetime) -> bool:
    if at_utc.tzinfo is None or at_utc.utcoffset() is None:
        raise ValueError("at_utc must be timezone-aware")
    london = at_utc.astimezone(LONDON).time()
    new_york = at_utc.astimezone(NEW_YORK).time()
    return time(7, 0) <= london < time(17, 0) or time(8, 0) <= new_york < time(17, 0)


def evaluate_eligibility(
    *,
    at_utc: datetime,
    quote_age_seconds: float,
    max_quote_age_seconds: int,
    spread_pips: float,
    max_spread_pips: float,
    has_exposure: bool,
    news_status: NewsStatus,
) -> EligibilityResult:
    reasons: list[ReasonCode] = []
    if quote_age_seconds < 0:
        reasons.append(ReasonCode.FUTURE_MARKET_DATA)
    elif quote_age_seconds > max_quote_age_seconds:
        reasons.append(ReasonCode.STALE_MARKET_DATA)
    if spread_pips > max_spread_pips:
        reasons.append(ReasonCode.SPREAD_TOO_WIDE)
    if has_exposure:
        reasons.append(ReasonCode.EXPOSURE_PRESENT)
    if not in_observation_session(at_utc):
        reasons.append(ReasonCode.OUTSIDE_SESSION)
    if news_status is NewsStatus.BLOCKED:
        reasons.append(ReasonCode.NEWS_BLOCKED)
    elif news_status is NewsStatus.NEWS_UNVERIFIED:
        reasons.append(ReasonCode.NEWS_UNVERIFIED)
    blocking = set(reasons) - {ReasonCode.NEWS_UNVERIFIED}
    return EligibilityResult(eligible=not blocking, news_status=news_status, reason_codes=tuple(reasons))
