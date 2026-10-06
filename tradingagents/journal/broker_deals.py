"""Atomic, idempotent projection of observed broker fills into the local journal."""

import json

from tradingagents.forex.domain import get_forex_pair


def record_broker_deal(journal, trade_id, deal):
    """Persist the deal, its costs, remaining volume and lifecycle event together.

    Broker commission/fee are signed cash flows. Journal commission is a cost.
    No terminal methods are invoked here.
    """
    if deal.entry not in ("IN", "OUT", "OUT_BY"):
        raise ValueError("Unsupported broker reversal requires manual reconciliation")
    key = f"mt5:{trade_id}:{deal.ticket}"
    with journal._lock:
        trade = journal.get_trade(trade_id)
        conn = journal._get_connection()
        try:
            with conn:
                if conn.execute("SELECT 1 FROM executions WHERE deal_id=?", (key,)).fetchone():
                    return None
                meta = dict(trade.metadata)
                totals = dict(meta.get("broker_totals", {}))
                for name in ("profit", "commission", "swap", "fee"):
                    totals[name] = totals.get(name, 0.0) + float(getattr(deal, name, 0.0))
                remaining = trade.lots
                closing = deal.entry in ("OUT", "OUT_BY")
                if closing:
                    if deal.volume > remaining + 1e-6:
                        raise ValueError("Exit deal exceeds remaining journal volume")
                    remaining = round(max(0, remaining-deal.volume), 8)
                final = closing and remaining <= 1e-6
                event_type = "POSITION_CLOSED" if final else "PARTIAL_CLOSE" if closing else "DEAL_FILLED"
                meta["broker_totals"] = totals
                meta.setdefault("broker_deal_ids", []).append(deal.ticket)
                if final:
                    meta["post_close_status"] = "PENDING"
                pair = get_forex_pair(trade.pair)
                direction = 1 if trade.action.value == "LONG" else -1
                deal_pips = direction * (deal.price-trade.open_price) / pair.pip_size if pair else None
                initial_lots = float(meta.get("initial_lots", trade.lots))
                if closing and deal_pips is not None:
                    meta["realized_pip_lots"] = float(meta.get("realized_pip_lots", 0.0)) + deal_pips * deal.volume
                pips = (
                    round(float(meta.get("realized_pip_lots", 0.0)) / initial_lots, 2)
                    if final and pair and initial_lots > 0 else None
                )
                risk = abs(trade.open_price-meta.get("initial_stop_loss", trade.stop_loss)) / pair.pip_size if pair else None
                reason = "STOP_LOSS" if deal.reason == 4 else "TAKE_PROFIT" if deal.reason == 5 else "MANUAL"
                conn.execute("""INSERT INTO executions
                    (deal_id,trade_id,proposal_id,pair,order_type,volume,price,slippage_pips,spread_at_open_pips,timestamp_utc)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (key, trade_id, trade.proposal_id, trade.pair, deal.entry, deal.volume, deal.price, 0, 0, deal.time.isoformat()))
                conn.execute("""UPDATE trades SET lots=?,status=?,close_time_utc=?,close_price=?,
                    gross_profit=?,commission=?,swap=?,net_profit=?,pips_gained=?,r_multiple=?,exit_reason=?,metadata_json=?
                    WHERE trade_id=?""",
                    (remaining if not final else trade.lots, "CLOSED" if final else trade.status.value,
                     deal.time.isoformat() if final else trade.close_time_utc,
                     deal.price if final else trade.close_price, totals["profit"],
                     -(totals["commission"]+totals["fee"]), totals["swap"], sum(totals.values()),
                     pips if final else trade.pips_gained,
                     round(pips/risk, 2) if final and pips is not None and risk else trade.r_multiple,
                     reason if final else None, json.dumps(meta), trade_id))
                payload = deal.model_dump(mode="json")
                payload.update(deal_ticket=deal.ticket, remaining_volume=remaining, volume_closed=deal.volume if closing else 0)
                conn.execute("""INSERT INTO trade_events
                    (event_id,trade_id,proposal_id,broker_position_id,broker_order_id,broker_deal_id,
                     event_type,timestamp_utc,source,actor,description,payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, trade_id, trade.proposal_id, str(deal.position_id), str(deal.order), str(deal.ticket),
                     event_type, deal.time.isoformat(), "MT5", "MT5Observer", "Observed broker execution", json.dumps(payload)))
                return event_type
        finally:
            if conn is not journal._mem_conn:
                conn.close()
