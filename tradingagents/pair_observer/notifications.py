"""Best-effort notification sinks; persisted alerts remain authoritative."""

from __future__ import annotations

import logging

from tradingagents.pair_observer.models import ObserverAlert

logger = logging.getLogger(__name__)


class NotificationUnavailableError(RuntimeError):
    """Notification transport is unavailable; alert remains persisted."""


class NotificationDisabledError(RuntimeError):
    """Notification delivery was explicitly disabled by observer settings."""


class NullAlertSink:
    def deliver(self, alert: ObserverAlert) -> None:
        logger.info("Observer alert persisted alert_id=%s headline=%s", alert.alert_id, alert.headline)
        raise NotificationDisabledError("Windows notifications are disabled")


class WindowsToastSink:
    def deliver(self, alert: ObserverAlert) -> None:
        try:
            from winotify import Notification  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NotificationUnavailableError(
                "Windows toast support is not installed; alert remains in observer history"
            ) from exc
        Notification(app_id="TradingAgents Market Observer", title=alert.headline, msg=alert.message).show()
