"""Automated Closed-Trade Processing Pipeline (Phase 13).

When MetaTrader 5 observer detects a fully closed trade, this pipeline
automatically orchestrates post-close calculations and reflections:
1. Close journal trade (if open)
2. Load MT5 historical intraday bars for holding interval
3. Calculate realized PnL, pips gained/lost, and R-multiple
4. Calculate MFE and MAE excursions
5. Analyze execution quality and slippage against initial proposal
6. Classify diagnostic outcome (e.g. STANDARD_WIN, PREMATURE_EXIT, etc.)
7. Generate post-trade reflection and persist lessons into lesson store

Processing States:
- PENDING: Queued or discovered for processing
- PROCESSING: Currently running calculations or reflection agent
- COMPLETED: All calculations, outcome classification, and reflection complete
- FAILED: Encountered an error during processing (retryable)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeStatus
from tradingagents.learning.history_provider import MT5TradeHistoryProvider, TradeHistoryProvider
from tradingagents.metrics.execution import compare_proposal_against_execution
from tradingagents.metrics.mfe_mae import calculate_trade_mfe_mae, parse_utc_timestamp
from tradingagents.metrics.outcome import TradeOutcomeEngine

if TYPE_CHECKING:
    from tradingagents.learning.manager import ForexLearningManager

logger = logging.getLogger(__name__)


class PostCloseProcessingStatus(str, Enum):
    """Lifecycle processing states for automated closed-trade pipeline."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class PostCloseResult:
    """Summary result of the automated closed-trade pipeline execution."""

    trade_id: str
    status: PostCloseProcessingStatus
    pnl: float | None = None
    pips: float | None = None
    r_multiple: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    outcome_category: str | None = None
    execution_quality_score: float | None = None
    reflection_rating: str | None = None
    lessons_extracted: int = 0
    error_message: str | None = None
    processed_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ClosedTradeProcessor:
    """Orchestrates deterministic post-close analytics, excursions, and automated reflection."""

    def __init__(
        self,
        journal: ForexTradeJournal | None = None,
        history_provider: TradeHistoryProvider | None = None,
        learning_mgr: ForexLearningManager | None = None,
    ) -> None:
        self.journal = journal or ForexTradeJournal()
        self.history_provider = history_provider or MT5TradeHistoryProvider()
        if learning_mgr is None:
            from tradingagents.learning.manager import ForexLearningManager
            learning_mgr = ForexLearningManager(
                journal=self.journal,
                history_provider=self.history_provider,
            )
        self.learning_mgr = learning_mgr
        self.outcome_engine = TradeOutcomeEngine()
        self._status_map: dict[str, PostCloseProcessingStatus] = {}
        self._lock = threading.RLock()

    def get_status(self, trade_id: str) -> PostCloseProcessingStatus:
        """Retrieve current processing status for a trade."""
        with self._lock:
            if trade_id in self._status_map:
                return self._status_map[trade_id]
            trade = self.journal.get_trade(trade_id)
            if trade and trade.metadata:
                st = trade.metadata.get("post_close_status")
                if st:
                    try:
                        # An in-flight marker from another process lifetime is retryable.
                        return PostCloseProcessingStatus.FAILED if st == "PROCESSING" else PostCloseProcessingStatus(st)
                    except ValueError:
                        pass
                if trade.reflection:
                    return PostCloseProcessingStatus.COMPLETED
            return PostCloseProcessingStatus.PENDING

    def process_closed_trade(
        self,
        trade_id: str,
        force: bool = False,
        candles: Sequence[Any] | None = None,
    ) -> PostCloseResult:
        """Execute full automated closed-trade processing pipeline.

        Idempotent: Prevents duplicate reflection unless force=True.
        Transient errors leave state FAILED, which is fully retryable.
        """
        with self._lock:
            # 1. State check to prevent concurrent processing or duplicate runs
            current_status = self.get_status(trade_id)
            if current_status == PostCloseProcessingStatus.PROCESSING:
                logger.info("Trade %s is currently being processed; skipping concurrent request.", trade_id)
                return PostCloseResult(trade_id=trade_id, status=PostCloseProcessingStatus.PROCESSING)

            trade = self.journal.get_trade(trade_id)
            if not trade:
                err = f"Trade {trade_id!r} not found in journal database."
                logger.error(err)
                return PostCloseResult(
                    trade_id=trade_id,
                    status=PostCloseProcessingStatus.FAILED,
                    error_message=err,
                )

            # Prevent duplicate reflection
            if not force and current_status == PostCloseProcessingStatus.COMPLETED and trade.reflection:
                logger.info("Trade %s already completed post-close pipeline; skipping duplicate.", trade_id)
                mfe_r = None
                mae_r = None
                if trade.metadata and "mfe_mae" in trade.metadata:
                    mfe_r = trade.metadata["mfe_mae"].get("mfe_r")
                    mae_r = trade.metadata["mfe_mae"].get("mae_r")
                return PostCloseResult(
                    trade_id=trade_id,
                    status=PostCloseProcessingStatus.COMPLETED,
                    pnl=trade.net_profit,
                    pips=trade.pips_gained,
                    r_multiple=trade.r_multiple,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    outcome_category=trade.metadata.get("outcome_category") if trade.metadata else None,
                )

            # Mark state PROCESSING
            self._status_map[trade_id] = PostCloseProcessingStatus.PROCESSING
            self._update_trade_status(trade_id, PostCloseProcessingStatus.PROCESSING)

        try:
            # Step 1: Ensure trade is closed in journal
            if trade.status != TradeStatus.CLOSED:
                raise ValueError("Post-close processing requires an already closed journal trade")

            # Step 2: Load historical bars for the holding period
            active_candles: Sequence[Any] = []
            history_meta: dict[str, Any] = {}
            if candles is not None:
                active_candles = candles
                history_meta = {"source": "MANUAL", "resolution": "M1", "precision": "BAR_APPROXIMATION", "is_available": True}
            else:
                open_dt = parse_utc_timestamp(trade.open_time_utc) or datetime.now(timezone.utc)
                close_dt = parse_utc_timestamp(trade.close_time_utc) or datetime.now(timezone.utc)
                h_res = self.history_provider.get_history(
                    pair=trade.pair,
                    start_time=open_dt,
                    end_time=close_dt,
                    timeframe="M1",
                )
                active_candles = h_res.candles or []
                history_meta = {
                    "source": h_res.source,
                    "resolution": h_res.resolution,
                    "precision": h_res.precision,
                    "retrieval_time_utc": h_res.retrieval_time_utc,
                    "is_available": h_res.is_available,
                    "unavailable_reason": h_res.unavailable_reason,
                }

            # Step 3: Calculate MFE & MAE excursions
            if not history_meta.get("is_available") or not active_candles:
                raise ValueError("MFE_MAE_UNAVAILABLE: historical M1 coverage is required; retry when available")
            mfe_mae = calculate_trade_mfe_mae(
                trade=trade,
                candles=active_candles,
                source=history_meta.get("source", "MT5"),
                resolution=history_meta.get("resolution", "M1"),
                precision=history_meta.get("precision", "BAR_APPROXIMATION"),
                retrieval_time_utc=history_meta.get("retrieval_time_utc"),
                is_available=history_meta.get("is_available", True),
                unavailable_reason=history_meta.get("unavailable_reason"),
            )
            if not mfe_mae.is_available:
                raise ValueError("MFE_MAE_UNAVAILABLE: no usable bars within the holding interval")

            # Step 4: Calculate execution quality against proposal if linked
            exec_quality_score: float | None = None
            if trade.proposal_id:
                proposal = self.journal.get_proposal(trade.proposal_id)
                if proposal:
                    comparison = compare_proposal_against_execution(
                        proposal=proposal,
                        trade=trade,
                        mfe_mae=mfe_mae,
                    )
                    self._update_trade_metadata(trade.trade_id, {"execution_quality": comparison.model_dump(mode="json")})

            # Step 5: Classify trade outcome
            outcome = self.outcome_engine.classify_outcome(mfe_mae=mfe_mae, trade=trade)

            # Step 6: Trigger reflection agent
            reflection = self.learning_mgr.reflect_on_trade(
                trade=self.journal.get_trade(trade_id) or trade,
                candles=active_candles,
            )

            # Step 7: Finalize state to COMPLETED
            with self._lock:
                self._update_trade_metadata(
                    trade_id,
                    {
                        "post_close_status": PostCloseProcessingStatus.COMPLETED.value,
                        "post_close_error": None,
                        "post_close_completed_at_utc": datetime.now(timezone.utc).isoformat(),
                        "outcome_category": outcome.category.value,
                        "mfe_mae": mfe_mae.model_dump(mode="json"),
                    },
                )
                self._status_map[trade_id] = PostCloseProcessingStatus.COMPLETED

            # Refresh updated trade record
            updated_trade = self.journal.get_trade(trade_id) or trade
            return PostCloseResult(
                trade_id=trade_id,
                status=PostCloseProcessingStatus.COMPLETED,
                pnl=updated_trade.net_profit,
                pips=updated_trade.pips_gained,
                r_multiple=updated_trade.r_multiple,
                mfe_r=mfe_mae.mfe_r,
                mae_r=mfe_mae.mae_r,
                outcome_category=outcome.category.value,
                execution_quality_score=exec_quality_score,
                reflection_rating=reflection.rating.value,
                lessons_extracted=len(reflection.lessons),
            )

        except Exception as exc:
            logger.error("Post-close pipeline failed for trade %s (%s)", trade_id, type(exc).__name__)
            with self._lock:
                self._status_map[trade_id] = PostCloseProcessingStatus.FAILED
                self._update_trade_metadata(
                    trade_id,
                    {
                        "post_close_status": PostCloseProcessingStatus.FAILED.value,
                        "post_close_error": type(exc).__name__,
                    },
                )
            return PostCloseResult(
                trade_id=trade_id,
                status=PostCloseProcessingStatus.FAILED,
                error_message="Post-close processing failed; retry when the required data or provider is available.",
            )

    def _update_trade_status(self, trade_id: str, status: PostCloseProcessingStatus) -> None:
        self._update_trade_metadata(trade_id, {"post_close_status": status.value})

    def _update_trade_metadata(self, trade_id: str, meta: dict[str, Any]) -> None:
        self.journal.update_trade_metadata(trade_id, meta)
