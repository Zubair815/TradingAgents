"""Maximum Favorable Excursion (MFE) & Maximum Adverse Excursion (MAE) Engine (Phase 16).

Calculates peak price excursions, drawdown penetration, and trade efficiencies
for Long and Short Forex positions using intraday candles or tick data.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.models import TradeJournalRecord
from tradingagents.forex.pips import pip_size_for
from tradingagents.metrics.models import TradeMfeMae

logger = logging.getLogger(__name__)


def _parse_utc_timestamp(ts: Any) -> datetime | None:
    """Safely parse diverse timestamp representations into UTC datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, pd.Timestamp):
        dt = ts.to_pydatetime()
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        try:
            cleaned = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _extract_candle_extremes(
    candle: Any,
) -> tuple[float, float, datetime | None]:
    """Extract (high, low, timestamp) from a candle object, dict, or pandas Series."""
    # Object with attributes (e.g. ForexBar)
    if hasattr(candle, "high") and hasattr(candle, "low"):
        high = float(candle.high)
        low = float(candle.low)
        ts = getattr(candle, "timestamp", None)
        return high, low, _parse_utc_timestamp(ts)

    # Dictionary
    if isinstance(candle, dict):
        high = float(candle.get("high") or candle.get("High") or 0.0)
        low = float(candle.get("low") or candle.get("Low") or 0.0)
        ts = candle.get("timestamp") or candle.get("Date") or candle.get("time")
        return high, low, _parse_utc_timestamp(ts)

    # Pandas Series / tuple
    if isinstance(candle, (tuple, list)) and len(candle) >= 4:
        # Assuming (ts, open, high, low, ...) or (open, high, low, close)
        try:
            return float(candle[2]), float(candle[3]), None
        except Exception:
            pass

    return 0.0, 0.0, None


def calculate_mfe_mae(
    open_price: float,
    stop_loss: float,
    action: ForexAction | str,
    pair: str,
    candles: Sequence[Any] | pd.DataFrame,
    close_price: float | None = None,
    take_profit: float | None = None,
    open_time_utc: str | datetime | None = None,
    close_time_utc: str | datetime | None = None,
    trade_id: str = "trd_unspecified",
) -> TradeMfeMae:
    """Calculate Maximum Favorable and Adverse Excursions for a single trade.

    Parameters
    ----------
    open_price:
        Trade entry price.
    stop_loss:
        Initial planned stop loss price.
    action:
        ForexAction.LONG / BUY or ForexAction.SHORT / SELL.
    pair:
        Forex currency pair symbol (e.g. 'EURUSD', 'USDJPY').
    candles:
        Sequence of ForexBar, candle dicts, or pandas DataFrame.
    close_price:
        Final settlement price if the trade is closed.
    take_profit:
        Initial take-profit target price (optional).
    open_time_utc:
        Timestamp when position was opened.
    close_time_utc:
        Timestamp when position was closed.
    trade_id:
        Trade identifier for tracking.

    Returns
    -------
    TradeMfeMae
        Complete typed excursion record.
    """
    # Normalize action
    if isinstance(action, str):
        act_str = action.strip().upper()
        if "BUY" in act_str or "LONG" in act_str:
            act = ForexAction.LONG
        elif "SELL" in act_str or "SHORT" in act_str:
            act = ForexAction.SHORT
        else:
            act = ForexAction.LONG
    else:
        act = action

    pip_sz = pip_size_for(pair)
    open_dt = _parse_utc_timestamp(open_time_utc)
    close_dt = _parse_utc_timestamp(close_time_utc)

    # Process candle sequence / DataFrame
    raw_candles: list[Any] = []
    if isinstance(candles, pd.DataFrame):
        if not candles.empty:
            for idx, row in candles.iterrows():
                high = float(row.get("High", row.get("high", 0.0)))
                low = float(row.get("Low", row.get("low", 0.0)))
                ts = row.get("Date", row.get("timestamp", idx))
                raw_candles.append({"high": high, "low": low, "timestamp": ts})
    else:
        raw_candles = list(candles)

    # Filter candles by open/close window if timestamps are available
    parsed_candles: list[tuple[float, float, datetime | None]] = []
    for c in raw_candles:
        c_high, c_low, ts = _extract_candle_extremes(c)
        if c_high <= 0.0 and c_low <= 0.0:
            continue
        parsed_candles.append((c_high, c_low, ts))

    filtered_candles: list[tuple[float, float, datetime | None]] = []
    if open_dt is not None:
        for c_high, c_low, ts in parsed_candles:
            if ts is None:
                filtered_candles.append((c_high, c_low, ts))
                continue
            # Keep if after or equal to open_dt
            if ts < open_dt:
                continue
            # If close_dt exists, keep if before or equal to close_dt
            if close_dt is not None and ts > close_dt:
                continue
            filtered_candles.append((c_high, c_low, ts))


    # If timestamp filtering resulted in empty set, fall back to parsed_candles
    active_candles = filtered_candles if filtered_candles else parsed_candles

    # Calculate stop distance in pips
    sl_dist_price = open_price - stop_loss if act == ForexAction.LONG else stop_loss - open_price
    sl_pips = max(0.0001, sl_dist_price / pip_sz)

    # Calculate target distance in pips if provided
    tp_pips: float | None = None
    if take_profit is not None:
        if act == ForexAction.LONG:
            tp_pips = max(0.0, (take_profit - open_price) / pip_sz)
        else:
            tp_pips = max(0.0, (open_price - take_profit) / pip_sz)

    # Calculate realized pips & R if closed
    realized_pips: float = 0.0
    realized_r: float = 0.0
    effective_close = close_price if close_price is not None else open_price
    if close_price is not None:
        if act == ForexAction.LONG:
            realized_pips = (close_price - open_price) / pip_sz
        else:
            realized_pips = (open_price - close_price) / pip_sz
        realized_r = round(realized_pips / sl_pips, 2)

    # If no candles available, fallback to entry/close
    if not active_candles:
        mfe_price = max(open_price, effective_close) if act == ForexAction.LONG else min(open_price, effective_close)
        mae_price = min(open_price, effective_close) if act == ForexAction.LONG else max(open_price, effective_close)
        mfe_pips = max(0.0, realized_pips) if close_price is not None else 0.0
        mae_pips = max(0.0, -realized_pips) if close_price is not None else 0.0
        mfe_r = round(mfe_pips / sl_pips, 2)
        mae_r = round(mae_pips / sl_pips, 2)
        runup_eff = 100.0 if (realized_pips > 0 and mfe_pips > 0) else 0.0
        dd_eff = max(0.0, min(100.0, round(((sl_pips - mae_pips) / sl_pips) * 100.0, 1)))

        return TradeMfeMae(
            trade_id=trade_id,
            pair=pair,
            action=act,
            open_price=open_price,
            close_price=close_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            open_time_utc=open_dt.isoformat() if open_dt else None,
            close_time_utc=close_dt.isoformat() if close_dt else None,
            mfe_price=mfe_price,
            mae_price=mae_price,
            mfe_pips=round(mfe_pips, 1),
            mae_pips=round(mae_pips, 1),
            mfe_r=mfe_r,
            mae_r=mae_r,
            realized_pips=round(realized_pips, 1),
            realized_r=realized_r,
            stop_distance_pips=round(sl_pips, 1),
            target_distance_pips=round(tp_pips, 1) if tp_pips is not None else None,
            runup_efficiency_pct=runup_eff,
            drawdown_efficiency_pct=dd_eff,
            exit_efficiency_pct=100.0,
            candle_count=0,
        )

    # Find extremes across active candles
    mfe_time_dt: datetime | None = None
    mae_time_dt: datetime | None = None

    if act == ForexAction.LONG:
        # Long: MFE is highest high, MAE is lowest low
        mfe_price = -1e9
        mae_price = 1e9

        for c_high, c_low, ts in active_candles:
            if c_high > mfe_price:
                mfe_price = c_high
                mfe_time_dt = ts
            if c_low < mae_price:
                mae_price = c_low
                mae_time_dt = ts

        # Sanity bounds: entry price should be within excursion bounds
        mfe_price = max(mfe_price, open_price, effective_close)
        mae_price = min(mae_price, open_price, effective_close)

        mfe_pips = max(0.0, (mfe_price - open_price) / pip_sz)
        mae_pips = max(0.0, (open_price - mae_price) / pip_sz)

        # Exit efficiency: how close to the top was the exit vs the bottom
        price_range = mfe_price - mae_price
        if close_price is not None and price_range > 0:
            exit_eff = round(((close_price - mae_price) / price_range) * 100.0, 1)
        else:
            exit_eff = 100.0

    else:
        # Short: MFE is lowest low, MAE is highest high
        mfe_price = 1e9
        mae_price = -1e9

        for c_high, c_low, ts in active_candles:
            if c_low < mfe_price:
                mfe_price = c_low
                mfe_time_dt = ts
            if c_high > mae_price:
                mae_price = c_high
                mae_time_dt = ts


        mfe_price = min(mfe_price, open_price, effective_close)
        mae_price = max(mae_price, open_price, effective_close)

        mfe_pips = max(0.0, (open_price - mfe_price) / pip_sz)
        mae_pips = max(0.0, (mae_price - open_price) / pip_sz)

        # Exit efficiency for Short: how close to the lowest bottom was the exit vs top
        price_range = mae_price - mfe_price
        if close_price is not None and price_range > 0:
            exit_eff = round(((mae_price - close_price) / price_range) * 100.0, 1)
        else:
            exit_eff = 100.0

    # R-Multiples
    mfe_r = round(mfe_pips / sl_pips, 2)
    mae_r = round(mae_pips / sl_pips, 2)

    # Runup efficiency: % of peak runup captured upon realization
    if mfe_pips > 0 and realized_pips > 0:
        runup_eff = min(100.0, round((realized_pips / mfe_pips) * 100.0, 1))
    else:
        runup_eff = 0.0

    # Drawdown efficiency: % of planned stop buffer remaining
    dd_eff = max(0.0, min(100.0, round(((sl_pips - mae_pips) / sl_pips) * 100.0, 1)))
    exit_eff = max(0.0, min(100.0, exit_eff))

    return TradeMfeMae(
        trade_id=trade_id,
        pair=pair,
        action=act,
        open_price=open_price,
        close_price=close_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        open_time_utc=open_dt.isoformat() if open_dt else None,
        close_time_utc=close_dt.isoformat() if close_dt else None,
        mfe_price=round(mfe_price, 5),
        mae_price=round(mae_price, 5),
        mfe_pips=round(mfe_pips, 1),
        mae_pips=round(mae_pips, 1),
        mfe_r=mfe_r,
        mae_r=mae_r,
        mfe_time_utc=mfe_time_dt.isoformat() if mfe_time_dt else None,
        mae_time_utc=mae_time_dt.isoformat() if mae_time_dt else None,
        realized_pips=round(realized_pips, 1),
        realized_r=realized_r,
        stop_distance_pips=round(sl_pips, 1),
        target_distance_pips=round(tp_pips, 1) if tp_pips is not None else None,
        runup_efficiency_pct=runup_eff,
        drawdown_efficiency_pct=dd_eff,
        exit_efficiency_pct=exit_eff,
        candle_count=len(active_candles),
    )


def calculate_trade_mfe_mae(
    trade: TradeJournalRecord | dict[str, Any],
    candles: Sequence[Any] | pd.DataFrame,
) -> TradeMfeMae:
    """Convenience adapter calculating MFE/MAE directly from a TradeJournalRecord."""
    if isinstance(trade, TradeJournalRecord):
        return calculate_mfe_mae(
            open_price=trade.open_price,
            stop_loss=trade.stop_loss,
            action=trade.action,
            pair=trade.pair,
            candles=candles,
            close_price=trade.close_price,
            take_profit=trade.take_profit,
            open_time_utc=trade.open_time_utc,
            close_time_utc=trade.close_time_utc,
            trade_id=trade.trade_id,
        )

    # Dictionary representation
    return calculate_mfe_mae(
        open_price=float(trade["open_price"]),
        stop_loss=float(trade["stop_loss"]),
        action=trade["action"],
        pair=str(trade["pair"]),
        candles=candles,
        close_price=float(trade["close_price"]) if trade.get("close_price") is not None else None,
        take_profit=float(trade["take_profit"]) if trade.get("take_profit") is not None else None,
        open_time_utc=trade.get("open_time_utc"),
        close_time_utc=trade.get("close_time_utc"),
        trade_id=str(trade.get("trade_id", "trd_unspecified")),
    )
