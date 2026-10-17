from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingagents.pair_observer.interfaces import PairObserverMarketSource
from tradingagents.pair_observer.models import (
    AlertKind,
    Candle,
    Direction,
    NewsStatus,
    ObserverAlert,
    ObserverSnapshotEnvelope,
    SetupState,
    SetupType,
    stable_fingerprint,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pair_observer"


def load_candle() -> tuple[datetime, Candle]:
    payload = json.loads((FIXTURE_DIR / "contract_bullish.json").read_text(encoding="utf-8"))
    return datetime.fromisoformat(payload["as_of_utc"]), Candle.model_validate(payload["candle"])


def test_contract_fixture_is_completed_and_point_in_time_safe() -> None:
    as_of, candle = load_candle()
    assert candle.is_usable_at(as_of)
    assert not candle.is_usable_at(as_of - timedelta(microseconds=1))


def test_candle_is_immutable_and_rejects_inconsistent_ohlc() -> None:
    _, candle = load_candle()
    with pytest.raises(ValidationError):
        candle.close = 1.2  # type: ignore[misc]
    with pytest.raises(ValidationError, match="OHLC"):
        Candle.model_validate({**candle.model_dump(), "high": candle.open - 0.01})


def test_candle_rejects_naive_non_utc_and_wrong_duration() -> None:
    _, candle = load_candle()
    with pytest.raises(ValidationError, match="timezone-aware"):
        Candle.model_validate({**candle.model_dump(), "open_time_utc": datetime(2026, 1, 5, 8)})
    with pytest.raises(ValidationError, match="duration"):
        Candle.model_validate(
            {**candle.model_dump(), "close_time_utc": candle.close_time_utc + timedelta(minutes=1)}
        )


def test_market_source_protocol_exposes_no_execution_surface() -> None:
    assert set(PairObserverMarketSource.__dict__) >= {
        "get_completed_candles",
        "get_quote",
        "has_open_exposure",
    }
    forbidden = {"place_order", "send_order", "modify_order", "close_position"}
    assert forbidden.isdisjoint(PairObserverMarketSource.__dict__)


def test_alert_state_must_match_alert_kind() -> None:
    with pytest.raises(ValidationError, match="alert kind"):
        ObserverAlert(
            alert_id="alert-1",
            setup_id="setup-1",
            pair="EURUSD",
            direction=Direction.LONG,
            setup_type=SetupType.TREND_CONTINUATION,
            kind=AlertKind.TECHNICALLY_READY,
            state=SetupState.DEVELOPING,
            created_at_utc=datetime(2026, 1, 5, 8, 5, tzinfo=UTC),
            fingerprint="a" * 64,
            news_status=NewsStatus.NEWS_UNVERIFIED,
            headline="Developing setup",
            message="Synthetic contract test",
        )


def test_snapshot_hash_is_stable_and_detects_tampering() -> None:
    snapshot = ObserverSnapshotEnvelope(
        snapshot_id="snapshot-1",
        alert_id="alert-1",
        setup_id="setup-1",
        pair="EURUSD",
        direction=Direction.LONG,
        setup_type=SetupType.BREAKOUT_RETEST,
        as_of_utc=datetime(2026, 1, 5, 8, 5, tzinfo=UTC),
        configuration_fingerprint="b" * 64,
        software_revision="ca29b78d9cc8826ff4d2877ca1babba17f4a52ad",
        evidence={"completed_m5": "2026-01-05T08:05:00Z", "news": "NEWS_UNVERIFIED"},
    ).with_integrity_hash()
    assert snapshot.verify_integrity()
    assert snapshot.model_copy(update={"evidence": {"changed": True}}).verify_integrity() is False
    restored = ObserverSnapshotEnvelope.model_validate_json(snapshot.model_dump_json())
    assert restored.verify_integrity()


def test_fingerprint_is_deterministic_and_directionally_distinct() -> None:
    first = stable_fingerprint("EURUSD", Direction.LONG, "2026-01-05T08:05:00Z")
    repeated = stable_fingerprint("EURUSD", Direction.LONG, "2026-01-05T08:05:00Z")
    mirrored = stable_fingerprint("EURUSD", Direction.SHORT, "2026-01-05T08:05:00Z")
    assert first == repeated
    assert first != mirrored
