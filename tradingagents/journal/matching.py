"""Proposal Matching & Broker Execution Reconciliation Engine (Phase 15).

Solves the critical trading workflow problem: Reconciling broker executions and MT5
positions with agent proposals via exact identifiers and heuristic multi-factor scoring.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord, ProposalStatus
from tradingagents.forex.domain import normalize_forex_pair
from tradingagents.forex.pips import pip_size_for
from tradingagents.journal.lifecycle import TradeLifecycleManager
from tradingagents.journal.models import (
    EventType,
    MatchConfidence,
    MatchResult,
)

logger = logging.getLogger(__name__)


def _extract_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to extract field from either a Pydantic model, MT5 position object, or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class ProposalMatcher:
    """Reconciles broker executions and MT5 positions with agent proposals."""

    def __init__(
        self,
        journal: ForexTradeJournal,
        lifecycle: TradeLifecycleManager | None = None,
        match_threshold: float = 0.70,
    ) -> None:
        self.journal = journal
        self.lifecycle = lifecycle or TradeLifecycleManager(journal=journal)
        self.match_threshold = match_threshold

    def match_position(
        self,
        position: Any,
        candidate_proposals: Sequence[ProposalRecord] | None = None,
    ) -> MatchResult:
        """Score and match an individual broker position against candidate proposals."""
        ticket = _extract_val(position, "ticket", 0)
        raw_sym = _extract_val(position, "symbol", "")
        norm_sym = normalize_forex_pair(raw_sym)
        raw_type = _extract_val(position, "type", 0)
        pos_action = (
            ForexAction.LONG
            if (raw_type == 0 or raw_type == ForexAction.LONG or str(raw_type) == "BUY")
            else ForexAction.SHORT
        )
        pos_price = float(_extract_val(position, "price_open", 0.0))
        pos_lots = float(_extract_val(position, "volume", 0.0))
        pos_comment = str(_extract_val(position, "comment", ""))
        pos_time = _extract_val(position, "time")

        if candidate_proposals is None:
            # Query proposals in APPROVED or MODIFIED status
            proposals = self.journal.list_proposals(status=ProposalStatus.APPROVED)
            proposals += self.journal.list_proposals(status=ProposalStatus.MODIFIED)
        else:
            proposals = list(candidate_proposals)

        best_match: MatchResult = MatchResult(
            broker_ticket=ticket,
            symbol=norm_sym,
            action=pos_action,
            confidence=MatchConfidence.NONE,
            score=0.0,
            is_matched=False,
        )

        for prop in proposals:
            score, confidence, reasons, disc_pips = self._score_candidate(
                prop=prop,
                norm_sym=norm_sym,
                pos_action=pos_action,
                pos_price=pos_price,
                pos_lots=pos_lots,
                pos_comment=pos_comment,
                pos_time=pos_time,
            )

            if score > best_match.score:
                is_matched = score >= self.match_threshold
                best_match = MatchResult(
                    proposal_id=prop.proposal_id,
                    broker_ticket=ticket,
                    symbol=norm_sym,
                    action=pos_action,
                    confidence=confidence,
                    score=round(score, 2),
                    reasons=reasons,
                    is_matched=is_matched,
                    discrepancy_pips=disc_pips,
                )

        return best_match

    def reconcile_positions(
        self,
        positions: Sequence[Any],
        candidate_proposals: Sequence[ProposalRecord] | None = None,
        auto_reconcile: bool = True,
    ) -> list[MatchResult]:
        """Reconcile a collection of broker positions with pending proposals."""
        results: list[MatchResult] = []
        candidates = (
            list(candidate_proposals)
            if candidate_proposals is not None
            else (
                self.journal.list_proposals(status=ProposalStatus.APPROVED)
                + self.journal.list_proposals(status=ProposalStatus.MODIFIED)
            )
        )

        for pos in positions:
            match = self.match_position(pos, candidate_proposals=candidates)
            results.append(match)

            if match.is_matched and match.proposal_id and auto_reconcile:
                # Reconcile: create trade in journal linked to proposal
                open_price = float(_extract_val(pos, "price_open", 0.0))
                lots = float(_extract_val(pos, "volume", 0.0))
                sl = float(_extract_val(pos, "sl", 0.0))
                tp = float(_extract_val(pos, "tp", 0.0))

                try:
                    trade_id = self.lifecycle.open_position_from_proposal(
                        proposal_id=match.proposal_id,
                        open_price=open_price,
                        lots=lots,
                        stop_loss=sl if sl > 0 else None,
                        take_profit=tp if tp > 0 else None,
                        ticket=match.broker_ticket,
                        actor="ProposalMatcher",
                    )
                    self.lifecycle.timeline.record_event(
                        event_type=EventType.RECONCILIATION_MATCH,
                        trade_id=trade_id,
                        proposal_id=match.proposal_id,
                        actor="ProposalMatcher",
                        description=f"Auto-reconciled broker ticket #{match.broker_ticket} with proposal {match.proposal_id} ({match.confidence.value} confidence)",
                        payload={
                            "score": match.score,
                            "confidence": match.confidence.value,
                            "reasons": match.reasons,
                            "discrepancy_pips": match.discrepancy_pips,
                        },
                    )
                    # Remove candidate so it is not double-matched
                    candidates = [c for c in candidates if c.proposal_id != match.proposal_id]
                except Exception as exc:
                    logger.warning("Auto-reconcile failed for proposal %s: %s", match.proposal_id, exc)

        return results

    def find_orphan_positions(
        self,
        positions: Sequence[Any],
        match_results: Sequence[MatchResult],
    ) -> list[Any]:
        """Identify broker positions that could not be matched to any approved proposal."""
        matched_tickets = {
            m.broker_ticket for m in match_results if m.is_matched
        }
        return [
            p for p in positions if _extract_val(p, "ticket", 0) not in matched_tickets
        ]

    def find_unfulfilled_proposals(
        self,
        candidate_proposals: Sequence[ProposalRecord],
        match_results: Sequence[MatchResult],
    ) -> list[ProposalRecord]:
        """Identify approved proposals that have not been filled by any broker execution."""
        matched_prop_ids = {
            m.proposal_id for m in match_results if m.is_matched and m.proposal_id
        }
        return [
            p for p in candidate_proposals if p.proposal_id not in matched_prop_ids
        ]

    def _score_candidate(
        self,
        prop: ProposalRecord,
        norm_sym: str,
        pos_action: ForexAction,
        pos_price: float,
        pos_lots: float,
        pos_comment: str,
        pos_time: Any,
    ) -> tuple[float, MatchConfidence, list[str], float]:
        """Calculate match score and diagnostic reasons for a candidate proposal."""
        # 1. Exact comment / magic match
        if prop.proposal_id in pos_comment:
            return 1.0, MatchConfidence.EXACT, ["Exact proposal_id found in order comment."], 0.0

        # Symbol check (mandatory)
        if prop.pair != norm_sym:
            return 0.0, MatchConfidence.NONE, ["Symbol mismatch."], 0.0

        # Action check (mandatory)
        if prop.action != pos_action:
            return 0.0, MatchConfidence.NONE, ["Direction mismatch."], 0.0

        score = 0.70  # Baseline for matching symbol & direction
        reasons = [f"Symbol matched ({norm_sym})", f"Direction matched ({pos_action.value})"]

        # 2. Price proximity check
        pip_sz = pip_size_for(norm_sym)
        disc_pips = 0.0
        if pos_price > 0 and prop.entry_price:
            disc_pips = round(abs(pos_price - prop.entry_price) / pip_sz, 1)

            # Within entry zone
            if (
                prop.entry_zone_low is not None
                and prop.entry_zone_high is not None
                and prop.entry_zone_low <= pos_price <= prop.entry_zone_high
            ):
                score += 0.15
                reasons.append(f"Price {pos_price} inside entry zone [{prop.entry_zone_low}, {prop.entry_zone_high}]")
            elif disc_pips <= 5.0:
                score += 0.15
                reasons.append(f"Price discrepancy within 5 pips ({disc_pips} pips)")
            elif disc_pips <= 15.0:
                score += 0.05
                reasons.append(f"Price discrepancy within 15 pips ({disc_pips} pips)")

        # 3. Lot size proximity check
        if prop.suggested_lot_size and pos_lots > 0:
            diff_lots = abs(pos_lots - prop.suggested_lot_size)
            ratio = diff_lots / prop.suggested_lot_size
            if ratio <= 0.10:
                score += 0.10
                reasons.append(f"Volume matched ({pos_lots} vs {prop.suggested_lot_size})")
            elif ratio <= 0.25:
                score += 0.05
                reasons.append(f"Volume close ({pos_lots} vs {prop.suggested_lot_size})")

        # 4. Timestamp proximity check
        if pos_time and prop.created_at_utc:
            try:
                p_dt = datetime.fromisoformat(prop.created_at_utc.replace("Z", "+00:00"))
                if isinstance(pos_time, datetime):
                    pos_dt = pos_time if pos_time.tzinfo else pos_time.replace(tzinfo=timezone.utc)
                elif isinstance(pos_time, (int, float)):
                    pos_dt = datetime.fromtimestamp(pos_time, tz=timezone.utc)
                else:
                    pos_dt = datetime.fromisoformat(str(pos_time).replace("Z", "+00:00"))

                sec_diff = abs((pos_dt - p_dt).total_seconds())
                if sec_diff <= 1800:  # Within 30 minutes
                    score += 0.05
                    reasons.append("Execution within 30 min of proposal creation")
            except Exception:
                pass

        score = min(score, 1.0)
        if score >= 0.85:
            conf = MatchConfidence.HIGH
        elif score >= 0.70:
            conf = MatchConfidence.MEDIUM
        elif score >= 0.40:
            conf = MatchConfidence.LOW
        else:
            conf = MatchConfidence.NONE

        return score, conf, reasons, disc_pips
