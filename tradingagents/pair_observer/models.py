"""Immutable, versioned contracts for the headless pair observer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
WEEKLY_REVIEW_SCHEMA_VERSION = 1


class ObserverModel(BaseModel):
    """Base for immutable and strictly validated observer contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Timeframe(StrEnum):
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3_600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
}


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class StructureState(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    TRANSITIONING = "TRANSITIONING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class LevelKind(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    DEMAND = "DEMAND"
    SUPPLY = "SUPPLY"


class LevelState(StrEnum):
    DETECTED = "DETECTED"
    ACTIVE = "ACTIVE"
    APPROACHED = "APPROACHED"
    TOUCHED = "TOUCHED"
    REACTED = "REACTED"
    CONSUMED = "CONSUMED"
    BROKEN = "BROKEN"
    RETEST_ELIGIBLE = "RETEST_ELIGIBLE"
    RETESTED = "RETESTED"
    TESTED = "TESTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class SetupType(StrEnum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"


class SetupState(StrEnum):
    CANDIDATE = "CANDIDATE"
    DEVELOPING = "DEVELOPING"
    TECHNICALLY_READY = "TECHNICALLY_READY"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class AlertKind(StrEnum):
    DEVELOPING = "DEVELOPING"
    TECHNICALLY_READY = "TECHNICALLY_READY"


class NewsStatus(StrEnum):
    CLEAR = "CLEAR"
    BLOCKED = "BLOCKED"
    NEWS_UNVERIFIED = "NEWS_UNVERIFIED"


class ReasonCode(StrEnum):
    COMPLETED_CANDLE = "COMPLETED_CANDLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    FUTURE_MARKET_DATA = "FUTURE_MARKET_DATA"
    INCOMPLETE_CANDLE = "INCOMPLETE_CANDLE"
    OUTSIDE_SESSION = "OUTSIDE_SESSION"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    EXPOSURE_PRESENT = "EXPOSURE_PRESENT"
    NEWS_BLOCKED = "NEWS_BLOCKED"
    NEWS_UNVERIFIED = "NEWS_UNVERIFIED"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    LEVEL_INVALIDATED = "LEVEL_INVALIDATED"
    CONFIRMATION_MISSING = "CONFIRMATION_MISSING"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field_name} must be UTC")


class Candle(ObserverModel):
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    timeframe: Timeframe
    open_time_utc: datetime
    close_time_utc: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    tick_volume: int = Field(ge=0)
    completed: bool
    source: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_market_bar(self) -> Candle:
        _require_utc(self.open_time_utc, "open_time_utc")
        _require_utc(self.close_time_utc, "close_time_utc")
        if self.close_time_utc <= self.open_time_utc:
            raise ValueError("close_time_utc must be after open_time_utc")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        expected = TIMEFRAME_SECONDS[self.timeframe]
        if int((self.close_time_utc - self.open_time_utc).total_seconds()) != expected:
            raise ValueError("candle duration does not match timeframe")
        return self

    def is_usable_at(self, as_of_utc: datetime) -> bool:
        """Return true only for a completed candle known at the cutoff."""

        _require_utc(as_of_utc, "as_of_utc")
        return self.completed and self.close_time_utc <= as_of_utc


class Pivot(ObserverModel):
    timeframe: Timeframe
    kind: str = Field(pattern=r"^(HIGH|LOW)$")
    price: float = Field(gt=0)
    open_time_utc: datetime
    confirmed_at_utc: datetime

    @model_validator(mode="after")
    def validate_confirmation(self) -> Pivot:
        _require_utc(self.open_time_utc, "open_time_utc")
        _require_utc(self.confirmed_at_utc, "confirmed_at_utc")
        if self.confirmed_at_utc < self.open_time_utc:
            raise ValueError("pivot cannot be confirmed before it opens")
        return self


class MarketStructure(ObserverModel):
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    timeframe: Timeframe
    state: StructureState
    as_of_utc: datetime
    decisive_pivots: tuple[Pivot, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def validate_cutoff(self) -> MarketStructure:
        _require_utc(self.as_of_utc, "as_of_utc")
        if any(p.confirmed_at_utc > self.as_of_utc for p in self.decisive_pivots):
            raise ValueError("structure contains a pivot unavailable at the cutoff")
        return self


class MarketLevel(ObserverModel):
    level_id: str = Field(min_length=1, max_length=128)
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    timeframe: Timeframe
    kind: LevelKind
    direction: Direction
    proximal_price: float = Field(gt=0)
    distal_price: float = Field(gt=0)
    state: LevelState
    origin_open_utc: datetime
    detected_at_utc: datetime
    touch_count: int = Field(default=0, ge=0)
    strength: float = Field(ge=0, le=1)
    source_type: str = Field(default="SWING", min_length=1, max_length=64)
    last_touch_utc: datetime | None = None
    invalidation_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_level(self) -> MarketLevel:
        _require_utc(self.origin_open_utc, "origin_open_utc")
        _require_utc(self.detected_at_utc, "detected_at_utc")
        if self.detected_at_utc < self.origin_open_utc:
            raise ValueError("level cannot be detected before its origin")
        if self.last_touch_utc is not None:
            _require_utc(self.last_touch_utc, "last_touch_utc")
        return self


class QuoteSnapshot(ObserverModel):
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    broker_symbol: str = Field(min_length=1, max_length=64)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_pips: float = Field(ge=0)
    observed_at_utc: datetime

    @model_validator(mode="after")
    def validate_quote(self) -> QuoteSnapshot:
        _require_utc(self.observed_at_utc, "observed_at_utc")
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        return self


class EligibilityResult(ObserverModel):
    eligible: bool
    news_status: NewsStatus
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def validate_eligibility(self) -> EligibilityResult:
        if self.eligible and any(
            reason in self.reason_codes
            for reason in (
                ReasonCode.STALE_MARKET_DATA,
                ReasonCode.FUTURE_MARKET_DATA,
                ReasonCode.INCOMPLETE_CANDLE,
                ReasonCode.OUTSIDE_SESSION,
                ReasonCode.SPREAD_TOO_WIDE,
                ReasonCode.EXPOSURE_PRESENT,
                ReasonCode.NEWS_BLOCKED,
            )
        ):
            raise ValueError("eligible result contains a blocking reason")
        return self


class ObserverAlert(ObserverModel):
    alert_id: str = Field(min_length=1, max_length=128)
    setup_id: str = Field(min_length=1, max_length=128)
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    direction: Direction
    setup_type: SetupType
    kind: AlertKind
    state: SetupState
    created_at_utc: datetime
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    news_status: NewsStatus
    headline: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_alert(self) -> ObserverAlert:
        _require_utc(self.created_at_utc, "created_at_utc")
        if self.direction is Direction.NEUTRAL:
            raise ValueError("an alert must have a trade direction")
        expected = {
            AlertKind.DEVELOPING: SetupState.DEVELOPING,
            AlertKind.TECHNICALLY_READY: SetupState.TECHNICALLY_READY,
        }[self.kind]
        if self.state is not expected:
            raise ValueError("alert kind and setup state do not match")
        return self


class ObserverSnapshotEnvelope(ObserverModel):
    schema_version: int = Field(default=SNAPSHOT_SCHEMA_VERSION, ge=1)
    snapshot_id: str = Field(min_length=1, max_length=128)
    alert_id: str = Field(min_length=1, max_length=128)
    setup_id: str = Field(min_length=1, max_length=128)
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    direction: Direction
    setup_type: SetupType
    as_of_utc: datetime
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    software_revision: str = Field(min_length=7, max_length=64)
    evidence: dict[str, Any]
    integrity_sha256: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_snapshot(self) -> ObserverSnapshotEnvelope:
        _require_utc(self.as_of_utc, "as_of_utc")
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported snapshot schema version: {self.schema_version}")
        if self.direction is Direction.NEUTRAL:
            raise ValueError("a setup snapshot must have a trade direction")
        return self

    def canonical_payload(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"integrity_sha256"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def calculated_integrity_sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()

    def with_integrity_hash(self) -> ObserverSnapshotEnvelope:
        return self.model_copy(update={"integrity_sha256": self.calculated_integrity_sha256()})

    def verify_integrity(self) -> bool:
        return bool(self.integrity_sha256) and self.integrity_sha256 == self.calculated_integrity_sha256()


class WeeklyReview(ObserverModel):
    schema_version: int = Field(default=WEEKLY_REVIEW_SCHEMA_VERSION, ge=1)
    review_id: str = Field(min_length=1, max_length=128)
    pair: str = Field(pattern=r"^[A-Z]{6}$")
    week_start_utc: datetime
    week_end_utc: datetime
    created_at_utc: datetime
    direction: Direction
    structures: dict[str, dict[str, Any]]
    carried_levels: tuple[MarketLevel, ...]
    source_candle_counts: dict[str, int]
    warnings: tuple[str, ...] = ()
    origin: str = Field(default="WEEKLY_REVIEW", pattern=r"^WEEKLY_REVIEW$")
    integrity_sha256: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_review(self) -> WeeklyReview:
        for name in ("week_start_utc", "week_end_utc", "created_at_utc"):
            _require_utc(getattr(self, name), name)
        if self.week_end_utc <= self.week_start_utc:
            raise ValueError("weekly review end must follow its start")
        if self.schema_version != WEEKLY_REVIEW_SCHEMA_VERSION:
            raise ValueError(f"unsupported weekly review schema version: {self.schema_version}")
        if any(level.pair != self.pair for level in self.carried_levels):
            raise ValueError("weekly review levels must match the review pair")
        return self

    def canonical_payload(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"integrity_sha256"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def calculated_integrity_sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()

    def with_integrity_hash(self) -> WeeklyReview:
        return self.model_copy(update={"integrity_sha256": self.calculated_integrity_sha256()})

    def verify_integrity(self) -> bool:
        return bool(self.integrity_sha256) and self.integrity_sha256 == self.calculated_integrity_sha256()


def stable_fingerprint(*parts: object) -> str:
    """Build a stable SHA-256 identity from canonical JSON-compatible parts."""

    encoded = json.dumps(parts, default=str, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utc_now() -> datetime:
    """Boundary helper for adapters; deterministic engines receive a clock."""

    return datetime.now(UTC)
