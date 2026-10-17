from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tradingagents.dataflows.forex_quality import validate_candles
from tradingagents.forex.domain import Timeframe as ForexTimeframe
from tradingagents.pair_observer.confirmations import confirmation_type
from tradingagents.pair_observer.eligibility import evaluate_eligibility
from tradingagents.pair_observer.models import (
    AlertKind,
    Candle,
    Direction,
    EligibilityResult,
    LevelKind,
    LevelState,
    MarketLevel,
    NewsStatus,
    ObserverAlert,
    ObserverSnapshotEnvelope,
    QuoteSnapshot,
    ReasonCode,
    SetupState,
    SetupType,
    Timeframe,
    WeeklyReview,
)
from tradingagents.pair_observer.mt5_adapter import MT5PairObserverSource
from tradingagents.pair_observer.notifications import (
    NotificationDisabledError,
    NotificationUnavailableError,
    NullAlertSink,
    WindowsToastSink,
)
from tradingagents.pair_observer.repository import SQLiteObserverRepository
from tradingagents.pair_observer.service import PairObserverService
from tradingagents.pair_observer.settings import ObserverSettings, ObserverSettingsStore
from tradingagents.pair_observer.setups import breakout_retest_level, build_alert
from tradingagents.pair_observer.weekly_review import build_weekly_review, previous_trading_week
from web.server import app

NOW = datetime(2026, 1, 5, 9, 5, tzinfo=UTC)


def candle(open_: float, high: float, low: float, close: float, *, completed: bool = True) -> Candle:
    from datetime import timedelta

    return Candle(
        pair="EURUSD",
        timeframe=Timeframe.M5,
        open_time_utc=NOW - timedelta(minutes=5),
        close_time_utc=NOW,
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        completed=completed,
        source="SYNTHETIC",
    )


def make_alert() -> ObserverAlert:
    return ObserverAlert(
        alert_id="alert-1",
        setup_id="setup-1",
        pair="EURUSD",
        direction=Direction.LONG,
        setup_type=SetupType.TREND_CONTINUATION,
        kind=AlertKind.DEVELOPING,
        state=SetupState.DEVELOPING,
        created_at_utc=NOW,
        fingerprint="a" * 64,
        news_status=NewsStatus.NEWS_UNVERIFIED,
        headline="EURUSD developing — NEWS UNVERIFIED",
        message="Synthetic test alert",
    )


def make_snapshot(alert: ObserverAlert) -> ObserverSnapshotEnvelope:
    return ObserverSnapshotEnvelope(
        snapshot_id="snapshot-alert-1",
        alert_id=alert.alert_id,
        setup_id=alert.setup_id,
        pair=alert.pair,
        direction=alert.direction,
        setup_type=alert.setup_type,
        as_of_utc=NOW,
        configuration_fingerprint="b" * 64,
        software_revision="test-revision",
        evidence={"source": "synthetic"},
    ).with_integrity_hash()


class WeeklyReviewSource:
    _minutes = {
        Timeframe.M5: 5,
        Timeframe.M15: 15,
        Timeframe.H1: 60,
        Timeframe.H4: 240,
        Timeframe.D1: 1440,
    }

    def get_completed_candles(self, pair, timeframe, *, count, as_of_utc):
        step = timedelta(minutes=self._minutes[timeframe])
        candles = []
        for index in range(count):
            close_time = as_of_utc - step * (count - index)
            price = 1.1 + index * 0.00001 + sin(index / 4) * 0.0008
            close = price + (0.0002 if index % 2 else -0.0002)
            candles.append(
                Candle(
                    pair=pair,
                    timeframe=timeframe,
                    open_time_utc=close_time - step,
                    close_time_utc=close_time,
                    open=price,
                    high=max(price, close) + 0.0003,
                    low=min(price, close) - 0.0003,
                    close=close,
                    tick_volume=100,
                    completed=True,
                    source="SYNTHETIC",
                )
            )
        return tuple(candles)

    def get_tick_size(self, _pair):
        return 0.00001

    def get_quote(self, pair, *, as_of_utc, max_age_seconds):
        del max_age_seconds
        return QuoteSnapshot(
            pair=pair,
            broker_symbol=pair,
            bid=1.12,
            ask=1.1201,
            spread_pips=1.0,
            observed_at_utc=as_of_utc,
        )

    def has_open_exposure(self, pair, *, as_of_utc):
        del pair, as_of_utc
        return False


def test_settings_default_disabled_and_save_isolated(tmp_path: Path) -> None:
    path = tmp_path / "pair_observer" / "settings.json"
    store = ObserverSettingsStore(path)
    assert store.load().enabled is False
    saved = store.save(ObserverSettings(pair="gbp/usd", poll_seconds=15))
    assert saved.pair == "GBPUSD"
    assert store.load().poll_seconds == 15
    assert path.exists()
    assert not (tmp_path / "forex_journal.db").exists()


def test_settings_reject_unknown_six_letter_pair() -> None:
    with pytest.raises(ValidationError, match="supported Forex symbol catalogue"):
        ObserverSettings(pair="ABCDEF")


def test_settings_limit_observer_to_three_approved_pairs() -> None:
    assert ObserverSettings(pair="eur/usd").pair == "EURUSD"
    assert ObserverSettings(pair="GBPUSD").pair == "GBPUSD"
    assert ObserverSettings(pair="USDJPY").pair == "USDJPY"
    with pytest.raises(ValidationError, match="three observer pairs"):
        ObserverSettings(pair="AUDUSD")


def test_running_observer_rejects_setting_changes(monkeypatch) -> None:
    import web.observer_routes as routes

    service = MagicMock()
    service.status.return_value = {"worker_alive": True}
    monkeypatch.setattr(routes, "get_observer_service", lambda: service)

    with pytest.raises(HTTPException) as patch_error:
        routes.patch_settings(routes.SettingsPatch(pair="GBPUSD"))
    assert patch_error.value.status_code == 409
    with pytest.raises(HTTPException) as reset_error:
        routes.reset_settings()
    assert reset_error.value.status_code == 409
    service.settings_store.save.assert_not_called()


def test_market_closed_refuses_start_and_disables_observer(monkeypatch, tmp_path: Path) -> None:
    import tradingagents.pair_observer.service as service_module

    next_open = "2026-01-04T22:00:00+00:00"
    monkeypatch.setattr(
        service_module,
        "get_market_status",
        lambda _now: {"is_market_open": False, "next_session_open_utc": next_open},
    )
    store = ObserverSettingsStore(tmp_path / "settings.json")
    store.save(ObserverSettings(enabled=True))
    service = PairObserverService(
        source=MagicMock(),
        repository=SQLiteObserverRepository(tmp_path / "observer.db"),
        settings_store=store,
        alert_sink=NullAlertSink(),
    )

    assert service.start() is False
    status = service.status()
    assert status["state"] == "STOPPED"
    assert status["worker_alive"] is False
    assert status["status_code"] == "MARKET_CLOSED"
    assert next_open in status["status_message"]
    assert store.load().enabled is False


def test_eligibility_allows_unverified_news_but_labels_it() -> None:
    result = evaluate_eligibility(
        at_utc=NOW,
        quote_age_seconds=1,
        max_quote_age_seconds=120,
        spread_pips=1.1,
        max_spread_pips=3,
        has_exposure=False,
        news_status=NewsStatus.NEWS_UNVERIFIED,
    )
    assert result.eligible
    assert result.reason_codes == (ReasonCode.NEWS_UNVERIFIED,)


def test_eligibility_fails_closed_for_stale_quote_and_exposure() -> None:
    result = evaluate_eligibility(
        at_utc=NOW,
        quote_age_seconds=121,
        max_quote_age_seconds=120,
        spread_pips=1,
        max_spread_pips=3,
        has_exposure=True,
        news_status=NewsStatus.CLEAR,
    )
    assert not result.eligible
    assert ReasonCode.STALE_MARKET_DATA in result.reason_codes
    assert ReasonCode.EXPOSURE_PRESENT in result.reason_codes


def test_daily_validation_accepts_dst_and_broker_holidays_but_rejects_long_gap() -> None:
    import pandas as pd

    def frame(dates: list[str]):
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(dates, utc=True),
                "Open": [1.1] * len(dates),
                "High": [1.2] * len(dates),
                "Low": [1.0] * len(dates),
                "Close": [1.15] * len(dates),
                "Volume": [100] * len(dates),
            }
        )

    dst = frame(["2025-10-25T21:00:00Z", "2025-10-26T22:00:00Z"])
    holiday = frame(["2025-12-23T22:00:00Z", "2025-12-25T22:00:00Z"])
    assert len(validate_candles(dst, timeframe=ForexTimeframe.D1)) == 2
    assert len(validate_candles(holiday, timeframe=ForexTimeframe.D1)) == 2
    rejected = frame(["2025-01-01T22:00:00Z", "2025-01-07T22:00:00Z"])
    with pytest.raises(Exception, match="extended market gap"):
        validate_candles(rejected, timeframe=ForexTimeframe.D1)


def test_confirmation_requires_completed_m5() -> None:
    previous = candle(1.1010, 1.1015, 1.0995, 1.1000)
    current = candle(1.0998, 1.1020, 1.0990, 1.1018)
    assert confirmation_type((previous, current), Direction.LONG) == "BULLISH_ENGULFING"
    assert confirmation_type((previous, current.model_copy(update={"completed": False})), Direction.LONG) is None


def test_repository_is_atomic_deduplicated_and_integrity_checked(tmp_path: Path) -> None:
    repository = SQLiteObserverRepository(tmp_path / "observer.db")
    alert = make_alert()
    snapshot = make_snapshot(alert)
    assert repository.save_alert_with_snapshot(alert, snapshot)
    assert not repository.save_alert_with_snapshot(alert, snapshot)
    assert repository.get_snapshot(snapshot.snapshot_id).verify_integrity()
    assert len(repository.list_alerts()) == 1
    assert repository.list_setups()[0]["setup_id"] == alert.setup_id
    assert repository.get_setup(alert.setup_id)["state"] == SetupState.DEVELOPING.value
    assert repository.acknowledge(alert.alert_id, NOW.isoformat())
    assert repository.integrity_check() == "ok"
    assert not (tmp_path / "forex_journal.db").exists()


def test_previous_trading_week_uses_latest_completed_forex_week() -> None:
    start, end = previous_trading_week(datetime(2026, 10, 18, 12, tzinfo=UTC))
    assert start == datetime(2026, 10, 12, tzinfo=UTC)
    assert end == datetime(2026, 10, 19, tzinfo=UTC)

    midweek_start, _ = previous_trading_week(datetime(2026, 10, 14, 12, tzinfo=UTC))
    assert midweek_start == datetime(2026, 10, 5, tzinfo=UTC)


def test_weekly_review_is_deterministic_integrity_checked_and_idempotent(tmp_path: Path) -> None:
    reviewed_at = datetime(2026, 10, 18, 12, tzinfo=UTC)
    first = build_weekly_review(WeeklyReviewSource(), "EURUSD", now_utc=reviewed_at)
    second = build_weekly_review(WeeklyReviewSource(), "EURUSD", now_utc=reviewed_at)

    assert first == second
    assert first.verify_integrity()
    assert first.week_start_utc == datetime(2026, 10, 12, tzinfo=UTC)
    assert first.carried_levels
    assert {"D1", "H4", "H1", "M15", "M5"} == set(first.structures)

    repository = SQLiteObserverRepository(tmp_path / "observer.db")
    assert repository.save_weekly_review(first).review_id == first.review_id
    assert repository.save_weekly_review(second).review_id == first.review_id
    restored = repository.latest_weekly_review("EURUSD")
    assert isinstance(restored, WeeklyReview)
    assert restored == first
    assert repository.integrity_check() == "ok"


def test_next_live_cycle_automatically_loads_stored_weekly_context(tmp_path: Path) -> None:
    repository = SQLiteObserverRepository(tmp_path / "observer.db")
    review = build_weekly_review(
        WeeklyReviewSource(),
        "EURUSD",
        now_utc=datetime(2026, 10, 18, 12, tzinfo=UTC),
    )
    repository.save_weekly_review(review)
    service = PairObserverService(
        source=WeeklyReviewSource(),
        repository=repository,
        settings_store=ObserverSettingsStore(tmp_path / "settings.json"),
        alert_sink=NullAlertSink(),
    )

    service.run_cycle(datetime(2026, 10, 19, 8, tzinfo=UTC))

    assert service.status()["weekly_context_loaded"] is True
    assert service.status()["weekly_review_id"] == review.review_id
    assert any(
        item["source_type"].startswith("WEEKLY_REVIEW_")
        or item["source_type"] == "PREVIOUS_REVIEWED_WEEK"
        for item in service.levels()
    )


def test_cycle_state_survives_repository_restart(tmp_path: Path) -> None:
    path = tmp_path / "observer.db"
    repository = SQLiteObserverRepository(path)
    level = MarketLevel(
        level_id="persistent-level",
        pair="EURUSD",
        timeframe=Timeframe.H1,
        kind=LevelKind.SUPPORT,
        direction=Direction.LONG,
        proximal_price=1.1,
        distal_price=1.099,
        state=LevelState.ACTIVE,
        origin_open_utc=NOW,
        detected_at_utc=NOW,
        strength=0.7,
        source_type="PREVIOUS_DAY",
    )
    repository.save_cycle_state(
        pair="EURUSD",
        as_of_utc=NOW.isoformat(),
        structures={"H1": {"state": "BULLISH"}},
        levels=(level,),
        watermarks={"H1": NOW.isoformat()},
    )
    restarted = SQLiteObserverRepository(path)
    structures, levels = restarted.load_state("EURUSD")
    cycles, last_success = restarted.load_runtime_meta("EURUSD")
    assert structures["H1"]["state"] == "BULLISH"
    assert levels[0]["source_type"] == "PREVIOUS_DAY"
    assert cycles == 1
    assert last_success == NOW.isoformat()


def test_setup_identity_and_alert_dedup_are_stable_across_candles() -> None:
    level = MarketLevel(
        level_id="stable-level",
        pair="EURUSD",
        timeframe=Timeframe.H1,
        kind=LevelKind.SUPPORT,
        direction=Direction.LONG,
        proximal_price=1.1,
        distal_price=1.099,
        state=LevelState.ACTIVE,
        origin_open_utc=NOW,
        detected_at_utc=NOW,
        strength=0.7,
    )
    eligible = EligibilityResult(eligible=True, news_status=NewsStatus.NEWS_UNVERIFIED)
    bars = (candle(1.1005, 1.101, 1.1, 1.1002), candle(1.1002, 1.1008, 1.0999, 1.1004))
    first = build_alert(
        pair="EURUSD",
        direction=Direction.LONG,
        setup_type=SetupType.TREND_CONTINUATION,
        level=level,
        m5_candles=bars,
        eligibility=eligible,
        created_at_utc=NOW,
        developing_allowed=True,
        ready_allowed=True,
    )
    later = build_alert(
        pair="EURUSD",
        direction=Direction.LONG,
        setup_type=SetupType.TREND_CONTINUATION,
        level=level,
        m5_candles=bars,
        eligibility=eligible,
        created_at_utc=NOW.replace(minute=10),
        developing_allowed=True,
        ready_allowed=True,
    )
    assert first is not None and later is not None
    assert first.setup_id == later.setup_id
    assert first.fingerprint == later.fingerprint


def test_notification_sinks_report_disabled_and_unavailable_truthfully() -> None:
    alert = make_alert()
    with pytest.raises(NotificationDisabledError):
        NullAlertSink().deliver(alert)
    try:
        import winotify  # noqa: F401
    except ImportError:
        with pytest.raises(NotificationUnavailableError):
            WindowsToastSink().deliver(alert)


def test_breakout_retest_requires_close_beyond_level_and_held_retest() -> None:
    from tradingagents.pair_observer.models import LevelKind, LevelState, MarketLevel

    level = MarketLevel(
        level_id="resistance-1",
        pair="EURUSD",
        timeframe=Timeframe.H1,
        kind=LevelKind.SUPPLY,
        direction=Direction.SHORT,
        proximal_price=1.1000,
        distal_price=1.1010,
        state=LevelState.ACTIVE,
        origin_open_utc=NOW,
        detected_at_utc=NOW,
        strength=0.8,
    )
    breakout = candle(1.1000, 1.1030, 1.0995, 1.1020)
    retest = candle(1.1020, 1.1030, 1.1005, 1.1025)
    assert breakout_retest_level((level,), Direction.LONG, (breakout, retest)) == level
    failed = retest.model_copy(update={"close": 1.1005})
    assert breakout_retest_level((level,), Direction.LONG, (breakout, failed)) is None


def test_committed_notification_is_returned_for_only_one_retry(tmp_path: Path) -> None:
    repository = SQLiteObserverRepository(tmp_path / "observer.db")
    alert = make_alert()
    assert repository.save_alert_with_snapshot(alert, make_snapshot(alert))
    assert repository.pending_notifications() == [alert]
    repository.mark_notification(alert.alert_id, "FAILED")
    assert repository.pending_notifications() == []


def test_default_disabled_service_creates_no_worker_or_market_calls(tmp_path: Path) -> None:
    source = MagicMock()
    service = PairObserverService(
        source=source,
        repository=SQLiteObserverRepository(tmp_path / "observer.db"),
        settings_store=ObserverSettingsStore(tmp_path / "settings.json"),
        alert_sink=NullAlertSink(),
    )
    assert service.start_if_enabled() is False
    assert service.status()["worker_alive"] is False
    source.assert_not_called()


def test_mt5_adapter_calls_only_read_observer_methods() -> None:
    observer = MagicMock()
    observer.get_open_positions.return_value = []
    observer.get_pending_orders.return_value = []
    source = MT5PairObserverSource(observer)
    observer.get_symbol_info.return_value.point = 0.001
    assert source.get_tick_size("USDJPY") == 0.001
    assert source.has_open_exposure("EURUSD", as_of_utc=NOW) is False
    observer.get_open_positions.assert_called_once_with(symbol="EURUSD")
    observer.get_pending_orders.assert_called_once_with(symbol="EURUSD")
    forbidden = {"place_order", "send_order", "modify_order", "close_position"}
    assert forbidden.isdisjoint(MT5PairObserverSource.__dict__)


def test_observer_page_and_api_require_expected_boundaries(monkeypatch, tmp_path: Path) -> None:
    import web.observer_assembly as assembly

    monkeypatch.setenv("TRADINGAGENTS_PAIR_OBSERVER_HOME", str(tmp_path))
    assembly.reset_observer_for_tests()
    client = TestClient(app)
    client.get("/")
    page = client.get("/observer")
    assert page.status_code == 200
    assert "Market Observer" in page.text
    assert "/static/app.js" not in page.text
    status = client.get("/api/observer/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    settings = client.get("/api/observer/settings")
    assert settings.status_code == 200
    assert settings.json()["pair"] == "EURUSD"
    dashboard = client.get("/")
    assert 'href="/observer"' in dashboard.text
    assert 'class="tab" href="/observer"' not in dashboard.text
    assembly.reset_observer_for_tests()


def test_observer_api_rejects_missing_authentication(monkeypatch, tmp_path: Path) -> None:
    import web.observer_assembly as assembly

    monkeypatch.setenv("TRADINGAGENTS_PAIR_OBSERVER_HOME", str(tmp_path))
    assembly.reset_observer_for_tests()
    unauthenticated = TestClient(app).get("/api/observer/status")
    assert unauthenticated.status_code == 401
    assembly.reset_observer_for_tests()
