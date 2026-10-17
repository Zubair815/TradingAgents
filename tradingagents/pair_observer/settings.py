"""Validated, atomically persisted settings for the isolated observer."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import Field, field_validator

from tradingagents.forex.symbols import DEFAULT_SYMBOL_MAP
from tradingagents.pair_observer.models import ObserverModel

OBSERVER_PAIR_OPTIONS = ("EURUSD", "GBPUSD", "USDJPY")


def observer_home() -> Path:
    configured = os.environ.get("TRADINGAGENTS_PAIR_OBSERVER_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".tradingagents" / "pair_observer"


class ObserverSettings(ObserverModel):
    enabled: bool = False
    pair: str = Field(default="EURUSD", pattern=r"^[A-Z]{6}$")
    profile: str = Field(default="intraday_balanced_v1", pattern=r"^[a-z0-9_]+$")
    poll_seconds: int = Field(default=30, ge=5, le=3600)
    max_spread_pips: float = Field(default=3.0, gt=0, le=50)
    max_quote_age_seconds: int = Field(default=120, ge=5, le=3600)
    recovery_candles: int = Field(default=300, ge=50, le=2_000)
    windows_notifications: bool = True
    developing_alerts: bool = True
    ready_alerts: bool = True

    @field_validator("pair", mode="before")
    @classmethod
    def normalize_pair(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.upper().replace("/", "")
        if DEFAULT_SYMBOL_MAP.get_by_canonical(normalized) is None:
            raise ValueError("pair is not in the supported Forex symbol catalogue")
        if normalized not in OBSERVER_PAIR_OPTIONS:
            raise ValueError("pair must be one of the three observer pairs: EURUSD, GBPUSD, USDJPY")
        return normalized


class ObserverSettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or observer_home() / "settings.json"

    def load(self) -> ObserverSettings:
        if not self.path.exists():
            return ObserverSettings()
        return ObserverSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, settings: ObserverSettings) -> ObserverSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(settings.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return settings

    def reset(self) -> ObserverSettings:
        return self.save(ObserverSettings())
