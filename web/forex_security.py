"""Default-deny authentication and public-safe errors for every Forex route."""

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)


def verify_forex_auth(request: Request) -> bool:
    try:
        from web.server import verify_auth
    except ImportError as exc:
        raise HTTPException(503, detail={"code": "AUTH_UNAVAILABLE"}) from exc
    try:
        authorized = verify_auth(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail={"code": "AUTH_UNAVAILABLE"}) from exc
    if authorized is not True:
        raise HTTPException(503, detail={"code": "AUTH_UNAVAILABLE"})
    return True


MESSAGES = {
    "UNAUTHORIZED": "Unauthorized: valid dashboard authentication is required.",
    "AUTH_UNAVAILABLE": "Dashboard authentication is unavailable.",
    "INVALID_REQUEST": "The request is invalid.",
    "DATA_UNAVAILABLE": "Requested data is unavailable.",
    "STALE_DATA": "Requested market data is stale.",
    "HISTORICAL_DATA_UNAVAILABLE": "Historical data is unavailable for the requested period.",
    "MT5_DISCONNECTED": "MetaTrader 5 is not connected.",
    "MT5_CONNECTION_FAILED": "MetaTrader 5 connection failed.",
    "MT5_DATA_UNAVAILABLE": "MetaTrader 5 data is temporarily unavailable.",
    "MT5_SYMBOL_UNAVAILABLE": "The requested MetaTrader 5 symbol is unavailable.",
    "PROPOSAL_NOT_FOUND": "Proposal not found",
    "TRADE_NOT_FOUND": "Trade not found",
    "RUN_NOT_FOUND": "Run not found",
    "RISK_REJECTED": "The proposal was rejected by risk checks.",
    "BACKTEST_FAILED": "The backtest could not be completed.",
    "PROVIDER_ERROR": "The provider request could not be completed.",
    "FOREX_BACKTEST_DEMO_ONLY": "Demo mode must be explicitly enabled.",
    "INVALID_BACKTEST_MODE": "The backtest mode is invalid.",
    "HISTORICAL_ANALYSIS_FAILED": "Historical analysis could not be completed.",
    "WALK_FORWARD_FAILED": "Walk-forward validation could not be completed.",
}


def error_response(request, status, detail=None):
    path = request.url.path
    code = "INVALID_REQUEST" if status < 500 else "PROVIDER_ERROR"
    if status in (401, 403):
        code = "UNAUTHORIZED"
    elif status == 404:
        code = ("TRADE_NOT_FOUND" if "/trades/" in path else
                "PROPOSAL_NOT_FOUND" if "/proposals/" in path else "RUN_NOT_FOUND")
    elif "/mt5/" in path and status >= 500:
        code = "MT5_CONNECTION_FAILED" if path.endswith("/connect") else "MT5_DISCONNECTED"
    elif "/backtest/" in path and status >= 500:
        code = "BACKTEST_FAILED"
    if isinstance(detail, dict) and detail.get("code") in MESSAGES:
        code = detail["code"]
    error = {"code": code, "message": MESSAGES[code], "details": {}}
    # Retain the existing detail field for API consumers during migration.
    legacy = {"code": code, "message": error["message"]} if isinstance(detail, dict) else error["message"]
    return JSONResponse(status_code=status, content={"error": error, "detail": legacy},
                        headers={"Cache-Control": "no-store"})


class ForexRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except HTTPException as exc:
                return error_response(request, exc.status_code, exc.detail)
            except RequestValidationError:
                # Validation errors contain input values, including credentials.
                return error_response(request, 422)
            except Exception as exc:
                logger.warning("Forex request failed (%s)", type(exc).__name__)
                return error_response(request, 500)

        return safe_handler
