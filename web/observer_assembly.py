"""Contained composition root for the isolated pair observer."""

from __future__ import annotations

import threading

from tradingagents.pair_observer.mt5_adapter import MT5PairObserverSource
from tradingagents.pair_observer.notifications import NullAlertSink, WindowsToastSink
from tradingagents.pair_observer.repository import SQLiteObserverRepository
from tradingagents.pair_observer.service import PairObserverService
from tradingagents.pair_observer.settings import ObserverSettingsStore

_lock = threading.Lock()
_service: PairObserverService | None = None


def get_observer_service() -> PairObserverService:
    global _service
    with _lock:
        if _service is None:
            from web.forex_routes import get_mt5_observer

            store = ObserverSettingsStore()
            settings = store.load()
            sink = WindowsToastSink() if settings.windows_notifications else NullAlertSink()
            _service = PairObserverService(
                source=MT5PairObserverSource(get_mt5_observer()),
                repository=SQLiteObserverRepository(),
                settings_store=store,
                alert_sink=sink,
            )
        return _service


def start_observer_if_enabled() -> bool:
    return get_observer_service().start_if_enabled()


def shutdown_observer() -> None:
    global _service
    with _lock:
        service = _service
    if service is not None:
        service.stop(disable=False)


def reset_observer_for_tests() -> None:
    global _service
    with _lock:
        service, _service = _service, None
    if service is not None:
        service.stop(disable=False)
