"""Tests for the Forex Trading Session Engine (Phase 2).

Validates:
- TradingSession enum members, string inheritance, case-insensitive parsing, metadata.
- SessionInfo dataclass and typical spread factor relationships.
- Forex 24/5 market hours: open midweek, Friday 22:00 UTC close, Saturday closed,
  Sunday 22:00 UTC open, global holidays (Christmas, New Year).
- Active session detection across the 24-hour UTC cycle.
- Session overlap identification (London/NY, Tokyo/Sydney, London/Tokyo) and dual-naming matching.
- Next session opening calculations from weekdays, weekends, and for specific sessions.
- Pair-to-session relevance mapping (EURUSD, USDJPY, AUDUSD, etc.).
- Prime session checking and active session filtering.
- MarketRegime enum stub.
- Market status snapshot and human-readable formatting.
"""

from datetime import datetime, time, timezone

import pytest

from tradingagents.forex.domain import get_forex_pair
from tradingagents.forex.sessions import (
    OVERLAP_LONDON_NY,
    OVERLAP_LONDON_TOKYO,
    OVERLAP_TOKYO_SYDNEY,
    MarketRegime,
    SessionInfo,
    TradingSession,
    format_session_summary,
    get_active_sessions,
    get_active_sessions_for_pair,
    get_market_status,
    get_next_session_open,
    get_relevant_sessions_for_pair,
    get_session_overlaps,
    is_holiday,
    is_market_open,
    is_pair_in_prime_session,
    is_weekend,
    session_for_pair,
)

# ---------------------------------------------------------------------------
# 1. TradingSession Enum & SessionInfo
# ---------------------------------------------------------------------------


class TestTradingSessionEnum:
    def test_all_four_members_exist(self):
        assert hasattr(TradingSession, "SYDNEY")
        assert hasattr(TradingSession, "TOKYO")
        assert hasattr(TradingSession, "LONDON")
        assert hasattr(TradingSession, "NEW_YORK")
        assert len(TradingSession) == 4

    def test_inherits_from_str(self):
        assert TradingSession.LONDON == "LONDON"
        assert TradingSession.NEW_YORK == "NEW_YORK"
        assert isinstance(TradingSession.TOKYO, str)

    def test_from_string_case_insensitive(self):
        assert TradingSession.from_string("london") == TradingSession.LONDON
        assert TradingSession.from_string("London") == TradingSession.LONDON
        assert TradingSession.from_string("NEW_YORK") == TradingSession.NEW_YORK
        assert TradingSession.from_string("new york") == TradingSession.NEW_YORK
        assert TradingSession.from_string("Sydney") == TradingSession.SYDNEY
        assert TradingSession.from_string("tokyo") == TradingSession.TOKYO

    def test_from_string_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown trading session"):
            TradingSession.from_string("FRANKFURT")

    def test_session_info_metadata(self):
        for s in TradingSession:
            info = s.info
            assert isinstance(info, SessionInfo)
            assert info.session == s
            assert isinstance(info.name, str) and len(info.name) > 0
            assert isinstance(info.open_utc, time)
            assert isinstance(info.close_utc, time)
            assert isinstance(info.major_pairs, tuple) and len(info.major_pairs) > 0
            assert info.typical_spread_factor > 0

    def test_property_delegates(self):
        assert TradingSession.LONDON.open_utc == time(8, 0)
        assert TradingSession.LONDON.close_utc == time(17, 0)
        assert TradingSession.NEW_YORK.open_utc == time(13, 0)
        assert TradingSession.NEW_YORK.close_utc == time(22, 0)
        assert TradingSession.TOKYO.open_utc == time(0, 0)
        assert TradingSession.TOKYO.close_utc == time(9, 0)
        assert TradingSession.SYDNEY.open_utc == time(22, 0)
        assert TradingSession.SYDNEY.close_utc == time(7, 0)

    def test_typical_spread_factors(self):
        # London and NY have deepest liquidity (tightest spreads = lowest factor)
        assert TradingSession.LONDON.typical_spread_factor == 1.0
        assert TradingSession.NEW_YORK.typical_spread_factor == 1.0
        # Tokyo and Sydney have wider spreads than London
        assert TradingSession.TOKYO.typical_spread_factor > 1.0
        assert TradingSession.SYDNEY.typical_spread_factor >= TradingSession.TOKYO.typical_spread_factor


# ---------------------------------------------------------------------------
# 2. Market Open / Close & Weekend / Holiday Awareness
# ---------------------------------------------------------------------------


class TestMarketOpenClosed:
    def test_midweek_regular_open(self):
        # Tuesday 2026-03-10 at 14:00 UTC -> market is open
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is True
        assert is_weekend(dt) is False
        assert is_holiday(dt) is False

    def test_midweek_asian_hours_open(self):
        # Wednesday 2026-03-11 at 02:00 UTC -> market is open
        dt = datetime(2026, 3, 11, 2, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is True
        assert is_weekend(dt) is False

    def test_friday_before_close_open(self):
        # Friday 2026-03-13 at 21:59:59 UTC -> market is open
        dt = datetime(2026, 3, 13, 20, 59, 59, tzinfo=timezone.utc)
        assert is_market_open(dt) is True
        assert is_weekend(dt) is False

    def test_friday_at_close_closed(self):
        # Friday 2026-03-13 at 22:00:00 UTC -> market closes
        dt = datetime(2026, 3, 13, 22, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False
        assert is_weekend(dt) is True

    def test_friday_after_close_closed(self):
        # Friday 2026-03-13 at 23:00:00 UTC -> market is closed
        dt = datetime(2026, 3, 13, 23, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False
        assert is_weekend(dt) is True

    def test_saturday_entire_day_closed(self):
        # Saturday 2026-03-14 at 12:00:00 UTC -> market is closed
        dt = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False
        assert is_weekend(dt) is True

    def test_sunday_before_open_closed(self):
        # Sunday 2026-03-15 at 21:59:59 UTC -> market is still closed
        dt = datetime(2026, 3, 15, 20, 59, 59, tzinfo=timezone.utc)
        assert is_market_open(dt) is False
        assert is_weekend(dt) is True

    def test_sunday_at_open_open(self):
        # Sunday 2026-03-15 at 22:00:00 UTC -> market opens for the week
        dt = datetime(2026, 3, 15, 22, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is True
        assert is_weekend(dt) is False

    def test_sunday_after_open_open(self):
        # Sunday 2026-03-15 at 22:30:00 UTC -> market is open
        dt = datetime(2026, 3, 15, 22, 30, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is True
        assert is_weekend(dt) is False

    def test_christmas_holiday_closed(self):
        # Christmas Day Dec 25, 2026 (Friday) at 10:00 UTC -> closed
        dt = datetime(2026, 12, 25, 10, 0, tzinfo=timezone.utc)
        assert is_holiday(dt) is True
        assert is_market_open(dt) is False

    def test_new_years_holiday_closed(self):
        # New Year's Day Jan 1, 2026 (Thursday) at 10:00 UTC -> closed
        dt = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        assert is_holiday(dt) is True
        assert is_market_open(dt) is False

    def test_tz_naive_treated_as_utc(self):
        # A naive datetime should be interpreted as UTC cleanly
        dt_naive = datetime(2026, 3, 10, 14, 0)
        assert is_market_open(dt_naive) is True


# ---------------------------------------------------------------------------
# 3. Active Sessions Across 24-Hour Cycle
# ---------------------------------------------------------------------------


class TestActiveSessions:
    def test_sydney_tokyo_night(self):
        # Tuesday at 03:00 UTC: Sydney (22-07) and Tokyo (00-09) are both active
        dt = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert TradingSession.SYDNEY in active
        assert TradingSession.TOKYO in active
        assert TradingSession.LONDON not in active
        assert TradingSession.NEW_YORK not in active

    def test_tokyo_only(self):
        # Tuesday at 07:30 UTC: Sydney closed at 07:00, Tokyo still open (00-09)
        dt = datetime(2026, 3, 10, 7, 30, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert active == [TradingSession.TOKYO]

    def test_tokyo_london_overlap(self):
        # Tuesday at 08:30 UTC: Tokyo (00-09) and London (08-17) overlap
        dt = datetime(2026, 3, 10, 8, 30, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert TradingSession.TOKYO in active
        assert TradingSession.LONDON in active
        assert TradingSession.NEW_YORK not in active

    def test_london_only(self):
        # Tuesday at 10:30 UTC: Tokyo closed at 09:00, London is sole active session
        dt = datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert active == [TradingSession.LONDON]

    def test_london_new_york_overlap(self):
        # Tuesday at 14:00 UTC: London (08-17) and New York (13-22) overlap
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert TradingSession.LONDON in active
        assert TradingSession.NEW_YORK in active
        assert TradingSession.TOKYO not in active
        assert TradingSession.SYDNEY not in active

    def test_new_york_only(self):
        # Tuesday at 18:00 UTC: London closed at 17:00, New York is active
        dt = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert active == [TradingSession.NEW_YORK]

    def test_sydney_only(self):
        # Tuesday at 22:30 UTC: New York closed at 22:00, Sydney opened at 22:00
        dt = datetime(2026, 3, 10, 22, 30, tzinfo=timezone.utc)
        active = get_active_sessions(dt)
        assert active == [TradingSession.SYDNEY]

    def test_weekend_returns_empty_by_default(self):
        # Saturday at 14:00 UTC: market is closed -> no active sessions
        dt = datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc)
        assert get_active_sessions(dt) == []

    def test_friday_after_close_returns_empty(self):
        # Friday at 23:00 UTC: market closed -> []
        dt = datetime(2026, 3, 13, 23, 0, tzinfo=timezone.utc)
        assert get_active_sessions(dt) == []

    def test_sunday_before_open_returns_empty(self):
        # Sunday at 20:00 UTC: market closed -> []
        dt = datetime(2026, 3, 15, 20, 0, tzinfo=timezone.utc)
        assert get_active_sessions(dt) == []

    def test_sunday_after_open_returns_sydney(self):
        # Sunday at 22:30 UTC: Sydney opens the trading week
        dt = datetime(2026, 3, 15, 22, 30, tzinfo=timezone.utc)
        assert get_active_sessions(dt) == [TradingSession.SYDNEY]

    def test_check_market_open_false_override(self):
        # When check_market_open=False, evaluates hours nominally even on Saturday
        dt = datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc)
        nominal = get_active_sessions(dt, check_market_open=False)
        assert TradingSession.LONDON in nominal
        assert TradingSession.NEW_YORK in nominal

    def test_session_info_is_active_helper(self):
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        assert TradingSession.LONDON.info.is_active(dt) is True
        assert TradingSession.NEW_YORK.info.is_active(dt) is True
        assert TradingSession.TOKYO.info.is_active(dt) is False


# ---------------------------------------------------------------------------
# 4. Session Overlaps
# ---------------------------------------------------------------------------


class TestSessionOverlaps:
    def test_london_ny_overlap_detected(self):
        # Tuesday at 14:00 UTC: London/NY overlap is active
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        overlaps = get_session_overlaps(dt)
        assert len(overlaps) == 1
        assert overlaps[0] == OVERLAP_LONDON_NY
        # Verify SessionOverlap satisfies both "London/New York" and "London/NY"
        assert overlaps[0] == "London/New York"
        assert overlaps[0] == "London/NY"
        assert "London/NY" in overlaps
        assert "London/New York" in overlaps

    def test_tokyo_sydney_overlap_detected(self):
        # Tuesday at 03:00 UTC: Tokyo/Sydney overlap is active
        dt = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)
        overlaps = get_session_overlaps(dt)
        assert len(overlaps) == 1
        assert overlaps[0] == OVERLAP_TOKYO_SYDNEY
        assert "Tokyo/Sydney" in overlaps

    def test_london_tokyo_overlap_detected(self):
        # Tuesday at 08:30 UTC: London/Tokyo overlap is active
        dt = datetime(2026, 3, 10, 8, 30, tzinfo=timezone.utc)
        overlaps = get_session_overlaps(dt)
        assert len(overlaps) == 1
        assert overlaps[0] == OVERLAP_LONDON_TOKYO
        assert "London/Tokyo" in overlaps

    def test_no_overlap_single_session(self):
        # Tuesday at 11:00 UTC: only London is active -> no overlap
        dt = datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc)
        assert get_session_overlaps(dt) == []

    def test_no_overlap_on_weekend(self):
        # Saturday at 14:00 UTC: market closed -> no overlap
        dt = datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc)
        assert get_session_overlaps(dt) == []


# ---------------------------------------------------------------------------
# 5. Next Session Open Calculations
# ---------------------------------------------------------------------------


class TestNextSessionOpen:
    def test_saturday_returns_sunday_sydney(self):
        # Saturday 2026-03-14 at 12:00 UTC: next market open is Sunday 22:00 UTC
        dt = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 15, 21, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_friday_night_returns_sunday_sydney(self):
        # Friday 2026-03-13 at 23:00 UTC (after close): next open is Sunday 22:00 UTC
        dt = datetime(2026, 3, 13, 23, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 15, 21, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_sunday_afternoon_returns_sunday_sydney(self):
        # Sunday 2026-03-15 at 18:00 UTC: next open is Sunday 22:00 UTC
        dt = datetime(2026, 3, 15, 18, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 15, 21, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_tuesday_morning_returns_london(self):
        # Tuesday 2026-03-10 at 06:00 UTC: Tokyo opened at 00:00; next is London at 08:00
        dt = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_tuesday_midday_returns_ny(self):
        # Tuesday 2026-03-10 at 10:00 UTC: London opened at 08:00; next is NY at 13:00
        dt = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_tuesday_afternoon_returns_sydney(self):
        # Tuesday 2026-03-10 at 15:00 UTC: NY opened at 13:00; next is Sydney at 22:00
        dt = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_tuesday_late_night_returns_tokyo(self):
        # Tuesday 2026-03-10 at 22:30 UTC: Sydney opened at 22:00; next is Tokyo at 00:00 Wed
        dt = datetime(2026, 3, 10, 22, 30, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt)
        expected = datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_specific_session_london_from_friday_night(self):
        # Friday 2026-03-13 at 20:00 UTC: London next opens on Monday at 08:00 UTC
        dt = datetime(2026, 3, 13, 20, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt, session=TradingSession.LONDON)
        expected = datetime(2026, 3, 16, 8, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_specific_session_sydney_from_friday(self):
        # Friday 2026-03-13 at 21:00 UTC: Sydney does NOT open Fri 22:00 (market close);
        # Sydney next opens on Sunday at 22:00 UTC!
        dt = datetime(2026, 3, 13, 21, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt, session=TradingSession.SYDNEY)
        expected = datetime(2026, 3, 15, 21, 0, tzinfo=timezone.utc)
        assert nxt == expected

    def test_specific_session_ny_midweek(self):
        # Wednesday 2026-03-11 at 10:00 UTC: NY next opens at 13:00 on Wednesday
        dt = datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc)
        nxt = get_next_session_open(dt, session=TradingSession.NEW_YORK)
        expected = datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc)
        assert nxt == expected


# ---------------------------------------------------------------------------
# 6. Pair-to-Session Relevance
# ---------------------------------------------------------------------------


class TestPairSessionRelevance:
    def test_eurusd_relevance(self):
        sessions = get_relevant_sessions_for_pair("EURUSD")
        assert TradingSession.LONDON in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_usdjpy_relevance(self):
        sessions = get_relevant_sessions_for_pair("USDJPY")
        assert TradingSession.TOKYO in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_audusd_relevance(self):
        sessions = get_relevant_sessions_for_pair("AUDUSD")
        assert TradingSession.SYDNEY in sessions
        assert TradingSession.TOKYO in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_gbpjpy_relevance(self):
        sessions = get_relevant_sessions_for_pair("GBPJPY")
        assert TradingSession.LONDON in sessions
        assert TradingSession.TOKYO in sessions

    def test_with_forex_pair_domain_object(self):
        pair = get_forex_pair("EURUSD")
        assert pair is not None
        sessions = session_for_pair(pair)
        assert TradingSession.LONDON in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_session_for_pair_dt_active_prioritized(self):
        # At Tuesday 14:00 UTC, both London and New York are active
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        sessions = session_for_pair("EURUSD", dt)
        assert TradingSession.LONDON in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_session_for_pair_active_only(self):
        # At Tuesday 10:30 UTC: London is active, NY is not yet active
        dt = datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc)
        active_for_pair = session_for_pair("EURUSD", dt, active_only=True)
        assert active_for_pair == [TradingSession.LONDON]

    def test_is_pair_in_prime_session_true(self):
        # Tuesday at 14:00 UTC is prime session for EURUSD
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        assert is_pair_in_prime_session("EURUSD", dt) is True

    def test_is_pair_in_prime_session_false_on_weekend(self):
        # Saturday at 14:00 UTC: market closed -> not in prime session
        dt = datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc)
        assert is_pair_in_prime_session("EURUSD", dt) is False

    def test_get_active_sessions_for_pair(self):
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        active = get_active_sessions_for_pair("EURUSD", dt)
        assert TradingSession.LONDON in active
        assert TradingSession.NEW_YORK in active


# ---------------------------------------------------------------------------
# 7. MarketRegime Stub
# ---------------------------------------------------------------------------


class TestMarketRegime:
    def test_regime_members(self):
        assert MarketRegime.TRENDING == "trending"
        assert MarketRegime.RANGING == "ranging"
        assert MarketRegime.HIGH_VOLATILITY == "high_volatility"
        assert MarketRegime.LOW_VOLATILITY == "low_volatility"

    def test_from_string(self):
        assert MarketRegime.from_string("trending") == MarketRegime.TRENDING
        assert MarketRegime.from_string("RANGING") == MarketRegime.RANGING
        assert MarketRegime.from_string("high_volatility") == MarketRegime.HIGH_VOLATILITY
        assert MarketRegime.from_string("Low_Volatility") == MarketRegime.LOW_VOLATILITY

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Unknown market regime"):
            MarketRegime.from_string("CHAOTIC")


# ---------------------------------------------------------------------------
# 8. Market Status Snapshot & Summary
# ---------------------------------------------------------------------------


class TestMarketStatus:
    def test_get_market_status_open(self):
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        status = get_market_status(dt)
        assert status["is_market_open"] is True
        assert status["is_weekend"] is False
        assert status["is_holiday"] is False
        assert "LONDON" in status["active_sessions"]
        assert "NEW_YORK" in status["active_sessions"]
        assert any("London" in o for o in status["overlaps"])
        assert status["next_session_open_utc"] is None

    def test_get_market_status_closed(self):
        dt = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
        status = get_market_status(dt)
        assert status["is_market_open"] is False
        assert status["is_weekend"] is True
        assert status["active_sessions"] == []
        assert status["next_session_open_utc"] is not None
        assert "2026-03-15T21:00:00" in status["next_session_open_utc"]

    def test_format_session_summary_open(self):
        dt = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        summary = format_session_summary(dt)
        assert "Market OPEN" in summary
        assert "LONDON" in summary
        assert "NEW_YORK" in summary

    def test_format_session_summary_closed(self):
        dt = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
        summary = format_session_summary(dt)
        assert "Market CLOSED" in summary
        assert "2026-03-15" in summary
