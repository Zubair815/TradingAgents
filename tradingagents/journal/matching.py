"""Proposal Matching & Broker Execution Reconciliation Engine (Phase 15 & Phase 9).

Solves the critical trading workflow problem: Reconciling broker executions and MT5
positions with agent proposals via exact identifiers and deterministic multi-factor scoring.
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
    ReconciliationStatus,
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
        ambiguity_delta: float = 0.05,
    ) -> None:
        self.journal = journal
        self.lifecycle = lifecycle or TradeLifecycleManager(journal=journal)
        self.match_threshold = match_threshold
        self.ambiguity_delta = ambiguity_delta

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
        pos_order_type = _extract_val(position, "order_type", None)

        if candidate_proposals is None:
            # Query proposals in APPROVED, MODIFIED, or WAITING_USER status
            proposals = self.journal.list_proposals(status=ProposalStatus.APPROVED)
            proposals += self.journal.list_proposals(status=ProposalStatus.MODIFIED)
            proposals += self.journal.list_proposals(status=ProposalStatus.WAITING_USER)
        else:
            proposals = list(candidate_proposals)

        scored_candidates: list[tuple[ProposalRecord, float, MatchConfidence, list[str], float]] = []

        for prop in proposals:
            score, confidence, reasons, disc_pips = self._score_candidate(
                prop=prop,
                norm_sym=norm_sym,
                pos_action=pos_action,
                pos_price=pos_price,
                pos_lots=pos_lots,
                pos_comment=pos_comment,
                pos_time=pos_time,
                pos_order_type=pos_order_type,
            )
            if score > 0.0:
                scored_candidates.append((prop, score, confidence, reasons, disc_pips))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        if not scored_candidates or scored_candidates[0][1] < 0.40:
            top_reasons = scored_candidates[0][3] if scored_candidates else ["No matching candidate proposals found."]
            top_disc = scored_candidates[0][4] if scored_candidates else 0.0
            return MatchResult(
                proposal_id=None,
                broker_ticket=ticket,
                symbol=norm_sym,
                action=pos_action,
                confidence=MatchConfidence.NONE,
                status=ReconciliationStatus.UNMATCHED,
                score=round(scored_candidates[0][1], 2) if scored_candidates else 0.0,
                reasons=top_reasons,
                is_matched=False,
                discrepancy_pips=top_disc,
            )

        top_prop, top_score, top_conf, top_reasons, top_disc = scored_candidates[0]

        # Check ambiguity rule: "If two proposals are similarly likely: do not guess."
        if (
            top_conf != MatchConfidence.EXACT
            and len(scored_candidates) > 1
            and scored_candidates[1][1] >= 0.40
            and (top_score - scored_candidates[1][1]) <= self.ambiguity_delta
        ):
            runner_up = scored_candidates[1]
            return MatchResult(
                proposal_id=top_prop.proposal_id,
                broker_ticket=ticket,
                symbol=norm_sym,
                action=pos_action,
                confidence=top_conf,
                status=ReconciliationStatus.NEEDS_CONFIRMATION,
                score=round(top_score, 2),
                reasons=top_reasons + [
                    f"Ambiguous match: proposal '{top_prop.proposal_id}' (score {top_score:.2f}) and '{runner_up[0].proposal_id}' (score {runner_up[1]:.2f}) are within ambiguity delta {self.ambiguity_delta}. Manual confirmation required."
                ],
                is_matched=False,
                discrepancy_pips=top_disc,
            )

        # Clear winner
        if top_score >= self.match_threshold:
            return MatchResult(
                proposal_id=top_prop.proposal_id,
                broker_ticket=ticket,
                symbol=norm_sym,
                action=pos_action,
                confidence=top_conf,
                status=ReconciliationStatus.MATCHED,
                score=round(top_score, 2),
                reasons=top_reasons,
                is_matched=True,
                discrepancy_pips=top_disc,
            )
        else:
            return MatchResult(
                proposal_id=top_prop.proposal_id,
                broker_ticket=ticket,
                symbol=norm_sym,
                action=pos_action,
                confidence=top_conf,
                status=ReconciliationStatus.NEEDS_CONFIRMATION,
                score=round(top_score, 2),
                reasons=top_reasons + ["Score is below automatic match threshold; requires user confirmation."],
                is_matched=False,
                discrepancy_pips=top_disc,
            )

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
                + self.journal.list_proposals(status=ProposalStatus.WAITING_USER)
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

    def record_manual_unplanned_trade(
        self,
        position: Any,
        actor: str = "MT5Observer",
    ) -> tuple[str, MatchResult]:
        """Journal an unplanned manual broker position that does not match any proposal."""
        ticket = _extract_val(position, "ticket", 0)
        raw_sym = _extract_val(position, "symbol", "")
        norm_sym = normalize_forex_pair(raw_sym)
        raw_type = _extract_val(position, "type", 0)
        pos_action = (
            ForexAction.LONG
            if (raw_type == 0 or raw_type == ForexAction.LONG or str(raw_type) == "BUY")
            else ForexAction.SHORT
        )
        open_price = float(_extract_val(position, "price_open", 0.0))
        lots = float(_extract_val(position, "volume", 0.0))
        sl = float(_extract_val(position, "sl", 0.0))
        tp = float(_extract_val(position, "tp", 0.0))
        pos_time = _extract_val(position, "time")
        open_time_str = None
        if pos_time:
            if isinstance(pos_time, datetime):
                open_time_str = (pos_time if pos_time.tzinfo else pos_time.replace(tzinfo=timezone.utc)).isoformat()
            elif isinstance(pos_time, (int, float)):
                open_time_str = datetime.fromtimestamp(pos_time, tz=timezone.utc).isoformat()
            else:
                open_time_str = str(pos_time)

        trade_id = self.lifecycle.open_unplanned_position(
            pair=norm_sym,
            action=pos_action,
            open_price=open_price,
            lots=lots,
            stop_loss=sl,
            take_profit=tp if tp > 0 else None,
            ticket=ticket,
            open_time_utc=open_time_str,
            actor=actor,
        )

        match_res = MatchResult(
            proposal_id=None,
            broker_ticket=ticket,
            symbol=norm_sym,
            action=pos_action,
            confidence=MatchConfidence.NONE,
            status=ReconciliationStatus.MANUAL_UNPLANNED,
            score=0.0,
            reasons=["Unplanned manual trade journaled without matched proposal."],
            is_matched=False,
        )
        return trade_id, match_res

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
        pos_order_type: Any = None,
    ) -> tuple[float, MatchConfidence, list[str], float]:
        """Calculate match score and diagnostic reasons for a candidate proposal."""
        # 1. Exact comment / magic match
        if prop.proposal_id and prop.proposal_id in pos_comment:
            return 1.0, MatchConfidence.EXACT, ["Exact proposal_id found in order comment."], 0.0

        # Check proposal status
        if prop.status == ProposalStatus.EXPIRED:
            return 0.0, MatchConfidence.NONE, ["Proposal status is EXPIRED."], 0.0

        # Parse timestamps for temporal checks
        pos_dt = None
        if pos_time is not None:
            try:
                if isinstance(pos_time, datetime):
                    pos_dt = pos_time if pos_time.tzinfo else pos_time.replace(tzinfo=timezone.utc)
                elif isinstance(pos_time, (int, float)):
                    pos_dt = datetime.fromtimestamp(pos_time, tz=timezone.utc)
                else:
                    pos_dt = datetime.fromisoformat(str(pos_time).replace("Z", "+00:00"))
            except Exception:
                pos_dt = None

        # Check proposal validity/expiry
        if prop.valid_until and pos_dt is not None:
            try:
                valid_until_dt = datetime.fromisoformat(prop.valid_until.replace("Z", "+00:00"))
                if pos_dt > valid_until_dt:
                    return 0.0, MatchConfidence.NONE, ["Execution timestamp is after proposal validity expiration."], 0.0
            except Exception:
                pass

        # Check if execution occurred significantly prior to proposal creation
        if prop.created_at_utc and pos_dt is not None:
            try:
                created_dt = datetime.fromisoformat(prop.created_at_utc.replace("Z", "+00:00"))
                if (pos_dt - created_dt).total_seconds() < -60:  # more than 1 min before creation
                    return 0.0, MatchConfidence.NONE, ["Execution timestamp preceded proposal creation."], 0.0
            except Exception:
                pass

        # Symbol check (mandatory)
        prop_norm_pair = normalize_forex_pair(prop.pair)
        if prop_norm_pair != norm_sym:
            return 0.0, MatchConfidence.NONE, [f"Symbol mismatch ({prop_norm_pair} vs {norm_sym})."], 0.0

        # Action check (mandatory)
        if prop.action != pos_action:
            return 0.0, MatchConfidence.NONE, [f"Direction mismatch ({prop.action.value} vs {pos_action.value})."], 0.0

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
        if pos_dt and prop.created_at_utc:
            try:
                p_dt = datetime.fromisoformat(prop.created_at_utc.replace("Z", "+00:00"))
                sec_diff = abs((pos_dt - p_dt).total_seconds())
                if sec_diff <= 1800:  # Within 30 minutes
                    score += 0.05
                    reasons.append("Execution within 30 min of proposal creation")
                elif sec_diff <= 7200:  # Within 2 hours
                    score += 0.02
                    reasons.append("Execution within 2 hours of proposal creation")
            except Exception:
                pass

        # 5. Order type alignment check
        if pos_order_type and prop.order_type:
            raw_ot = str(pos_order_type).upper()
            prop_ot = str(prop.order_type.value if hasattr(prop.order_type, "value") else prop.order_type).upper()
            if raw_ot in prop_ot or prop_ot in raw_ot:
                score += 0.02
                reasons.append(f"Order type aligned ({prop_ot})")

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
