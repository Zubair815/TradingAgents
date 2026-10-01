"""Deterministic, directional FX conversion observations and resolution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FXConversionUnavailable(ValueError):
    """Raised when a risk-critical currency conversion cannot be resolved."""


class ForexConversionRate(BaseModel):
    """Rate direction is explicit: one ``from_currency`` equals ``rate`` ``to_currency``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    status: AvailabilityStatus
    rate: float | None = Field(default=None, gt=0.0)
    conversion_path: tuple[str, ...] = ()
    source: str | None = None
    observed_at: datetime | None = None

    @field_validator("from_currency", "to_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("observed_at", mode="before")
    @classmethod
    def observed_at_is_utc(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError("an explicit UTC offset is required")
        return stamp.astimezone(timezone.utc)

    @model_validator(mode="after")
    def status_matches_payload(self):
        if self.status == AvailabilityStatus.AVAILABLE:
            if self.rate is None or self.observed_at is None or not self.source:
                raise ValueError("available conversion requires rate, source, and observed_at")
        elif self.rate is not None:
            raise ValueError("unavailable or not-applicable conversion cannot carry a rate")
        return self


def resolve_conversion_rate(
    from_currency: str,
    to_currency: str,
    conversions: Sequence[ForexConversionRate] = (),
    *,
    as_of_utc: datetime | None = None,
    max_age: timedelta | None = None,
) -> float:
    """Resolve a direct or inverse observation without inventing a rate."""
    source = from_currency.upper()
    target = to_currency.upper()
    if source == target:
        return 1.0
    if as_of_utc is not None:
        if as_of_utc.tzinfo is None:
            raise ValueError("as_of_utc must be timezone-aware")
        as_of_utc = as_of_utc.astimezone(timezone.utc)

    candidates: list[tuple[datetime, float]] = []
    for item in conversions:
        if item.status != AvailabilityStatus.AVAILABLE or item.rate is None or item.observed_at is None:
            continue
        if as_of_utc is not None:
            if item.observed_at > as_of_utc:
                continue
            if max_age is not None and as_of_utc - item.observed_at > max_age:
                continue
        if item.from_currency == source and item.to_currency == target:
            candidates.append((item.observed_at, item.rate))
        elif item.from_currency == target and item.to_currency == source:
            candidates.append((item.observed_at, 1.0 / item.rate))
    if not candidates:
        raise FXConversionUnavailable(f"No current conversion from {source} to {target}")
    return max(candidates, key=lambda candidate: candidate[0])[1]
