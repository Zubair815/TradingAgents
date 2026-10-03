"""Contextual Lesson Retrieval Engine for Prompt Augmentation (Phase 16).

Matches upcoming trade proposals and market setups against historical
lessons and pitfalls to enforce continuous institutional learning.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from tradingagents.forex.domain import get_forex_pair
from tradingagents.learning.models import ForexLesson, RetrievedLesson
from tradingagents.learning.store import ForexLessonStore

logger = logging.getLogger(__name__)


def _extract_currencies(pair: str) -> tuple[str, str]:
    """Extract base and quote 3-letter currency codes."""
    fp = get_forex_pair(pair)
    if fp is not None:
        return fp.base_currency, fp.quote_currency
    clean = pair.strip().upper().replace("/", "").replace("=X", "")
    if len(clean) >= 6:
        return clean[:3], clean[3:6]
    return clean, ""


def _are_compatible_timeframes(tf1: str, tf2: str) -> bool:
    """Check if two timeframes have adjacent horizon compatibility."""
    t1 = tf1.upper()
    t2 = tf2.upper()
    intraday = {"M1", "M5", "M15", "M30"}
    swing = {"H1", "H4", "D1"}
    return (t1 in intraday and t2 in intraday) or (t1 in swing and t2 in swing)


def _parse_timestamp(ts: datetime | str | None) -> datetime | None:
    """Parse string or datetime to UTC timezone-aware datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def calculate_age_decay(
    lsn: ForexLesson,
    as_of: datetime | str | None = None,
    half_life_days: float = 30.0,
) -> tuple[float, float]:
    """Calculate elapsed days and exponential age decay factor for a lesson (LEARN-010).

    Returns:
        (elapsed_days, decay_factor) where decay_factor = 2 ** (-elapsed_days / half_life_days).
    """
    ref_dt = _parse_timestamp(as_of) or datetime.now(timezone.utc)

    # Determine relevant timestamp known as-of ref_dt
    created_dt = _parse_timestamp(lsn.created_at)
    val_dt = _parse_timestamp(lsn.last_validated_at)

    # If last validation is in the future relative to ref_dt, cap to created_dt
    effective_dt = val_dt if (val_dt and val_dt <= ref_dt) else created_dt
    if effective_dt is None:
        return 0.0, 1.0

    elapsed_seconds = (ref_dt - effective_dt).total_seconds()
    elapsed_days = 0.0 if elapsed_seconds < 0 else elapsed_seconds / 86400.0

    if half_life_days <= 0:
        return elapsed_days, 1.0

    decay = 2.0 ** (-elapsed_days / half_life_days)
    return elapsed_days, max(0.0, min(1.0, decay))


def _calculate_recency_bonus(
    lsn: ForexLesson,
    as_of: datetime | str | None = None,
    half_life_days: float = 30.0,
) -> float:
    """Calculate recency boost for recently observed/validated lessons using continuous exponential decay (LEARN-010)."""
    _, decay = calculate_age_decay(lsn, as_of=as_of, half_life_days=half_life_days)
    # 0.05 base bonus scaled continuously by exponential decay
    return round(0.05 * decay, 4)


class LessonRetriever:
    """Ranks and retrieves historical trading lessons relevant to current market setups (Phase 16)."""

    def __init__(
        self,
        store: ForexLessonStore,
        default_min_support: int = 1,
        default_half_life_days: float = 30.0,
    ) -> None:
        self.store = store
        self.default_min_support = max(1, default_min_support)
        self.default_half_life_days = max(0.1, default_half_life_days)

    def retrieve_lessons(
        self,
        pair: str,
        timeframe: str | None = None,
        setup_type: str | None = None,
        direction: str | None = None,
        session: str | None = None,
        market_regime: str | None = None,
        volatility_regime: str | None = None,
        news_environment: str | None = None,
        action: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
        min_relevance: float = 0.25,
        active_only: bool = True,
        min_support: int | None = None,
        half_life_days: float | None = None,
        as_of: datetime | str | None = None,
    ) -> list[RetrievedLesson]:
        """Query memory and rank lessons by multidimensional relevance (Phase 16).

        Evaluates 10 contextual dimensions:
        - Symbol match (exact pair vs currency exposure overlap)
        - Execution timeframe (exact vs compatible horizon)
        - Setup classification (exact strategy alignment)
        - Direction alignment (LONG vs SHORT)
        - Trading session (e.g. LONDON, NEW_YORK, TOKYO)
        - Market regime (e.g. TRENDING_BULLISH, RANGING)
        - Volatility regime and news environment tags
        - Empirical validation strength (evidence count with configurable min_support)
        - Continuous exponential age decay (half_life_days)
        - Point-in-time retrieval cutoff (as_of)
        - Statistical confidence multiplier
        """
        all_lessons = self.store.list_lessons(limit=200)
        if not all_lessons:
            return []

        target_pair = pair.strip().upper()
        target_base, target_quote = _extract_currencies(target_pair)
        target_tf = (timeframe or "").strip().upper()
        target_setup = (setup_type or "").strip().upper()
        target_dir = (direction or action or "").strip().upper()
        target_session = (session or "").strip().upper()
        target_regime = (market_regime or "").strip().upper()
        target_vol = (volatility_regime or "").strip().upper()
        target_news = (news_environment or "").strip().upper()
        target_tags = {t.lower() for t in (tags or [])}

        as_of_dt = _parse_timestamp(as_of)
        effective_min_support = (
            max(1, min_support) if min_support is not None else self.default_min_support
        )
        effective_half_life = (
            max(0.1, half_life_days)
            if half_life_days is not None
            else self.default_half_life_days
        )

        scored: list[RetrievedLesson] = []

        for lsn in all_lessons:
            if active_only and not lsn.active:
                continue

            # Point-in-Time Cutoff: Exclude lessons created in the future of as_of
            if as_of_dt is not None:
                created_dt = _parse_timestamp(lsn.created_at)
                if created_dt and created_dt > as_of_dt:
                    continue

            # Configurable Minimum Support Filter (LEARN-006)
            if lsn.evidence_count < effective_min_support:
                continue

            score = 0.0
            reasons: list[str] = []

            # 1. Symbol Match (up to 0.35)
            lsn_pair = lsn.pair.strip().upper()
            lsn_base, lsn_quote = _extract_currencies(lsn_pair)
            if lsn_pair == target_pair:
                score += 0.35
                reasons.append(f"Exact pair match ({target_pair})")
            elif target_base in (lsn_base, lsn_quote) or target_quote in (lsn_base, lsn_quote):
                shared = target_base if target_base in (lsn_base, lsn_quote) else target_quote
                score += 0.15
                reasons.append(f"Currency exposure overlap ({shared})")

            # 2. Execution Timeframe Match (up to 0.15)
            lsn_tf = (lsn.timeframe or "").strip().upper()
            if target_tf and lsn_tf:
                if lsn_tf == target_tf:
                    score += 0.15
                    reasons.append(f"Timeframe match ({target_tf})")
                elif _are_compatible_timeframes(lsn_tf, target_tf):
                    score += 0.05
                    reasons.append(f"Timeframe horizon proximity ({lsn_tf} ~ {target_tf})")

            # 3. Setup Strategy Match (up to 0.25)
            lsn_setup = (lsn.setup or lsn.setup_type or "").strip().upper()
            if target_setup and lsn_setup == target_setup:
                score += 0.25
                reasons.append(f"Setup strategy match ({target_setup})")

            # 4. Direction Alignment (up to 0.08)
            lsn_dir = (lsn.direction or "").strip().upper()
            if target_dir and lsn_dir == target_dir:
                score += 0.08
                reasons.append(f"Direction alignment ({target_dir})")

            # 5. Trading Session Match (up to 0.08)
            lsn_session = (lsn.session or "").strip().upper()
            if target_session and lsn_session == target_session:
                score += 0.08
                reasons.append(f"Trading session match ({target_session})")

            # 6. Market Regime Match (up to 0.06)
            lsn_regime = (lsn.market_regime or "").strip().upper()
            if target_regime and lsn_regime == target_regime:
                score += 0.06
                reasons.append(f"Market regime match ({target_regime})")

            # 7. Volatility Regime / News Environment (up to 0.04)
            lsn_tags_upper = {t.upper() for t in lsn.tags}
            if target_vol and (target_vol in lsn_tags_upper or target_vol == lsn_regime):
                score += 0.04
                reasons.append(f"Volatility regime overlap ({target_vol})")
            if target_news and (target_news in lsn_tags_upper or "HIGH_IMPACT_NEWS" in lsn_tags_upper):
                score += 0.04
                reasons.append(f"News environment overlap ({target_news})")

            # 8. Tag Overlap (up to 0.04)
            lsn_tags = {t.lower() for t in lsn.tags}
            if target_tags and lsn_tags:
                common = target_tags.intersection(lsn_tags)
                if common:
                    overlap_pts = min(0.04, len(common) * 0.02)
                    score += overlap_pts
                    reasons.append(f"Context tags overlap: {sorted(common)}")

            # 9. Recency Bonus with Continuous Exponential Age Decay (LEARN-010)
            elapsed_days, decay = calculate_age_decay(
                lsn, as_of=as_of_dt, half_life_days=effective_half_life
            )
            rec_bonus = round(0.05 * decay, 4)
            if rec_bonus > 0.005:
                score += rec_bonus
                reasons.append(
                    f"Recent validation (+{rec_bonus:.2f}, {elapsed_days:.1f}d ago, decay: {decay:.2f})"
                )

            # 10. Empirical Evidence Strength Bonus (up to 0.04)
            if lsn.evidence_count >= 6:
                score += 0.04
                reasons.append(
                    f"Strong accumulated evidence ({lsn.evidence_count} observations >= {effective_min_support} min)"
                )
            elif lsn.evidence_count >= 3:
                score += 0.02
                reasons.append(
                    f"Moderate evidence ({lsn.evidence_count} observations >= {effective_min_support} min)"
                )
            elif lsn.evidence_count >= effective_min_support:
                reasons.append(
                    f"Meets evidence support threshold ({lsn.evidence_count} >= {effective_min_support})"
                )

            # Scale score by statistical confidence
            conf = max(0.4, min(1.0, float(lsn.confidence)))
            final_score = round(min(1.0, score * conf), 2)

            if final_score >= min_relevance:
                scored.append(
                    RetrievedLesson(
                        lesson=lsn,
                        relevance_score=final_score,
                        match_reasons=reasons,
                    )
                )

        # Sort descending by relevance score
        scored.sort(key=lambda x: x.relevance_score, reverse=True)
        # Constrain to small relevant subset (default 3 to 10)
        max_items = max(1, min(10, limit))
        return scored[:max_items]

    def format_lessons_for_prompt(
        self,
        retrieved: Sequence[RetrievedLesson],
        title: str = "### Historical Heuristics & Pitfalls (Lessons Learned)",
    ) -> str:
        """Format retrieved lessons into an institutional markdown section for LLM prompts."""
        if not retrieved:
            return ""

        applied_ids = [item.lesson.lesson_id for item in retrieved]
        lines: list[str] = [
            title,
            f"*Institutional memory applied ({len(retrieved)} active lessons, IDs: {', '.join(applied_ids)}):*",
            "*ADVISORY EVIDENCE ONLY: Historical heuristics inform hypothesis generation but do not override deterministic risk rules, sizing limits, or broker constraints.*",
            "",
        ]

        for i, item in enumerate(retrieved, start=1):
            lsn = item.lesson
            tf_part = f" {lsn.timeframe}" if lsn.timeframe else ""
            lines.append(
                f"**Lesson {i} [ID: {lsn.lesson_id} | {lsn.pair}{tf_part} | {lsn.setup} | Relevance: {int(item.relevance_score * 100)}%]**:"
            )
            if lsn.rule_violated:
                lines.append(f"- **Rule / Trap:** {lsn.rule_violated}")
            if lsn.observation:
                lines.append(f"- **Observation:** {lsn.observation}")
            lines.append(f"- **Actionable Directive:** {lsn.actionable_rule}")
            lines.append("")

        return "\n".join(lines).strip()
