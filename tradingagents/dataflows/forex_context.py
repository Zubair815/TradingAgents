"""One UTC observation cutoff for Forex graph inputs and tool requests."""

from contextlib import suppress
from datetime import datetime, timedelta, timezone

from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex.calendar import _calendar_cutoff


def resolve_forex_cutoff(requested=None, trade_date="", run_as_of=""):
    """Pin model requests to the run; date-only historical requests mean midnight."""
    bound = _calendar_cutoff(run_as_of or trade_date) if run_as_of or trade_date else None
    if not requested:
        return bound
    requested_cutoff = _calendar_cutoff(requested)
    if bound is None:
        return requested_cutoff
    # A model repeating the run date is asking about this intraday run.
    if len(str(requested)) == 10 and requested_cutoff.date() == bound.date():
        return bound
    return min(requested_cutoff, bound)


def prepare_live_forex_context(symbol):
    """Observe sourced feeds first, then freeze the analysis cutoff.

    Calendar failure blocks the run. News is optional and exposes its absence
    through NEWS_UNAVAILABLE when queried by an analyst.
    """
    from tradingagents.dataflows.forex_news import fetch_forex_news
    from tradingagents.dataflows.trading_economics import TradingEconomicsCalendar

    today = datetime.now(timezone.utc).date()
    TradingEconomicsCalendar().refresh(symbol, (today-timedelta(days=31)).isoformat(),
                                     (today+timedelta(days=7)).isoformat())
    with suppress(DataInsufficientError):
        fetch_forex_news(symbol)
    return datetime.now(timezone.utc).isoformat()
