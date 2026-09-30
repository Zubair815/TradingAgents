"""Research identities, UTC timestamps and state transitions shared by all callers.

Candle, quote, economic event, proposal, risk, trade and lesson payloads retain
their existing domain models; these envelopes connect them without duplicating
their calculations or introducing competing persistence stores.
"""

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("An explicit UTC offset is required")
    return stamp.astimezone(timezone.utc)


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1


class AnalysisRequest(Record):
    pair: str = "EURUSD"
    timeframe: str = "H1"
    execution_timeframe: str | None = None
    context_timeframes: tuple[str, ...] | None = None
    date: str | None = None
    analysts: list[str] = Field(default_factory=lambda: ["forex_technical", "forex_macro", "forex_news"])
    provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    account_balance: float = Field(default=100000.0, gt=0)
    account_free_margin: float | None = Field(default=None, ge=0)
    risk_percent: float = Field(default=1.0, gt=0, le=5.0)
    higher_timeframes: tuple[str, ...] = ("D1", "H4")
    account_currency: str = "USD"
    session: str | None = None
    requirements: tuple[str, ...] = ()
    research_depth: str = "deep"
    min_rr: float | None = None
    max_spread_pips: float | None = None
    economic_blackout: bool = True
    account_source: str = "mt5"

    @model_validator(mode="before")
    @classmethod
    def sync_timeframes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            from tradingagents.dataflows.config import get_config
            from tradingagents.forex.domain import Timeframe, get_default_context_timeframes

            settings = get_config()
            data.setdefault("pair", settings["forex_default_pair"])
            data.setdefault("risk_percent", settings["forex_default_risk_percent"])
            data.setdefault("min_rr", settings["forex_min_rr"])
            data.setdefault("max_spread_pips", settings["forex_max_spread_pips"])
            explicit_exec_tf = data.get("execution_timeframe") or data.get("timeframe")
            exec_tf = explicit_exec_tf or settings["forex_default_execution_timeframe"]
            norm_exec_tf = Timeframe.from_string(exec_tf).value
            data["execution_timeframe"] = norm_exec_tf
            data["timeframe"] = norm_exec_tf

            ctx_tfs = data.get("context_timeframes")
            if ctx_tfs is None:
                ctx_tfs = data.get("higher_timeframes")
            if ctx_tfs is None:
                configured = settings.get("forex_default_context_timeframes") if not explicit_exec_tf else None
                ctx_tfs = configured or tuple(tf.value for tf in get_default_context_timeframes(norm_exec_tf))
            else:
                ctx_tfs = tuple(Timeframe.from_string(t).value for t in ctx_tfs)
                if len(set(ctx_tfs)) != len(ctx_tfs):
                    raise ValueError("Higher timeframes must be distinct")
            data["context_timeframes"] = ctx_tfs
            data["higher_timeframes"] = ctx_tfs
        return data

    @field_validator("pair")
    @classmethod
    def valid_pair(cls, value):
        from tradingagents.forex.domain import get_forex_pair
        pair = get_forex_pair(value)
        if pair is None:
            raise ValueError("Unknown Forex pair")
        return pair.symbol

    @field_validator("timeframe", "execution_timeframe")
    @classmethod
    def valid_timeframe(cls, value):
        if value is None:
            return None
        from tradingagents.forex.domain import Timeframe
        return Timeframe.from_string(value).value

    @field_validator("higher_timeframes", "context_timeframes")
    @classmethod
    def valid_higher_timeframes(cls, values):
        if values is None:
            return None
        from tradingagents.forex.domain import Timeframe
        normalized = tuple(Timeframe.from_string(v).value for v in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Higher timeframes must be distinct")
        return normalized

    @field_validator("account_currency")
    @classmethod
    def valid_currency(cls, value):
        value = value.upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise ValueError("Account currency must be a three-letter currency code")
        return value

    @field_validator("session")
    @classmethod
    def valid_session(cls, value):
        if value is not None:
            from tradingagents.forex.sessions import TradingSession
            return TradingSession.from_string(value).value
        return value

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            from tradingagents.forex.calendar import _calendar_cutoff
            _calendar_cutoff(value)
        return value

    @field_validator("analysts")
    @classmethod
    def valid_analysts(cls, values):
        if not values or len(set(values)) != len(values) or set(values) - {"forex_technical", "forex_macro", "forex_news"}:
            raise ValueError("Choose distinct supported Forex analysts")
        return values


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUN_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(), RunStatus.FAILED: set(), RunStatus.CANCELLED: set(),
}


class AnalysisRun(Record):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    request: AnalysisRequest
    status: RunStatus = RunStatus.QUEUED
    created_at_utc: datetime = Field(default_factory=utc_now)
    as_of_utc: datetime
    error: str | None = None

    _times = field_validator("created_at_utc", "as_of_utc", mode="before")(require_utc)


class MarketSnapshot(Record):
    snapshot_id: str = Field(default_factory=lambda: new_id("snapshot"))
    run_id: str
    pair: str
    as_of_utc: datetime
    retrieved_at_utc: datetime
    source: str = Field(min_length=1)
    payload_json: str

    _times = field_validator("as_of_utc", "retrieved_at_utc", mode="before")(require_utc)

    @field_validator("payload_json")
    @classmethod
    def valid_payload(cls, value):
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("Snapshot payload must be an object")
        return canonical_json(payload)

    @property
    def payload(self):
        """Return a detached value; mutating it cannot alter the snapshot."""
        return json.loads(self.payload_json)


class AgentReport(Record):
    report_id: str = Field(default_factory=lambda: new_id("report"))
    run_id: str
    snapshot_id: str
    agent: str = Field(min_length=1)
    content: str
    version_id: str
    created_at_utc: datetime = Field(default_factory=utc_now)

    _time = field_validator("created_at_utc", mode="before")(require_utc)


class BrokerEvent(Record):
    event_id: str = Field(default_factory=lambda: new_id("broker_event"))
    broker: str
    account_ref: str
    external_id: str
    event_type: str
    occurred_at_utc: datetime
    observed_at_utc: datetime
    payload_json: str

    _times = field_validator("occurred_at_utc", "observed_at_utc", mode="before")(require_utc)

    @field_validator("payload_json")
    @classmethod
    def valid_payload(cls, value):
        return MarketSnapshot.valid_payload(value)
