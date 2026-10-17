"""Authenticated API for the isolated headless pair observer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

from tradingagents.pair_observer.notifications import NullAlertSink, WindowsToastSink
from tradingagents.pair_observer.settings import ObserverSettings
from web.forex_security import ForexRoute, verify_forex_auth
from web.observer_assembly import get_observer_service

router = APIRouter(
    prefix="/api/observer",
    tags=["Market Observer"],
    dependencies=[Depends(verify_forex_auth)],
    route_class=ForexRoute,
)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair: str | None = None
    profile: str | None = None
    poll_seconds: int | None = None
    max_spread_pips: float | None = None
    max_quote_age_seconds: int | None = None
    recovery_candles: int | None = None
    windows_notifications: bool | None = None
    developing_alerts: bool | None = None
    ready_alerts: bool | None = None


@router.get("/status")
def status() -> dict[str, Any]:
    return get_observer_service().status()


@router.post("/start")
def start() -> dict[str, Any]:
    service = get_observer_service()
    started = service.start()
    current = service.status()
    if not started and current.get("status_code") == "MARKET_CLOSED":
        return JSONResponse(
            status_code=409,
            content={"detail": {
                "code": "MARKET_CLOSED",
                "message": current["status_message"],
            }},
        )
    return current


@router.post("/stop")
def stop() -> dict[str, Any]:
    service = get_observer_service()
    service.stop()
    return service.status()


@router.get("/structure")
def structure() -> dict[str, Any]:
    return {"items": get_observer_service().structures()}


@router.get("/levels")
def levels() -> dict[str, Any]:
    return {"items": get_observer_service().levels()}


@router.get("/alerts")
def alerts(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    items = get_observer_service().repository.list_alerts(limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/setups")
def setups(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    items = get_observer_service().repository.list_setups(limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/setups/{setup_id}")
def setup_detail(setup_id: str) -> dict[str, Any]:
    try:
        return get_observer_service().repository.get_setup(setup_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "DATA_UNAVAILABLE"}) from exc


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: str) -> dict[str, bool]:
    found = get_observer_service().repository.acknowledge(alert_id, datetime.now(UTC).isoformat())
    if not found:
        raise HTTPException(404, detail={"code": "DATA_UNAVAILABLE"})
    return {"acknowledged": True}


@router.get("/snapshots/{snapshot_id}")
def snapshot(snapshot_id: str) -> dict[str, Any]:
    try:
        value = get_observer_service().repository.get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "DATA_UNAVAILABLE"}) from exc
    return value.model_dump(mode="json")


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return get_observer_service().settings_store.load().model_dump(mode="json")


@router.get("/weekly-review/latest")
def latest_weekly_review() -> dict[str, Any]:
    return {"review": get_observer_service().latest_weekly_review()}


@router.post("/weekly-review", response_model=None)
def review_previous_week() -> Any:
    service = get_observer_service()
    try:
        return service.review_previous_week()
    except RuntimeError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "REVIEW_UNAVAILABLE", "message": str(exc)}},
        )
    except (ValueError, KeyError) as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "REVIEW_DATA_INSUFFICIENT",
                    "message": f"Previous-week review could not be completed: {exc}",
                }
            },
        )


@router.patch("/settings")
def patch_settings(request: SettingsPatch) -> dict[str, Any]:
    service = get_observer_service()
    if service.status()["worker_alive"]:
        raise HTTPException(
            409,
            detail={"code": "OBSERVER_RUNNING", "message": "Stop the observer before changing settings."},
        )
    current = service.settings_store.load()
    updates = request.model_dump(exclude_none=True)
    candidate = ObserverSettings.model_validate({**current.model_dump(), **updates})
    service.settings_store.save(candidate)
    service.set_alert_sink(WindowsToastSink() if candidate.windows_notifications else NullAlertSink())
    service.reload_persisted_state(candidate.pair)
    return service.settings_store.load().model_dump(mode="json")


@router.post("/settings/reset")
def reset_settings() -> dict[str, Any]:
    service = get_observer_service()
    if service.status()["worker_alive"]:
        raise HTTPException(
            409,
            detail={"code": "OBSERVER_RUNNING", "message": "Stop the observer before resetting settings."},
        )
    settings = service.settings_store.reset()
    service.set_alert_sink(WindowsToastSink() if settings.windows_notifications else NullAlertSink())
    service.reload_persisted_state(settings.pair)
    return settings.model_dump(mode="json")
