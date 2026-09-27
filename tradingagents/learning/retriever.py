"""Contextual Lesson Retrieval Engine for Prompt Augmentation (Phase 17).

Matches upcoming trade proposals and market setups against historical
lessons and pitfalls to enforce continuous institutional learning.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from tradingagents.forex.domain import get_forex_pair
from tradingagents.learning.models import RetrievedLesson
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


class LessonRetriever:
    """Ranks and retrieves historical trading lessons relevant to current market setups."""

    def __init__(self, store: ForexLessonStore) -> None:
        self.store = store

    def retrieve_lessons(
        self,
        pair: str,
        setup_type: str | None = None,
        action: str | None = None,
        tags: list[str] | None = None,
        limit: int = 3,
        min_relevance: float = 0.25,
    ) -> list[RetrievedLesson]:
        """Query memory and rank lessons by relevance to current setup context.

        Parameters
        ----------
        pair:
            Currency pair of upcoming proposal (e.g. 'EURUSD').
        setup_type:
            Setup category (e.g. 'TREND_CONTINUATION', 'BREAKOUT').
        action:
            Direction ('LONG' or 'SHORT').
        tags:
            Contextual tags (e.g. ['high_impact_news', 'london_session']).
        limit:
            Maximum number of top lessons to return.
        min_relevance:
            Minimum relevance threshold.

        Returns
        -------
        list[RetrievedLesson]
            Ranked lessons with diagnostic match reasons.
        """
        all_lessons = self.store.list_lessons(limit=200)
        if not all_lessons:
            return []

        target_pair = pair.strip().upper()
        target_base, target_quote = _extract_currencies(target_pair)
        target_setup = str(setup_type).strip().upper() if setup_type else None
        target_tags = {t.lower() for t in (tags or [])}

        scored: list[RetrievedLesson] = []

        for lsn in all_lessons:
            score = 0.0
            reasons: list[str] = []

            lsn_pair = lsn.pair.strip().upper()
            lsn_base, lsn_quote = _extract_currencies(lsn_pair)
            lsn_setup = lsn.setup_type.strip().upper()
            lsn_tags = {t.lower() for t in lsn.tags}

            # 1. Symbol Match (up to 0.40)
            if lsn_pair == target_pair:
                score += 0.40
                reasons.append(f"Exact pair match ({target_pair})")
            elif target_base in (lsn_base, lsn_quote) or target_quote in (lsn_base, lsn_quote):
                shared = target_base if target_base in (lsn_base, lsn_quote) else target_quote
                score += 0.20
                reasons.append(f"Currency exposure overlap ({shared})")

            # 2. Setup Type Match (up to 0.35)
            if target_setup and lsn_setup == target_setup:
                score += 0.35
                reasons.append(f"Setup strategy match ({target_setup})")
            elif lsn_setup:
                score += 0.05

            # 3. Tag Overlap (up to 0.25)
            if target_tags and lsn_tags:
                common = target_tags.intersection(lsn_tags)
                if common:
                    overlap_pts = min(0.25, len(common) * 0.10)
                    score += overlap_pts
                    reasons.append(f"Context tag overlap: {list(common)}")

            # Cap score at 1.0
            final_score = round(min(1.0, score), 2)

            if final_score >= min_relevance:
                scored.append(
                    RetrievedLesson(
                        lesson=lsn,
                        relevance_score=final_score,
                        match_reasons=reasons,
                    )
                )

        # Sort by relevance score descending
        scored.sort(key=lambda x: x.relevance_score, reverse=True)
        return scored[:limit]

    def format_lessons_for_prompt(
        self,
        retrieved: Sequence[RetrievedLesson],
        title: str = "### Historical Heuristics & Pitfalls (Lessons Learned)",
    ) -> str:
        """Format retrieved lessons into an institutional markdown section for LLM prompts."""
        if not retrieved:
            return ""

        lines: list[str] = [
            title,
            "*Institutional memory from settled trades matching this setup / currency pair:*",
            "",
        ]

        for i, item in enumerate(retrieved, start=1):
            lsn = item.lesson
            lines.append(
                f"**Lesson {i} [{lsn.pair} | {lsn.setup_type} | Relevance: {int(item.relevance_score * 100)}%]**:"
            )
            if lsn.rule_violated:
                lines.append(f"- **Rule / Trap:** {lsn.rule_violated}")
            lines.append(f"- **Observation:** {lsn.observation}")
            lines.append(f"- **Actionable Directive:** {lsn.actionable_rule}")
            lines.append("")

        return "\n".join(lines).strip()
