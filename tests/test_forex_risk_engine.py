"""Unit and integration test suite for Deterministic Forex Risk Engine (Phase 10).

Covers:
1. ForexRiskLimits defaults and custom overrides.
2. NO_TRADE proposal immediate approval with capital preservation.
3. Valid LONG / SHORT proposal approval with deterministic lot sizing.
4. Trade geometry enforcement (LONG SL < entry, TP > entry; SHORT SL > entry, TP < entry).
5. Pip stop-loss distance bounds (min_sl_pips, max_sl_pips).
6. Risk-to-Reward (R:R) validation (sub-1.0 reject; 1.0-1.5 modify; >= 1.5 approve).
7. Economic calendar news blackout enforcement (Tier-1 event < 2h reject; < 24h modify).
8. Market open and weekend closure enforcement.
9. Spread and spread-to-ATR friction checks.
10. Equity risk clamping and position sizing.
11. quick_check() and clamp_proposal_to_limits() methods.
12. Module-level convenience helper validate_forex_proposal().
"""

from __future__ import annotations

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
    from_trader_proposal,
    render_forex_risk_decision,
)
from tradingagents.forex import (
    ForexRiskEngine,
    ForexRiskLimits,
    validate_forex_proposal,
)

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def make_valid_long_proposal(
    pair: str = "EURUSD",
    entry: float = 1.08500,
    sl: float = 1.08200,    # 30 pips
    tp1: float = 1.09000,   # 50 pips (R:R = 1.67)
    risk_pct: float = 1.0,
) -> ForexTraderProposal:
    return ForexTraderProposal(
        pair=pair,
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.PULLBACK,
        timeframe="H1",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        suggested_risk_percent=risk_pct,
        reasoning="H1 bullish pullback to 50 EMA with strong institutional order block confluence.",
    )


def make_valid_short_proposal(
    pair: str = "GBPUSD",
    entry: float = 1.27000,
    sl: float = 1.27300,    # 30 pips
    tp1: float = 1.26400,   # 60 pips (R:R = 2.0)
    risk_pct: float = 1.0,
) -> ForexTraderProposal:
    return ForexTraderProposal(
        pair=pair,
        action=ForexAction.SHORT,
        order_type=OrderType.MARKET,
        setup_type=SetupType.BREAKOUT,
        timeframe="H4",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        suggested_risk_percent=risk_pct,
        reasoning="H4 bearish breakdown below key swing support with dovish macro catalyst.",
    )


# ---------------------------------------------------------------------------
# 1. ForexRiskLimits Configuration Tests
# ---------------------------------------------------------------------------


class TestForexRiskLimits:
    def test_default_values(self):
        limits = ForexRiskLimits()
        assert limits.min_risk_reward_ratio == 1.5
        assert limits.hard_min_risk_reward_ratio == 1.0
        assert limits.max_risk_percent == 2.0
        assert limits.default_risk_percent == 1.0
        assert limits.min_sl_pips == 8.0
        assert limits.max_sl_pips == 100.0
        assert limits.enforce_news_blackout is True
        assert limits.blackout_lookahead_hours == 2.0
        assert limits.news_warning_lookahead_hours == 8.0
        assert limits.enforce_market_open is True
        assert limits.max_spread_pips == 3.5
        assert limits.max_spread_atr_ratio == 0.15
        assert limits.require_take_profit is True
        assert limits.allow_modification is True

    def test_custom_overrides(self):
        limits = ForexRiskLimits(
            min_risk_reward_ratio=2.0,
            max_risk_percent=1.5,
            min_sl_pips=12.0,
            max_sl_pips=80.0,
            enforce_news_blackout=False,
        )
        assert limits.min_risk_reward_ratio == 2.0
        assert limits.max_risk_percent == 1.5
        assert limits.min_sl_pips == 12.0
        assert limits.max_sl_pips == 80.0
        assert limits.enforce_news_blackout is False


# ---------------------------------------------------------------------------
# 2. NO_TRADE Proposal Approval
# ---------------------------------------------------------------------------


class TestNoTradeApproval:
    def test_no_trade_proposal_approved_safely(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            reasoning="High uncertainty ahead of FOMC rate decision. Capital preservation mandated.",
        )
        decision = engine.validate_proposal(proposal)

        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.original_action == ForexAction.NO_TRADE
        assert decision.approved_action == ForexAction.NO_TRADE
        assert decision.max_risk_percent == 0.0
        assert decision.approved_lot_size == 0.0
        assert len(decision.risk_violations) == 0
        assert "Capital preserved" in decision.risk_checks_passed[0]


# ---------------------------------------------------------------------------
# 3. Valid Long & Short Proposals Approval
# ---------------------------------------------------------------------------


class TestValidProposalApproval:
    def test_valid_long_proposal_approved(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Wednesday 14:00 UTC (market open, no news)
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            current_spread_pips=1.2,
            atr_pips=65.0,
            account_balance=10000.0,
            account_currency="USD",
        )

        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.LONG
        assert decision.entry_price == 1.08500
        assert decision.stop_loss == 1.08200
        assert decision.take_profit == 1.09000
        assert decision.risk_reward_ratio == 1.67
        assert decision.max_risk_percent == 1.0
        assert decision.approved_lot_size is None
        assert len(decision.risk_violations) == 0
        assert len(decision.risk_checks_passed) >= 5

    def test_valid_short_proposal_approved(self):
        engine = ForexRiskEngine()
        proposal = make_valid_short_proposal()

        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            current_spread_pips=1.5,
            atr_pips=75.0,
            account_balance=25000.0,
        )

        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.SHORT
        assert decision.entry_price == 1.27000
        assert decision.stop_loss == 1.27300
        assert decision.take_profit == 1.26400
        assert decision.risk_reward_ratio == 2.0
        assert decision.max_risk_percent == 1.0
        assert decision.approved_lot_size is None
        assert len(decision.risk_violations) == 0


# ---------------------------------------------------------------------------
# 4. Trade Geometry & Structural Integrity Tests
# ---------------------------------------------------------------------------


class TestTradeGeometryViolations:
    def test_long_sl_above_entry_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08700,    # Inverted: SL above entry
            take_profit_1=1.09200,
            reasoning="Faulty geometry proposal",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert decision.approved_action == ForexAction.NO_TRADE
        assert any("LONG stop-loss" in v and "must be strictly below" in v for v in decision.risk_violations)

    def test_long_tp_below_entry_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit_1=1.08400,  # Inverted: TP below entry
            reasoning="Faulty TP geometry proposal",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("LONG take-profit" in v and "must be strictly above" in v for v in decision.risk_violations)

    def test_short_sl_below_entry_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="GBPUSD",
            action=ForexAction.SHORT,
            entry_price=1.27000,
            stop_loss=1.26800,    # Inverted: SL below entry
            take_profit_1=1.26200,
            reasoning="Faulty short geometry",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("SHORT stop-loss" in v and "must be strictly above" in v for v in decision.risk_violations)

    def test_short_tp_above_entry_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="GBPUSD",
            action=ForexAction.SHORT,
            entry_price=1.27000,
            stop_loss=1.27300,
            take_profit_1=1.27500,  # Inverted: TP above entry
            reasoning="Faulty short TP geometry",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("SHORT take-profit" in v and "must be strictly below" in v for v in decision.risk_violations)

    def test_missing_mandatory_entry_or_stop_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=None,
            stop_loss=None,
            reasoning="Incomplete proposal missing prices",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("entry price" in v for v in decision.risk_violations)
        assert any("stop-loss" in v for v in decision.risk_violations)

    def test_missing_mandatory_take_profit_rejected(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit_1=None,  # Missing TP
            reasoning="Proposal missing TP target",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("take-profit target is missing" in v for v in decision.risk_violations)


# ---------------------------------------------------------------------------
# 5. Pip Stop-Loss Bounds Tests
# ---------------------------------------------------------------------------


class TestPipStopLossBounds:
    def test_sl_too_tight_rejected(self):
        engine = ForexRiskEngine()
        # 4 pips stop (0.00040 on EURUSD) < min_sl_pips (8.0)
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08460,    # 4.0 pips
            take_profit_1=1.08600,
            reasoning="Ultra tight scalping stop",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("Stop-loss distance" in v and "below the minimum threshold" in v for v in decision.risk_violations)

    def test_sl_too_wide_rejected(self):
        engine = ForexRiskEngine()
        # 150 pips stop (0.01500 on EURUSD) > max_sl_pips (100.0)
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.07000,    # 150 pips
            take_profit_1=1.11000,
            reasoning="Excessively wide stop",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("Stop-loss distance" in v and "exceeds maximum allowable limit" in v for v in decision.risk_violations)


# ---------------------------------------------------------------------------
# 6. Risk-to-Reward Ratio (R:R) Tests
# ---------------------------------------------------------------------------


class TestRiskRewardValidation:
    def test_sub_one_rr_rejected_outright(self):
        engine = ForexRiskEngine()
        # Entry 1.08500, SL 1.08200 (30 pips), TP 1.08700 (20 pips) -> R:R 0.67 < 1.0
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit_1=1.08700,
            reasoning="Sub-1.0 R:R proposal",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("sub-1.0" in v for v in decision.risk_violations)

    def test_rr_between_one_and_target_triggers_modify(self):
        engine = ForexRiskEngine()
        # Entry 1.08500, SL 1.08200 (30 pips), TP 1.08860 (36 pips) -> R:R 1.20
        # 1.0 <= 1.20 < 1.50 -> Triggers MODIFY with suggested TP target
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit_1=1.08860,
            reasoning="Borderline R:R proposal",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.approved_action == ForexAction.LONG
        assert len(decision.modifications_required) >= 1
        assert any("Take-Profit target adjusted" in m for m in decision.modifications_required)
        # Check that take_profit was adjusted to 1.5R (1.08500 + 30 pips * 1.5 = 1.08950)
        assert decision.take_profit == 1.08950
        assert decision.risk_reward_ratio == 1.5

    def test_rr_between_one_and_target_rejected_if_modification_disallowed(self):
        strict_limits = ForexRiskLimits(allow_modification=False)
        engine = ForexRiskEngine(default_limits=strict_limits)

        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit_1=1.08860,  # R:R 1.20
            reasoning="Borderline R:R proposal under strict rules",
        )
        decision = engine.validate_proposal(proposal, curr_date="2025-06-11", curr_time_utc="14:00")

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("below required institutional threshold" in v for v in decision.risk_violations)


# ---------------------------------------------------------------------------
# 7. Economic Calendar News Blackout Window Tests
# ---------------------------------------------------------------------------


class TestEconomicNewsBlackout:
    def test_imminent_tier_one_event_triggers_blackout_rejection(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # NFP is on first Friday of month at 13:30 UTC.
        # On 2025-01-03 at 12:30 UTC (1 hour before USD NFP)
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-03",
            curr_time_utc="12:30",
        )

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert decision.approved_action == ForexAction.NO_TRADE
        assert any("Economic news blackout active" in v for v in decision.risk_violations)

    def test_approaching_tier_one_event_triggers_modify_with_size_cut(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # On 2025-01-03 at 07:00 UTC (6.5 hours before USD NFP at 13:30 UTC)
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-03",
            curr_time_utc="07:00",
        )

        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.approved_action == ForexAction.LONG
        assert any("Risk allocation reduced by 50%" in m for m in decision.modifications_required)
        assert decision.max_risk_percent == 0.5  # 1.0% cut in half

    def test_disabled_news_blackout_skips_check(self):
        limits = ForexRiskLimits(enforce_news_blackout=False)
        engine = ForexRiskEngine(default_limits=limits)
        proposal = make_valid_long_proposal()

        # Same NFP blackout window, but enforcement disabled
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-03",
            curr_time_utc="12:30",
        )

        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.LONG


# ---------------------------------------------------------------------------
# 8. Market Open & Weekend Closure Enforcement Tests
# ---------------------------------------------------------------------------


class TestMarketOpenEnforcement:
    def test_saturday_market_closed_rejected(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Saturday 2025-01-11 14:00 UTC
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-11",
            curr_time_utc="14:00",
        )

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("Forex market is closed" in v and "weekend closure" in v for v in decision.risk_violations)

    def test_sunday_before_open_rejected(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Sunday 2025-01-12 18:00 UTC (opens at 22:00 UTC)
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-12",
            curr_time_utc="18:00",
        )

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("Forex market is closed" in v for v in decision.risk_violations)

    def test_sunday_after_open_accepted(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Sunday 2025-01-12 23:00 UTC (market open for Sydney session)
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-12",
            curr_time_utc="23:00",
        )

        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.LONG

    def test_disabled_market_open_enforcement_skips_check(self):
        limits = ForexRiskLimits(enforce_market_open=False)
        engine = ForexRiskEngine(default_limits=limits)
        proposal = make_valid_long_proposal()

        # Saturday, but market open enforcement is off
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-01-11",
            curr_time_utc="14:00",
        )

        assert decision.decision == ForexRiskDecisionAction.APPROVE


# ---------------------------------------------------------------------------
# 9. Spread & Volatility Friction Limits Tests
# ---------------------------------------------------------------------------


class TestSpreadAndAtrFriction:
    def test_spread_exceeding_max_pips_rejected(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Spread is 4.5 pips > max 3.5 pips
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            current_spread_pips=4.5,
        )

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("exceeds maximum allowable threshold" in v for v in decision.risk_violations)

    def test_spread_to_atr_ratio_exceeding_limit_rejected(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()

        # Spread 3.0 pips on ATR of 15.0 pips -> ratio 20.0% > max 15.0%
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            current_spread_pips=3.0,
            atr_pips=15.0,
        )

        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert any("Spread-to-ATR ratio" in v and "exceeds maximum limit" in v for v in decision.risk_violations)


# ---------------------------------------------------------------------------
# 10. Equity Risk Clamping & Position Sizing Tests
# ---------------------------------------------------------------------------


class TestEquityRiskClampingAndSizing:
    def test_proposed_risk_exceeding_max_is_clamped_with_modify(self):
        engine = ForexRiskEngine()
        # Proposal requests 3.5% risk (policy maximum is 2.0%)
        proposal = make_valid_long_proposal(risk_pct=3.5)

        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            account_balance=10000.0,
        )

        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.max_risk_percent == 2.0  # Clamped to policy limit
        assert any("clamped from proposed 3.5%" in m for m in decision.modifications_required)

    def test_policy_engine_does_not_calculate_competing_lot_size(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal(entry=1.08500, sl=1.08200)

        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            account_balance=20000.0,
            account_currency="USD",
        )

        assert decision.approved_lot_size is None
        assert not any("Authorized position size" in c for c in decision.risk_checks_passed)


# ---------------------------------------------------------------------------
# 11. quick_check() and clamp_proposal_to_limits() Tests
# ---------------------------------------------------------------------------


class TestQuickCheckAndClamp:
    def test_quick_check_valid_setup(self):
        engine = ForexRiskEngine()
        proposal = make_valid_long_proposal()
        is_valid, violations = engine.quick_check(proposal)

        assert is_valid is True
        assert len(violations) == 0

    def test_quick_check_invalid_setup(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08600,    # Inverted geometry
            take_profit_1=1.09000,
            reasoning="Invalid geometry",
        )
        is_valid, violations = engine.quick_check(proposal)

        assert is_valid is False
        assert len(violations) >= 1

    def test_clamp_proposal_to_limits(self):
        engine = ForexRiskEngine()
        # Proposal with tight stop (4 pips), weak R:R (1.2), high risk (3.5%)
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08460,    # 4 pips (below min 8.0)
            take_profit_1=1.08548, # 4.8 pips (R:R 1.2)
            suggested_risk_percent=3.5,
            reasoning="Needs institutional clamping",
        )

        clamped = engine.clamp_proposal_to_limits(proposal)

        assert clamped.suggested_risk_percent == 2.0  # Clamped to max
        # SL adjusted to at least min_sl_pips (8.0 pips -> 1.08500 - 0.00080 = 1.08420)
        assert clamped.sl_pips == 8.0
        assert clamped.stop_loss == 1.08420
        # TP adjusted to achieve 1.5R (8.0 pips * 1.5 = 12.0 pips -> 1.08620)
        assert clamped.tp_pips == 12.0
        assert clamped.take_profit_1 == 1.08620
        assert clamped.risk_reward_ratio == 1.5


# ---------------------------------------------------------------------------
# 12. Convenience Function & Markdown Rendering
# ---------------------------------------------------------------------------


class TestConvenienceAndRendering:
    def test_validate_forex_proposal_function(self):
        proposal = make_valid_long_proposal()
        decision = validate_forex_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            account_balance=10000.0,
        )

        assert isinstance(decision, ForexRiskDecision)
        assert decision.decision == ForexRiskDecisionAction.APPROVE

    def test_render_forex_risk_decision(self):
        proposal = make_valid_long_proposal()
        decision = validate_forex_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            account_balance=10000.0,
        )

        md = render_forex_risk_decision(decision)
        assert "# Forex Risk Management Decision: EURUSD" in md
        assert "FINAL RISK DECISION: **APPROVE**" in md
        assert "**Authorized Risk Allocation**: 1.0% of equity" in md
        assert "Risk Controls Verified" in md

    def test_from_trader_proposal_adapter_flow(self):
        from tradingagents.agents.schemas import TraderAction, TraderProposal

        stock_proposal = TraderProposal(
            action=TraderAction.BUY,
            entry_price=1.08500,
            stop_loss=1.08200,
            reasoning="Converted proposal test",
        )
        forex_proposal = from_trader_proposal(stock_proposal, pair="EURUSD")
        # Set take profit to make it valid
        forex_proposal = ForexTraderProposal(
            pair=forex_proposal.pair,
            action=forex_proposal.action,
            entry_price=forex_proposal.entry_price,
            stop_loss=forex_proposal.stop_loss,
            take_profit_1=1.09000,
            reasoning=forex_proposal.reasoning,
        )

        decision = validate_forex_proposal(
            proposal=forex_proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
        )
        assert decision.decision == ForexRiskDecisionAction.APPROVE


class TestForexRiskEngineBrokerDigits:
    def test_jpy_pair_defaults_to_3_digits_in_rr_modification(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="USDJPY",
            action=ForexAction.LONG,
            entry_price=150.000,
            stop_loss=149.500,     # 50 pips
            take_profit_1=150.600,  # 60 pips (R:R = 1.2:1, between 1.0 and 1.5)
            reasoning="JPY test",
        )
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
        )
        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.take_profit == 150.750
        assert any("Take-Profit target adjusted to 150.750" in mod for mod in decision.modifications_required)

    def test_explicit_broker_digits_overrides_pair_default(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit_1=1.0886,  # 36 pips (R:R = 1.2:1)
            reasoning="4 digit test",
        )
        decision = engine.validate_proposal(
            proposal=proposal,
            curr_date="2025-06-11",
            curr_time_utc="14:00",
            broker_digits=4,
        )
        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.take_profit == 1.0895
        assert any("Take-Profit target adjusted to 1.0895" in mod for mod in decision.modifications_required)

    def test_clamp_proposal_to_limits_honors_digits(self):
        engine = ForexRiskEngine()
        proposal = ForexTraderProposal(
            pair="USDJPY",
            action=ForexAction.LONG,
            entry_price=150.000,
            stop_loss=148.000,  # 200 pips (exceeds default max_sl_pips=100.0)
            take_profit_1=150.500,  # Below 1.5 R:R
            reasoning="clamp test",
        )
        clamped = engine.clamp_proposal_to_limits(proposal)
        # Clamped to 100 pips (1.000 price distance): 150.000 - 1.000 = 149.000
        assert clamped.stop_loss == 149.000
        # TP adjusted to 1.5R: 150.000 + (1.000 * 1.5) = 151.500
        assert clamped.take_profit_1 == 151.500

        # With explicit broker_digits=1
        clamped_1d = engine.clamp_proposal_to_limits(proposal, broker_digits=1)
        assert clamped_1d.stop_loss == 149.0
        assert clamped_1d.take_profit_1 == 151.5


@pytest.fixture(autouse=True)
def explicit_calendar_fixture(monkeypatch):
    from tests.forex_calendar_fixture import fixture_query
    monkeypatch.setattr("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", fixture_query)

