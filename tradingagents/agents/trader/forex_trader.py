"""Institutional Forex Trader Agent (Phase 13).

Translates technical market structure, currency macroeconomic trends,
and economic calendar risks into an institutional Forex trade setup
represented by a typed ``ForexTraderProposal``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
    render_forex_trader_proposal,
)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
)
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
)
from tradingagents.forex.domain import normalize_forex_pair

logger = logging.getLogger(__name__)


def parse_forex_proposal_from_text(
    text: str,
    pair: str,
    default_timeframe: str = "H1",
) -> ForexTraderProposal:
    """Parse or heuristically extract a ForexTraderProposal from unstructured or JSON text.

    Ensures risk safety by defaulting to ``NO_TRADE`` if text cannot be safely
    parsed or contains invalid trade geometry.
    """
    canon_pair = normalize_forex_pair(pair)
    if not text or not text.strip():
        return ForexTraderProposal(
            pair=canon_pair,
            action=ForexAction.NO_TRADE,
            timeframe=default_timeframe,
            reasoning="Empty proposal text returned by model.",
            trade_rationale_summary="NO_TRADE: Empty model response.",
        )

    cleaned = text.strip()

    # 1. Attempt JSON block extraction
    json_candidate = None
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        json_candidate = cleaned[start:end].strip() if end != -1 else cleaned[start:].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        json_candidate = cleaned[start:end].strip() if end != -1 else cleaned[start:].strip()

    if not json_candidate:
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start != -1 and json_end != -1 and json_end > json_start:
            json_candidate = cleaned[json_start : json_end + 1]

    if json_candidate:
        try:
            data = json.loads(json_candidate)
            if isinstance(data, dict):
                data.setdefault("pair", canon_pair)
                data.setdefault("timeframe", default_timeframe)
                p = ForexTraderProposal.model_validate(data)
                if default_timeframe and p.timeframe != default_timeframe:
                    p = p.model_copy(update={"timeframe": default_timeframe})
                return p
        except Exception as exc:
            logger.debug("JSON extraction failed for proposal: %s", exc)

    # 2. Heuristic regex extraction from structured markdown
    action = ForexAction.NO_TRADE
    action_match = re.search(
        r"(?:Action|FINAL FOREX PROPOSAL)[:*\s]+(LONG|SHORT|NO_TRADE|BUY|SELL|HOLD)",
        cleaned,
        re.I,
    )
    if action_match:
        raw_act = action_match.group(1).upper()
        if raw_act in ("LONG", "BUY"):
            action = ForexAction.LONG
        elif raw_act in ("SHORT", "SELL"):
            action = ForexAction.SHORT
        else:
            action = ForexAction.NO_TRADE

    def extract_float(pattern: str) -> float | None:
        m = re.search(pattern, cleaned, re.I)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
        return None

    entry_price = extract_float(r"(?:Entry Price|Entry)[:*\s]+([0-9]+\.?[0-9]*)")
    stop_loss = extract_float(r"(?:Stop Loss|Stop-Loss|SL)[:*\s]+([0-9]+\.?[0-9]*)")
    take_profit_1 = extract_float(r"(?:Take Profit 1|Take Profit|TP1|TP)[:*\s]+([0-9]+\.?[0-9]*)")
    take_profit_2 = extract_float(r"(?:Take Profit 2|TP2)[:*\s]+([0-9]+\.?[0-9]*)")
    risk_pct = extract_float(r"(?:Risk Percent|Risk Allocation|Risk)[:*\s]+([0-9]+\.?[0-9]*)%?")

    # Confluences
    confluences: list[str] = []
    for line in cleaned.splitlines():
        line_clean = line.strip()
        if line_clean.startswith(("- ✅", "* ✅", "- Confluence:", "* Confluence:")):
            factor = line_clean.lstrip("-* ✅:").strip()
            if factor:
                confluences.append(factor)

    # Invalidation condition
    inval_match = re.search(r"(?:Invalidation Condition|Invalidation)[:*\s]+([^\n]+)", cleaned, re.I)
    inval_condition = inval_match.group(1).strip() if inval_match else None

    # Geometry check before directional proposal creation
    if action in (ForexAction.LONG, ForexAction.SHORT):
        if entry_price is None or stop_loss is None:
            return ForexTraderProposal(
                pair=canon_pair,
                action=ForexAction.NO_TRADE,
                reasoning=f"Directional trade requested ({action.value}) but required entry or stop-loss level missing.\n\nRaw text:\n{cleaned}",
                trade_rationale_summary="NO_TRADE: Incomplete price levels.",
            )

        if action == ForexAction.LONG and entry_price <= stop_loss:
            return ForexTraderProposal(
                pair=canon_pair,
                action=ForexAction.NO_TRADE,
                reasoning=f"Inverted trade geometry: LONG requires entry ({entry_price}) > stop_loss ({stop_loss}).\n\nRaw text:\n{cleaned}",
                trade_rationale_summary="NO_TRADE: Inverted LONG geometry.",
            )

        if action == ForexAction.SHORT and entry_price >= stop_loss:
            return ForexTraderProposal(
                pair=canon_pair,
                action=ForexAction.NO_TRADE,
                reasoning=f"Inverted trade geometry: SHORT requires entry ({entry_price}) < stop_loss ({stop_loss}).\n\nRaw text:\n{cleaned}",
                trade_rationale_summary="NO_TRADE: Inverted SHORT geometry.",
            )

    try:
        return ForexTraderProposal(
            pair=canon_pair,
            action=action,
            order_type=OrderType.MARKET,
            setup_type=SetupType.TREND_CONTINUATION,
            timeframe=default_timeframe,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            suggested_risk_percent=risk_pct or 1.0,
            confluence_factors=confluences,
            invalidation_condition=inval_condition,
            reasoning=cleaned,
            trade_rationale_summary=cleaned[:250].replace("\n", " ").strip(),
        )
    except Exception as exc:
        logger.warning("Failed constructing parsed ForexTraderProposal: %s", exc)
        return ForexTraderProposal(
            pair=canon_pair,
            action=ForexAction.NO_TRADE,
            reasoning=f"Fallback to NO_TRADE due to validation failure: {exc}\n\n{cleaned}",
            trade_rationale_summary="NO_TRADE: Validation failure.",
        )


def create_forex_trader(llm: Any):
    """Factory for institutional Forex Trader agent node."""
    structured_llm = bind_structured(llm, ForexTraderProposal, "Forex Trader")

    def forex_trader_node(state: dict[str, Any], name: str = "Forex Trader") -> dict[str, Any]:
        raw_pair = state.get("company_of_interest", "EURUSD")
        pair = normalize_forex_pair(raw_pair)
        instrument_context = get_instrument_context_from_state(state)
        portfolio_context = get_portfolio_context_from_state(state)

        technical_report = (state.get("forex_technical_report") or "").strip()
        macro_report = (state.get("forex_macro_report") or "").strip()
        news_report = (state.get("forex_news_report") or "").strip()
        investment_plan = (state.get("investment_plan") or "").strip()
        past_context = (state.get("past_context") or "").strip()

        # Build grounded institutional prompt
        prompt_sections = [
            f"# Forex Trade Formulation: {pair}",
            f"{instrument_context}",
            "",
            "## Analyst Intelligence Reports",
        ]
        if technical_report:
            prompt_sections.append(f"### Technical Market Structure & Key Levels\n{technical_report}\n")
        if macro_report:
            prompt_sections.append(f"### Currency Macro & Central Bank Stance\n{macro_report}\n")
        if news_report:
            prompt_sections.append(f"### Event Risk & Economic Calendar Windows\n{news_report}\n")
        if investment_plan:
            prompt_sections.append(f"### Research Debate Investment Plan\n{investment_plan}\n")
        if portfolio_context:
            prompt_sections.append(f"### Portfolio Risk Context\n{portfolio_context}\n")
        if past_context:
            prompt_sections.append(f"### Historical Execution Lessons\n{past_context}\n")

        system_prompt = (
            "You are an institutional Forex Trader managing proprietary capital in G8 currency pairs. "
            "Synthesize the technical market structure, currency macroeconomic trends, and economic calendar "
            "into a high-expectancy, executable Forex trade proposal.\n\n"
            "## Non-Negotiable Institutional Rules:\n"
            "1. Ground concrete price levels (entry, stop-loss, take-profit 1 & 2) strictly in the technical market structure "
            "(support/resistance, ATR, swing levels, fair value gaps, session ranges).\n"
            "2. State entry price and stop-loss as absolute numeric quote prices (e.g. 1.08500), NEVER as percentages or ranges.\n"
            "3. For LONG: entry_price > stop_loss and take_profit_1 > entry_price.\n"
            "4. For SHORT: entry_price < stop_loss and take_profit_1 < entry_price.\n"
            "5. Aim for a verified Risk-to-Reward (R:R) ratio of at least 1.5:1 to TP1.\n"
            "6. If market structure is unclear, conflicting, or high-impact news blackout risk is eminent, select NO_TRADE.\n"
            "7. Allocate risk between 0.5% and 2.0% of equity (default: 1.0%).\n"
            f"{NO_EXTERNAL_TOOLS}{get_language_instruction()}"
        )

        user_content = "\n".join(prompt_sections)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        proposal: ForexTraderProposal | None = None

        if structured_llm is not None:
            try:
                raw_res = structured_llm.invoke(messages)
                if isinstance(raw_res, dict) and "parsed" in raw_res:
                    candidate = raw_res.get("parsed")
                else:
                    candidate = raw_res

                if isinstance(candidate, ForexTraderProposal):
                    proposal = candidate
                elif isinstance(candidate, dict):
                    candidate.setdefault("pair", pair)
                    proposal = ForexTraderProposal.model_validate(candidate)
            except Exception as exc:
                logger.warning(
                    "Forex Trader structured invocation failed (%s); falling back to text parsing",
                    exc,
                )

        selected_tf = state.get("forex_execution_timeframe") or state.get("timeframe") or "H1"
        if hasattr(selected_tf, "value"):
            selected_tf = selected_tf.value

        if proposal is None:
            # Fallback to plain LLM invocation and robust text parser
            raw_resp = llm.invoke(messages)
            content = getattr(raw_resp, "content", str(raw_resp))
            proposal = parse_forex_proposal_from_text(content, pair, default_timeframe=selected_tf)

        if proposal is not None and selected_tf and proposal.timeframe != selected_tf:
            proposal = proposal.model_copy(update={"timeframe": selected_tf})

        # Render institutional markdown proposal
        rendered_plan = render_forex_trader_proposal(proposal)

        return {
            "messages": [AIMessage(content=rendered_plan)],
            "trader_investment_plan": rendered_plan,
            "forex_proposal": proposal.model_dump(),
            "sender": name,
        }

    return forex_trader_node
