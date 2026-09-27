"""Forex trading session engine.

Models the 24/5 global Forex market using local session hours and IANA zones.
London, New York and Sydney shift with their own daylight-saving calendars.

Key features:
- Market open/close detection (Forex closes Friday 17:00 New York time, opens Sunday 17:00).
- Active session detection for any UTC timestamp.
- Major session overlap identification (London/NY peak liquidity, Tokyo/Sydney, London/Tokyo).
- Next session opening calculation across regular days and weekends.
- Pair-to-session relevance mapping (e.g. EURUSD -> London + New York).
- MarketRegime enum stub (to be fully integrated with indicators in Phase 5).
- Deterministic: pure Python standard library + ForexPair domain objects (zero network/LLM dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Union

from tradingagents.forex.domain import ForexPair, get_forex_pair


# ---------------------------------------------------------------------------
# MarketRegime — enum stub for regime classification (Phase 5)
# ---------------------------------------------------------------------------


from zoneinfo import ZoneInfo

class MarketRegime(str, Enum):
    """Forex market regime classification.

    This stub defines the canonical regimes. Phase 5 will implement the
    deterministic detection algorithms (ATR, ADX, Bollinger Band width, etc.).
    """

    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"

    @classmethod
    def from_string(cls, value: str) -> "MarketRegime":
        """Parse case-insensitive string → MarketRegime."""
        normalised = (value or "").strip().lower()
        for member in cls:
            if member.value == normalised or member.name.lower() == normalised:
                return member
        raise ValueError(
            f"Unknown market regime {value!r}. Valid values: "
            + ", ".join(m.value for m in cls)
        )


# ---------------------------------------------------------------------------
# TradingSession Enum
# ---------------------------------------------------------------------------


class TradingSession(str, Enum):
    """The four major global Forex trading sessions."""

    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"

    @classmethod
    def from_string(cls, value: str) -> "TradingSession":
        """Parse case-insensitive string → TradingSession."""
        normalised = (value or "").strip().upper().replace(" ", "_")
        for member in cls:
            if member.value == normalised or member.name == normalised:
                return member
        raise ValueError(
            f"Unknown trading session {value!r}. Valid values: "
            + ", ".join(m.value for m in cls)
        )

    @property
    def info(self) -> "SessionInfo":
        """Detailed session metadata."""
        return _SESSION_INFOS[self]

    @property
    def open_utc(self) -> time:
        """UTC session opening time."""
        return self.info.open_utc

    @property
    def close_utc(self) -> time:
        """UTC session closing time."""
        return self.info.close_utc

    @property
    def major_pairs(self) -> tuple[str, ...]:
        """Major pairs traded heavily during this session."""
        return self.info.major_pairs

    @property
    def typical_spread_factor(self) -> float:
        """Relative spread factor (1.0 = tightest baseline, >1.0 = wider)."""
        return self.info.typical_spread_factor


# ---------------------------------------------------------------------------
# SessionInfo Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    """Session metadata. UTC clock fields are nominal winter values; use session_window for a date."""

    session: TradingSession
    name: str
    open_utc: time
    close_utc: time
    major_pairs: tuple[str, ...]
    typical_spread_factor: float
    description: str

    def is_active(self, dt_utc: datetime) -> bool:
        """Check whether this session is open at dt_utc (respecting market hours)."""
        return self.session in get_active_sessions(dt_utc)


# ---------------------------------------------------------------------------
# Canonical Session Registry
# ---------------------------------------------------------------------------

_SESSION_INFOS: dict[TradingSession, SessionInfo] = {
    TradingSession.SYDNEY: SessionInfo(
        session=TradingSession.SYDNEY,
        name="Sydney",
        open_utc=time(22, 0),
        close_utc=time(7, 0),
        major_pairs=("AUDUSD", "NZDUSD", "AUDJPY", "NZDJPY", "AUDNZD"),
        typical_spread_factor=1.6,
        description="Sydney / Pacific session. Initiates the 24h trading day; primary liquidity for AUD and NZD.",
    ),
    TradingSession.TOKYO: SessionInfo(
        session=TradingSession.TOKYO,
        name="Tokyo",
        open_utc=time(0, 0),
        close_utc=time(9, 0),
        major_pairs=("USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY"),
        typical_spread_factor=1.3,
        description="Tokyo / Asian session. Major liquidity for Japanese Yen and regional Asian currencies.",
    ),
    TradingSession.LONDON: SessionInfo(
        session=TradingSession.LONDON,
        name="London",
        open_utc=time(8, 0),
        close_utc=time(17, 0),
        major_pairs=("EURUSD", "GBPUSD", "USDCHF", "EURGBP", "EURJPY", "GBPJPY"),
        typical_spread_factor=1.0,
        description="London / European session. Deepest global liquidity, highest volume, tightest spreads.",
    ),
    TradingSession.NEW_YORK: SessionInfo(
        session=TradingSession.NEW_YORK,
        name="New York",
        open_utc=time(13, 0),
        close_utc=time(22, 0),
        major_pairs=("EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD"),
        typical_spread_factor=1.0,
        description="New York / North American session. High volatility, US economic releases, London overlap.",
    ),
}


# ---------------------------------------------------------------------------
# Session Overlap Representation
# ---------------------------------------------------------------------------


class SessionOverlap(str):
    """Session overlap string that supports both full and abbreviated matching.

    Examples:
        overlap = SessionOverlap("London/New York", "London/NY")
        overlap == "London/New York"  # True
        overlap == "London/NY"        # True
        "London" in overlap           # True
    """

    _short_name: str

    def __new__(cls, full_name: str, short_name: str = "") -> "SessionOverlap":
        obj = super().__new__(cls, full_name)
        obj._short_name = short_name or full_name
        return obj

    @property
    def short_name(self) -> str:
        return self._short_name

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        if isinstance(other, str) and other == self._short_name:
            return True
        return False

    def __hash__(self) -> int:
        return super().__hash__()


OVERLAP_LONDON_NY = SessionOverlap("London/New York", "London/NY")
OVERLAP_TOKYO_SYDNEY = SessionOverlap("Tokyo/Sydney", "Tokyo/Sydney")
OVERLAP_LONDON_TOKYO = SessionOverlap("London/Tokyo", "London/Tokyo")


# ---------------------------------------------------------------------------
# Global Holidays
# ---------------------------------------------------------------------------

_GLOBAL_FOREX_HOLIDAYS = {
    (1, 1),   # New Year's Day
    (12, 25), # Christmas Day
}


# ---------------------------------------------------------------------------
# Helper: Ensure UTC Datetime
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize datetime to UTC. If naive, assume UTC as per system convention."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Market Open / Close
# ---------------------------------------------------------------------------


def is_holiday(dt: datetime) -> bool:
    """Check if the given date is a global Forex holiday (New Year, Christmas)."""
    utc_dt = _ensure_utc(dt)
    return (utc_dt.month, utc_dt.day) in _GLOBAL_FOREX_HOLIDAYS


def is_weekend(dt: datetime) -> bool:
    """Check if the given time falls within the Forex weekend closure.

    Forex closes Friday at 17:00 New York time and reopens Sunday at 17:00 there.
    """
    local = _ensure_utc(dt).astimezone(ZoneInfo("America/New_York"))
    return (local.weekday() == 5
            or (local.weekday() == 4 and local.time() >= time(17))
            or (local.weekday() == 6 and local.time() < time(17)))



def is_market_open(dt: datetime) -> bool:
    """Check if the global Forex market is open for trading.

    Forex trades from Sunday 17:00 through Friday 17:00 America/New_York,
    except on major global holidays (New Year's Day, Christmas Day).
    """
    if is_holiday(dt):
        return False
    return not is_weekend(dt)


# ---------------------------------------------------------------------------
# Active Sessions
# ---------------------------------------------------------------------------


def _is_time_in_range(t: time, start: time, end: time) -> bool:
    """Check whether time t falls within [start, end), handling midnight wrap."""
    if start <= end:
        return start <= t < end
    # Wraps midnight (e.g. 22:00 to 07:00)
    return t >= start or t < end


_LOCAL_SESSIONS = {
    TradingSession.SYDNEY: ("Australia/Sydney", time(8), time(17)),
    TradingSession.TOKYO: ("Asia/Tokyo", time(9), time(18)),
    TradingSession.LONDON: ("Europe/London", time(8), time(17)),
    TradingSession.NEW_YORK: ("America/New_York", time(8), time(17)),
}


def session_window(session, local_date):
    """Actual UTC boundaries for a session's local date, including DST."""
    zone, start, end = _LOCAL_SESSIONS[session]
    opened = datetime.combine(local_date, start, ZoneInfo(zone)).astimezone(timezone.utc)
    closed = datetime.combine(local_date, end, ZoneInfo(zone)).astimezone(timezone.utc)
    return opened, closed


def get_active_sessions(
    dt: datetime,
    *,
    check_market_open: bool = True,
) -> list[TradingSession]:
    """Return the list of currently active trading sessions for dt (in UTC).

    Args:
        dt: The timestamp to evaluate (UTC).
        check_market_open: If True (default), returns [] if the market is closed
                           (weekends or global holidays). If False, evaluates
                           session hours strictly by time-of-day.

    Returns:
        List of active TradingSession enum members, ordered by typical session open.
    """
    if check_market_open and not is_market_open(dt):
        return []

    utc_dt = _ensure_utc(dt)
    active: list[TradingSession] = []
    # Evaluate in canonical sequence: Sydney, Tokyo, London, New York
    for session in (
        TradingSession.SYDNEY,
        TradingSession.TOKYO,
        TradingSession.LONDON,
        TradingSession.NEW_YORK,
    ):
        zone, opened, closed = _LOCAL_SESSIONS[session]
        local = utc_dt.astimezone(ZoneInfo(zone))
        if _is_time_in_range(local.time(), opened, closed):
            active.append(session)

    return active


# ---------------------------------------------------------------------------
# Session Overlaps
# ---------------------------------------------------------------------------


def get_session_overlaps(
    dt: datetime,
    *,
    check_market_open: bool = True,
) -> list[SessionOverlap]:
    """Return active session overlaps for dt (in UTC).

    Overlaps provide the highest liquidity and volatility windows:
    - London / New York: 13:00 - 17:00 UTC (peak global liquidity)
    - Tokyo / Sydney:   00:00 - 07:00 UTC (Asian/Pacific overlap)
    - London / Tokyo:   08:00 - 09:00 UTC (European open transition)

    Returns:
        List of active SessionOverlap objects (which match both full and short strings).
    """
    active = get_active_sessions(dt, check_market_open=check_market_open)
    if len(active) < 2:
        return []

    overlaps: list[SessionOverlap] = []

    # London / New York overlap
    if TradingSession.LONDON in active and TradingSession.NEW_YORK in active:
        overlaps.append(OVERLAP_LONDON_NY)

    # Tokyo / Sydney overlap
    if TradingSession.TOKYO in active and TradingSession.SYDNEY in active:
        overlaps.append(OVERLAP_TOKYO_SYDNEY)

    # London / Tokyo overlap
    if TradingSession.LONDON in active and TradingSession.TOKYO in active:
        overlaps.append(OVERLAP_LONDON_TOKYO)

    return overlaps


# ---------------------------------------------------------------------------
# Next Session Open
# ---------------------------------------------------------------------------


def _is_valid_session_open_day(session: TradingSession, cand_dt: datetime) -> bool:
    """Verify if session opens on cand_dt's weekday and isn't closed by holiday."""
    if is_holiday(cand_dt):
        return False

    weekday = cand_dt.weekday()  # Mon=0 ... Sun=6
    # Sydney opens at 22:00 on Sun, Mon, Tue, Wed, Thu.
    # On Friday at 22:00 UTC the market closes (no Sydney open).
    # On Saturday at 22:00 UTC the market remains closed.
    if session == TradingSession.SYDNEY:
        return weekday in (6, 0, 1, 2, 3)

    # Tokyo (00:00), London (08:00), New York (13:00) open Monday through Friday.
    return weekday in (0, 1, 2, 3, 4)


def _get_next_open_for_single_session(
    dt_utc: datetime,
    session: TradingSession,
) -> datetime:
    """Find the next open datetime for a single specific session."""
    zone = ZoneInfo(_LOCAL_SESSIONS[session][0])
    for day_offset in range(10):
        local_day = dt_utc.astimezone(zone).date() + timedelta(days=day_offset)
        if local_day.weekday() >= 5:
            continue
        candidate, closed = session_window(session, local_day)
        # The Sydney session may start before the global Sunday opening.
        ny = candidate.astimezone(ZoneInfo("America/New_York"))
        if ny.weekday() == 6 and ny.time() < time(17):
            candidate = datetime.combine(ny.date(), time(17), ny.tzinfo).astimezone(timezone.utc)
        if candidate > dt_utc and candidate < closed and is_market_open(candidate):
            return candidate
    raise RuntimeError(f"Could not determine next session open for {session}")



def get_next_session_open(
    dt: datetime,
    session: Optional[TradingSession] = None,
) -> datetime:
    """Calculate the next session opening datetime in UTC.

    Args:
        dt: The reference timestamp (UTC).
        session: If specified, finds the next opening of that specific session.
                 If None, finds the earliest upcoming session opening among all 4 sessions.

    Returns:
        Next session opening datetime with timezone preserved (or UTC).
    """
    utc_dt = _ensure_utc(dt)

    if session is not None:
        return _get_next_open_for_single_session(utc_dt, session)

    # If session is None, find next open among all 4 sessions and take earliest
    candidates = [
        _get_next_open_for_single_session(utc_dt, s)
        for s in (
            TradingSession.SYDNEY,
            TradingSession.TOKYO,
            TradingSession.LONDON,
            TradingSession.NEW_YORK,
        )
    ]
    return min(candidates)


# ---------------------------------------------------------------------------
# Pair to Session Relevance
# ---------------------------------------------------------------------------

_CURRENCY_TO_SESSIONS: dict[str, list[TradingSession]] = {
    "EUR": [TradingSession.LONDON],
    "GBP": [TradingSession.LONDON],
    "CHF": [TradingSession.LONDON],
    "USD": [TradingSession.NEW_YORK],
    "CAD": [TradingSession.NEW_YORK],
    "JPY": [TradingSession.TOKYO],
    "AUD": [TradingSession.SYDNEY, TradingSession.TOKYO],
    "NZD": [TradingSession.SYDNEY, TradingSession.TOKYO],
    "SGD": [TradingSession.TOKYO],
    "HKD": [TradingSession.TOKYO],
    "CNH": [TradingSession.TOKYO],
    "ZAR": [TradingSession.LONDON],
    "SEK": [TradingSession.LONDON],
    "NOK": [TradingSession.LONDON],
    "PLN": [TradingSession.LONDON],
    "MXN": [TradingSession.NEW_YORK],
}


def _extract_base_quote(pair: Union[ForexPair, str]) -> tuple[str, str]:
    """Extract base and quote currency codes from a ForexPair or string symbol."""
    if isinstance(pair, ForexPair):
        return pair.base_currency, pair.quote_currency

    # Try lookup in domain registry
    fp = get_forex_pair(pair)
    if fp is not None:
        return fp.base_currency, fp.quote_currency

    # Parse 6-character clean symbol fallback
    clean = str(pair).strip().upper().replace("/", "").replace("=X", "")
    if len(clean) >= 6:
        return clean[:3], clean[3:6]

    return clean, ""



def get_relevant_sessions_for_pair(
    pair: Union[ForexPair, str],
) -> list[TradingSession]:
    """Return all trading sessions inherently relevant to this currency pair."""
    base, quote = _extract_base_quote(pair)
    sessions: list[TradingSession] = []

    for curr in (base, quote):
        for s in _CURRENCY_TO_SESSIONS.get(curr, []):
            if s not in sessions:
                sessions.append(s)

    # If no specific mapping found, default to London and New York (major global liquidity)
    if not sessions:
        sessions = [TradingSession.LONDON, TradingSession.NEW_YORK]

    return sessions


def session_for_pair(
    pair: Union[ForexPair, str],
    dt: Optional[datetime] = None,
    *,
    active_only: bool = False,
) -> list[TradingSession]:
    """Return the trading sessions most relevant for a given currency pair.

    Args:
        pair: ForexPair domain object or symbol string (e.g. "EURUSD").
        dt: Optional UTC timestamp. When provided, sessions are ordered so that
            any currently active relevant session is listed first.
        active_only: If True, returns only those relevant sessions that are currently
                     active at dt (returns [] if closed). Default is False.

    Returns:
        List of relevant TradingSession enum members.
    """
    relevant = get_relevant_sessions_for_pair(pair)

    if dt is None:
        return relevant

    active = get_active_sessions(dt)

    if active_only:
        return [s for s in relevant if s in active]

    # Prioritize active sessions first, then remaining relevant sessions
    active_part = [s for s in relevant if s in active]
    inactive_part = [s for s in relevant if s not in active]
    return active_part + inactive_part


def is_pair_in_prime_session(
    pair: Union[ForexPair, str],
    dt: datetime,
) -> bool:
    """Check if at least one of the pair's prime trading sessions is currently active."""
    active = get_active_sessions(dt)
    relevant = get_relevant_sessions_for_pair(pair)
    return any(s in active for s in relevant)


def get_active_sessions_for_pair(
    pair: Union[ForexPair, str],
    dt: datetime,
) -> list[TradingSession]:
    """Convenience helper returning only the currently active sessions relevant to the pair."""
    return session_for_pair(pair, dt, active_only=True)


# ---------------------------------------------------------------------------
# Market Status Snapshot
# ---------------------------------------------------------------------------


def get_market_status(dt: Optional[datetime] = None) -> dict[str, Any]:
    """Generate a comprehensive dictionary snapshot of the current Forex market state.

    Suitable for agent prompts, CLI headers, and web dashboards.
    """
    ref_dt = _ensure_utc(dt or datetime.now(timezone.utc))
    market_open = is_market_open(ref_dt)
    active = get_active_sessions(ref_dt)
    overlaps = get_session_overlaps(ref_dt)

    next_open: Optional[datetime] = None
    if not market_open:
        next_open = get_next_session_open(ref_dt)

    return {
        "timestamp_utc": ref_dt.isoformat(),
        "is_market_open": market_open,
        "is_weekend": is_weekend(ref_dt),
        "is_holiday": is_holiday(ref_dt),
        "active_sessions": [s.value for s in active],
        "session_windows": {
            s.value: {
                "open_utc": session_window(s, ref_dt.astimezone(ZoneInfo(_LOCAL_SESSIONS[s][0])).date())[0].isoformat(),
                "close_utc": session_window(s, ref_dt.astimezone(ZoneInfo(_LOCAL_SESSIONS[s][0])).date())[1].isoformat(),
                "minutes_since_open": (ref_dt - session_window(s, ref_dt.astimezone(ZoneInfo(_LOCAL_SESSIONS[s][0])).date())[0]).total_seconds() / 60,
                "minutes_until_close": (session_window(s, ref_dt.astimezone(ZoneInfo(_LOCAL_SESSIONS[s][0])).date())[1] - ref_dt).total_seconds() / 60,
            } for s in active
        },
        "overlaps": [str(o) for o in overlaps],
        "next_session_open_utc": next_open.isoformat() if next_open else None,
    }


def format_session_summary(dt: Optional[datetime] = None) -> str:
    """Format a human-readable one-line summary of market state."""
    status = get_market_status(dt)
    if not status["is_market_open"]:
        next_open = status.get("next_session_open_utc") or "unknown"
        return f"Market CLOSED (Weekend/Holiday) | Next Open: {next_open}"

    active_str = ", ".join(status["active_sessions"]) or "None"
    overlap_str = f" | Overlap: {', '.join(status['overlaps'])}" if status["overlaps"] else ""
    return f"Market OPEN | Active: [{active_str}]{overlap_str}"
