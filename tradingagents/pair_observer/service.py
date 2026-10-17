"""Bounded lifecycle and orchestration for the headless observer."""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from tradingagents.forex.sessions import get_market_status
from tradingagents.pair_observer.eligibility import evaluate_eligibility
from tradingagents.pair_observer.interfaces import AlertSink, PairObserverMarketSource
from tradingagents.pair_observer.levels import (
    advance_level,
    level_from_pivot,
    range_boundary_levels,
)
from tradingagents.pair_observer.models import (
    Direction,
    NewsStatus,
    ObserverSnapshotEnvelope,
    SetupType,
    Timeframe,
    stable_fingerprint,
)
from tradingagents.pair_observer.notifications import (
    NotificationDisabledError,
    NotificationUnavailableError,
)
from tradingagents.pair_observer.repository import SQLiteObserverRepository
from tradingagents.pair_observer.settings import ObserverSettingsStore
from tradingagents.pair_observer.setups import (
    breakout_retest_level,
    build_alert,
    nearby_level,
    stable_setup_id,
    top_down_direction,
)
from tradingagents.pair_observer.structure import classify_structure, confirmed_pivots
from tradingagents.pair_observer.weekly_review import build_weekly_review

logger = logging.getLogger(__name__)

HISTORY = {Timeframe.M5: 120, Timeframe.M15: 200, Timeframe.H1: 300, Timeframe.H4: 300, Timeframe.D1: 260}
PIVOT_STRENGTH = {Timeframe.M5: 2, Timeframe.M15: 2, Timeframe.H1: 2, Timeframe.H4: 3, Timeframe.D1: 3}


class PairObserverService:
    def __init__(
        self,
        source: PairObserverMarketSource,
        repository: SQLiteObserverRepository,
        settings_store: ObserverSettingsStore,
        alert_sink: AlertSink,
    ):
        self.source = source
        self.repository = repository
        self.settings_store = settings_store
        self.alert_sink = alert_sink
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._state = "STOPPED"
        self._last_success_utc: datetime | None = None
        self._last_error: str | None = None
        self._last_failure: str | None = None
        self._status_code: str | None = None
        self._status_message: str | None = None
        self._cycles = 0
        self._structures: dict[str, dict[str, Any]] = {}
        self._levels: list[dict[str, Any]] = []
        self._weekly_context_id: str | None = None
        stored_structures, stored_levels = self.repository.load_state(self.settings_store.load().pair)
        stored_cycles, stored_last_success = self.repository.load_runtime_meta(
            self.settings_store.load().pair
        )
        self._structures = stored_structures
        self._levels = stored_levels
        self._cycles = stored_cycles
        self._last_success_utc = (
            datetime.fromisoformat(stored_last_success) if stored_last_success else None
        )

    def set_alert_sink(self, alert_sink: AlertSink) -> None:
        with self._lock:
            self.alert_sink = alert_sink

    def reload_persisted_state(self, pair: str) -> None:
        structures, levels = self.repository.load_state(pair)
        cycles, last_success = self.repository.load_runtime_meta(pair)
        with self._lock:
            self._structures = structures
            self._levels = levels
            self._cycles = cycles
            self._last_success_utc = datetime.fromisoformat(last_success) if last_success else None

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            market = get_market_status(datetime.now(UTC))
            if not market["is_market_open"]:
                self._mark_market_closed(market)
                return False
            settings = self.settings_store.load().model_copy(update={"enabled": True})
            self.settings_store.save(settings)
            self._stop.clear()
            self._state = "STARTING"
            self._last_error = None
            self._status_code = None
            self._status_message = None
            self._thread = threading.Thread(target=self._run, name="pair-observer", daemon=True)
            self._thread.start()
            return True

    def start_if_enabled(self) -> bool:
        return self.start() if self.settings_store.load().enabled else False

    def stop(self, timeout: float = 5.0, *, disable: bool = True) -> bool:
        with self._lock:
            thread = self._thread
            if disable:
                self.settings_store.save(self.settings_store.load().model_copy(update={"enabled": False}))
            if not thread or not thread.is_alive():
                self._state = "STOPPED"
                return False
            self._state = "STOPPING"
            self._stop.set()
        thread.join(timeout)
        with self._lock:
            self._state = "STOPPED" if not thread.is_alive() else "STOP_TIMEOUT"
            if self._state == "STOPPED":
                self._last_error = None
        return not thread.is_alive()

    def _run(self) -> None:
        self._state = "RUNNING"
        self._retry_pending_notifications()
        while not self._stop.is_set():
            market = get_market_status(datetime.now(UTC))
            if not market["is_market_open"]:
                with self._lock:
                    self._mark_market_closed(market)
                break
            try:
                self.run_cycle()
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._last_failure = self._last_error
                self._state = "DEGRADED"
                logger.warning("Pair observer cycle failed: %s", type(exc).__name__)
            wait_seconds = self.settings_store.load().poll_seconds
            self._stop.wait(wait_seconds)
        self._state = "STOPPED"

    def _mark_market_closed(self, market: dict[str, Any]) -> None:
        next_open = market.get("next_session_open_utc")
        self.settings_store.save(self.settings_store.load().model_copy(update={"enabled": False}))
        self._stop.set()
        self._state = "STOPPED"
        self._last_error = None
        self._status_code = "MARKET_CLOSED"
        self._status_message = (
            f"Market is closed. Next Forex open: {next_open}."
            if next_open
            else "Market is closed. Start watching after Forex reopens."
        )

    def _retry_pending_notifications(self) -> None:
        for alert in self.repository.pending_notifications():
            try:
                self.alert_sink.deliver(alert)
                self.repository.mark_notification(alert.alert_id, "DELIVERED")
            except NotificationDisabledError:
                self.repository.mark_notification(alert.alert_id, "DISABLED")
            except NotificationUnavailableError:
                self.repository.mark_notification(alert.alert_id, "UNAVAILABLE")
            except Exception:
                logger.warning("Observer startup notification retry failed alert_id=%s", alert.alert_id)
                self.repository.mark_notification(alert.alert_id, "FAILED")

    def review_previous_week(self, now_utc: datetime | None = None) -> dict[str, Any]:
        at_utc = now_utc or datetime.now(UTC)
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("stop the observer before reviewing the previous week")
        market = get_market_status(at_utc)
        if market["is_market_open"]:
            raise RuntimeError("weekly review is available only while Forex is closed")
        settings = self.settings_store.load()
        review = build_weekly_review(self.source, settings.pair, now_utc=at_utc)
        stored = self.repository.save_weekly_review(review)
        return stored.model_dump(mode="json")

    def latest_weekly_review(self) -> dict[str, Any] | None:
        pair = self.settings_store.load().pair
        review = self.repository.latest_weekly_review(pair)
        return review.model_dump(mode="json") if review else None

    def run_cycle(self, as_of_utc: datetime | None = None) -> dict[str, Any]:
        cutoff = as_of_utc or datetime.now(UTC)
        settings = self.settings_store.load()
        windows = {
            timeframe: tuple(
                self.source.get_completed_candles(
                    settings.pair,
                    timeframe,
                    count=min(HISTORY[timeframe], settings.recovery_candles),
                    as_of_utc=cutoff,
                )
            )
            for timeframe in Timeframe
        }
        if any(not values for values in windows.values()):
            raise ValueError("required completed-candle window is unavailable")
        structures = {}
        pivots = {}
        for timeframe, candles in windows.items():
            strength = PIVOT_STRENGTH[timeframe]
            tf_pivots = confirmed_pivots(candles, left_strength=strength, right_strength=strength)
            pivots[timeframe] = tf_pivots
            structures[timeframe.value] = classify_structure(candles, tf_pivots)
        h1 = windows[Timeframe.H1]
        atr = _atr(h1, 14)
        tick_size = self.source.get_tick_size(settings.pair)
        level_map: dict[str, Any] = {}
        weekly_review = self.repository.latest_weekly_review(settings.pair, before_utc=cutoff)
        if weekly_review is not None:
            for reviewed_level in weekly_review.carried_levels:
                level_map[reviewed_level.level_id] = reviewed_level
        weekly_context_id = weekly_review.review_id if weekly_review is not None else None
        for existing in self.repository.load_levels(settings.pair):
            current_candles = windows.get(existing.timeframe)
            if current_candles and current_candles[-1].close_time_utc >= existing.detected_at_utc:
                existing = advance_level(existing, current_candles[-1])
            level_map[existing.level_id] = existing
        for timeframe in (Timeframe.D1, Timeframe.H4, Timeframe.H1):
            timeframe_atr = _atr(windows[timeframe], 14)
            for pivot in pivots[timeframe][-12:]:
                level = level_from_pivot(
                    settings.pair,
                    pivot,
                    atr=timeframe_atr,
                    tick_size=tick_size,
                    source_type=f"{timeframe.value}_SWING",
                )
                level_map.setdefault(level.level_id, level)
        boundaries = [
            (windows[Timeframe.D1][-2:-1], "PREVIOUS_DAY", 0.8),
            (windows[Timeframe.D1][-6:-1], "PREVIOUS_WEEK", 0.85),
        ]
        current_day = cutoff.date()
        m15_today = tuple(item for item in windows[Timeframe.M15] if item.open_time_utc.date() == current_day)
        asian = tuple(item for item in m15_today if 0 <= item.open_time_utc.hour < 7)
        london = tuple(item for item in m15_today if 7 <= item.open_time_utc.hour < 17)
        new_york = tuple(item for item in m15_today if 12 <= item.open_time_utc.hour < 22)
        boundaries.extend(((asian, "ASIAN_SESSION", 0.65), (london, "LONDON_SESSION", 0.7), (new_york, "NEW_YORK_SESSION", 0.7)))
        for source_candles, source_type, strength in boundaries:
            if source_candles:
                for level in range_boundary_levels(
                    settings.pair,
                    source_candles,
                    source_type=source_type,
                    atr=atr,
                    tick_size=tick_size,
                    detected_at_utc=cutoff,
                    strength=strength,
                ):
                    level_map.setdefault(level.level_id, level)
        levels = tuple(
            sorted(level_map.values(), key=lambda item: item.detected_at_utc, reverse=True)[:500]
        )
        quote = self.source.get_quote(
            settings.pair,
            as_of_utc=cutoff,
            max_age_seconds=settings.max_quote_age_seconds,
        )
        eligibility = evaluate_eligibility(
            at_utc=cutoff,
            quote_age_seconds=(cutoff - quote.observed_at_utc).total_seconds(),
            max_quote_age_seconds=settings.max_quote_age_seconds,
            spread_pips=quote.spread_pips,
            max_spread_pips=settings.max_spread_pips,
            has_exposure=self.source.has_open_exposure(settings.pair, as_of_utc=cutoff),
            news_status=NewsStatus.NEWS_UNVERIFIED,
        )
        direction = top_down_direction(structures)
        price = (quote.bid + quote.ask) / 2
        level = breakout_retest_level(levels, direction, windows[Timeframe.M15])
        setup_type = SetupType.BREAKOUT_RETEST if level else SetupType.TREND_CONTINUATION
        level = level or nearby_level(levels, direction, price, atr)
        alert = None
        if level is not None:
            if direction is not Direction.NEUTRAL:
                candidate_id = stable_setup_id(settings.pair, direction, setup_type, level.level_id)
                self.repository.record_setup_state(
                    {
                        "setup_id": candidate_id,
                        "pair": settings.pair,
                        "direction": direction.value,
                        "setup_type": setup_type.value,
                        "state": "CANDIDATE",
                        "level_id": level.level_id,
                        "updated_at_utc": cutoff.isoformat(),
                    }
                )
            alert = build_alert(
                pair=settings.pair,
                direction=direction,
                setup_type=setup_type,
                level=level,
                m5_candles=windows[Timeframe.M5][-2:],
                eligibility=eligibility,
                created_at_utc=cutoff,
                developing_allowed=settings.developing_alerts,
                ready_allowed=settings.ready_alerts,
            )
        serialized_structures = {key: value.model_dump(mode="json") for key, value in structures.items()}
        self.repository.save_cycle_state(
            pair=settings.pair,
            as_of_utc=cutoff.isoformat(),
            structures=serialized_structures,
            levels=levels,
            watermarks={key.value: values[-1].close_time_utc.isoformat() for key, values in windows.items()},
        )
        persisted = False
        if alert is not None:
            config_hash = stable_fingerprint(settings.model_dump(mode="json"))
            snapshot = ObserverSnapshotEnvelope(
                snapshot_id=f"snapshot-{alert.alert_id}",
                alert_id=alert.alert_id,
                setup_id=alert.setup_id,
                pair=alert.pair,
                direction=alert.direction,
                setup_type=alert.setup_type,
                as_of_utc=cutoff,
                configuration_fingerprint=config_hash,
                software_revision=os.environ.get("TRADINGAGENTS_SOFTWARE_REVISION", "unknown"),
                evidence={
                    "structures": {key: value.model_dump(mode="json") for key, value in structures.items()},
                    "level": level.model_dump(mode="json"),
                    "quote": quote.model_dump(mode="json"),
                    "eligibility": eligibility.model_dump(mode="json"),
                    "completed_candles": {
                        key.value: values[-1].model_dump(mode="json") for key, values in windows.items()
                    },
                },
            ).with_integrity_hash()
            persisted = self.repository.save_alert_with_snapshot(alert, snapshot)
            if persisted:
                try:
                    self.alert_sink.deliver(alert)
                    self.repository.mark_notification(alert.alert_id, "DELIVERED")
                except NotificationDisabledError:
                    self.repository.mark_notification(alert.alert_id, "DISABLED")
                except NotificationUnavailableError:
                    self.repository.mark_notification(alert.alert_id, "UNAVAILABLE")
                except Exception:
                    logger.warning("Observer notification failed alert_id=%s", alert.alert_id)
                    self.repository.mark_notification(alert.alert_id, "FAILED")
        with self._lock:
            self._structures = serialized_structures
            self._levels = [item.model_dump(mode="json") for item in levels]
            self._last_success_utc = cutoff
            self._last_error = None
            self._cycles += 1
            self._state = "RUNNING"
            self._weekly_context_id = weekly_context_id
        return {"alert_created": persisted, "direction": direction.value, "eligible": eligibility.eligible}

    def status(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        market = get_market_status(datetime.now(UTC))
        with self._lock:
            return {
                "state": self._state,
                "enabled": settings.enabled,
                "pair": settings.pair,
                "cycles": self._cycles,
                "last_success_utc": self._last_success_utc.isoformat() if self._last_success_utc else None,
                "last_error": self._last_error,
                "last_failure": self._last_failure,
                "status_code": self._status_code,
                "status_message": self._status_message,
                "weekly_context_loaded": self._weekly_context_id is not None,
                "weekly_review_id": self._weekly_context_id,
                "market_open": market["is_market_open"],
                "next_market_open_utc": market.get("next_session_open_utc"),
                "worker_alive": bool(self._thread and self._thread.is_alive()),
            }

    def structures(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._structures)

    def levels(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._levels)


def _atr(candles: tuple, period: int) -> float:
    if len(candles) < period + 1:
        raise ValueError("ATR history is unavailable")
    ranges = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:], strict=True):
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    value = sum(ranges) / len(ranges)
    if value <= 0:
        raise ValueError("ATR is not positive")
    return value
