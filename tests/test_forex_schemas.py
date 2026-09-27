"""Unit tests for Forex Trader Proposal Schema & Risk Decisions (Phase 9).

Validates:
- ForexAction, OrderType, SetupType, ForexRiskDecisionAction enums and string coercions.
- Deterministic pip distance and Risk:Reward arithmetic (compute_pips_and_rr).
- ForexTraderProposal schema validation, automatic derived fields (sl_pips, tp_pips, R:R).
- Trade geometry validation (SL below entry for LONG, above entry for SHORT).
- Conversion to and from legacy stock TraderProposal.
- ForexRiskDecision schema validation and risk controls.
- Institutional markdown rendering for both proposal and risk decision.
- Module exports from tradingagents.agents.schemas and tradingagents.forex.
"""


from tradingagents.agents.schemas import (
    TraderAction,
    TraderProposal,
)
from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
    compute_pips_and_rr,
    from_trader_proposal,
    render_forex_risk_decision,
    render_forex_trader_proposal,
)
from tradingagents.forex import (
    ForexAction as ForexActionExport,
    ForexRiskDecision as ForexRiskDecisionExport,
    ForexTraderProposal as ForexTraderProposalExport,
)

# ---------------------------------------------------------------------------
# 1. Enums and String Coercion Tests
# ---------------------------------------------------------------------------


class TestForexEnums:
    """Test enum members and tolerant string parsing."""

    def test_forex_action_from_str(self):
        assert ForexAction.from_str("BUY") == ForexAction.LONG
        assert ForexAction.from_str("long") == ForexAction.LONG
        assert ForexAction.from_str("bullish") == ForexAction.LONG

        assert ForexAction.from_str("SELL") == ForexAction.SHORT
        assert ForexAction.from_str("short") == ForexAction.SHORT
        assert ForexAction.from_str("bearish") == ForexAction.SHORT

        assert ForexAction.from_str("HOLD") == ForexAction.NO_TRADE
        assert ForexAction.from_str("no_trade") == ForexAction.NO_TRADE
        assert ForexAction.from_str("neutral") == ForexAction.NO_TRADE
        assert ForexAction.from_str("pass") == ForexAction.NO_TRADE
        assert ForexAction.from_str("unknown_xyz") == ForexAction.NO_TRADE

    def test_order_type_from_str(self):
        assert OrderType.from_str("market") == OrderType.MARKET
        assert OrderType.from_str("buy limit") == OrderType.BUY_LIMIT
        assert OrderType.from_str("BUY_LIMIT") == OrderType.BUY_LIMIT
        assert OrderType.from_str("sell stop") == OrderType.SELL_STOP
        assert OrderType.from_str("other") == OrderType.MARKET

    def test_setup_type_from_str(self):
        assert SetupType.from_str("breakout") == SetupType.BREAKOUT
        assert SetupType.from_str("trend-continuation") == SetupType.TREND_CONTINUATION
        assert SetupType.from_str("pullback") == SetupType.PULLBACK
        assert SetupType.from_str("news momentum") == SetupType.NEWS_MOMENTUM
        assert SetupType.from_str("random") == SetupType.TREND_CONTINUATION

    def test_risk_decision_action_from_str(self):
        assert ForexRiskDecisionAction.from_str("approve") == ForexRiskDecisionAction.APPROVE
        assert ForexRiskDecisionAction.from_str("accepted") == ForexRiskDecisionAction.APPROVE
        assert ForexRiskDecisionAction.from_str("reject") == ForexRiskDecisionAction.REJECT
        assert ForexRiskDecisionAction.from_str("decline") == ForexRiskDecisionAction.REJECT
        assert ForexRiskDecisionAction.from_str("modify") == ForexRiskDecisionAction.MODIFY
        assert ForexRiskDecisionAction.from_str("adjust") == ForexRiskDecisionAction.MODIFY


# ---------------------------------------------------------------------------
# 2. Pip and Risk:Reward Arithmetic Tests
# ---------------------------------------------------------------------------


class TestPipAndRiskRewardArithmetic:
    """Test deterministic compute_pips_and_rr function."""

    def test_long_standard_pair(self):
        # EURUSD: pip size = 0.0001
        # Entry: 1.0850, SL: 1.0820 (30 pips), TP: 1.0910 (60 pips) -> R:R = 2.0
        sl_pips, tp_pips, rr = compute_pips_and_rr(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry=1.0850,
            stop_loss=1.0820,
            take_profit=1.0910,
        )
        assert sl_pips == 30.0
        assert tp_pips == 60.0
        assert rr == 2.0

    def test_short_standard_pair(self):
        # GBPUSD: pip size = 0.0001
        # Entry: 1.2700, SL: 1.2725 (25 pips), TP: 1.2625 (75 pips) -> R:R = 3.0
        sl_pips, tp_pips, rr = compute_pips_and_rr(
            pair="GBPUSD",
            action=ForexAction.SHORT,
            entry=1.2700,
            stop_loss=1.2725,
            take_profit=1.2625,
        )
        assert sl_pips == 25.0
        assert tp_pips == 75.0
        assert rr == 3.0

    def test_jpy_pair(self):
        # USDJPY: pip size = 0.01
        # Entry: 155.00, SL: 154.50 (50 pips), TP: 156.25 (125 pips) -> R:R = 2.5
        sl_pips, tp_pips, rr = compute_pips_and_rr(
            pair="USDJPY",
            action=ForexAction.LONG,
            entry=155.00,
            stop_loss=154.50,
            take_profit=156.25,
        )
        assert sl_pips == 50.0
        assert tp_pips == 125.0
        assert rr == 2.5

    def test_missing_levels_return_none(self):
        sl_pips, tp_pips, rr = compute_pips_and_rr(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry=None,
            stop_loss=1.0820,
            take_profit=1.0910,
        )
        assert sl_pips is None
        assert tp_pips is None
        assert rr is None


# ---------------------------------------------------------------------------
# 3. ForexTraderProposal Model Tests
# ---------------------------------------------------------------------------


class TestForexTraderProposalModel:
    """Test ForexTraderProposal validation, coercion, and geometry checks."""

    def test_auto_derivation_of_pips_and_rr(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0825,
            take_profit_1=1.0900,
            reasoning="Bullish break of market structure on H1.",
        )
        # 1.0850 - 1.0825 = 0.0025 -> 25 pips
        assert proposal.sl_pips == 25.0
        # 1.0900 - 1.0850 = 0.0050 -> 50 pips
        assert proposal.tp_pips == 50.0
        # 50 / 25 = 2.0
        assert proposal.risk_reward_ratio == 2.0

    def test_string_numeric_coercion(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action="BUY",  # coerced to LONG
            entry_price="1.0850",
            stop_loss="$1.0820",
            take_profit_1="1.0910",
            take_profit_2="None",  # coerced to None
            risk_reward_ratio="2.0",
            suggested_risk_percent="1.5",
            reasoning="Valid setup.",
        )
        assert proposal.action == ForexAction.LONG
        assert proposal.entry_price == 1.0850
        assert proposal.stop_loss == 1.0820
        assert proposal.take_profit_1 == 1.0910
        assert proposal.take_profit_2 is None
        assert proposal.suggested_risk_percent == 1.5

    def test_validate_trade_geometry_valid_long(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit_1=1.0910,
            reasoning="Valid long geometry.",
        )
        is_valid, violations = proposal.validate_trade_geometry()
        assert is_valid is True
        assert len(violations) == 0

    def test_validate_trade_geometry_invalid_long_sl_above_entry(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0880,  # Invalid: SL above entry for LONG
            take_profit_1=1.0920,
            reasoning="Invalid long geometry.",
        )
        is_valid, violations = proposal.validate_trade_geometry()
        assert is_valid is False
        assert any("stop-loss" in v and "below" in v for v in violations)

    def test_validate_trade_geometry_invalid_short_sl_below_entry(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.SHORT,
            entry_price=1.0850,
            stop_loss=1.0820,  # Invalid: SL below entry for SHORT
            take_profit_1=1.0780,
            reasoning="Invalid short geometry.",
        )
        is_valid, violations = proposal.validate_trade_geometry()
        assert is_valid is False
        assert any("stop-loss" in v and "above" in v for v in violations)

    def test_validate_trade_geometry_no_trade(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            reasoning="Market in consolidation ahead of FOMC.",
        )
        is_valid, violations = proposal.validate_trade_geometry()
        assert is_valid is True

    def test_sub_one_rr_flagged(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0810,   # 40 pips SL
            take_profit_1=1.0870, # 20 pips TP -> R:R 0.5:1
            reasoning="Low R:R setup.",
        )
        is_valid, violations = proposal.validate_trade_geometry()
        assert is_valid is False
        assert any("Risk:Reward ratio" in v for v in violations)


# ---------------------------------------------------------------------------
# 4. Conversion to/from Legacy TraderProposal Tests
# ---------------------------------------------------------------------------


class TestTraderProposalConversion:
    """Test bidirectional compatibility with stock/crypto TraderProposal."""

    def test_to_trader_proposal(self):
        ftp = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit_1=1.0910,
            suggested_risk_percent=1.0,
            suggested_lot_size=0.5,
            reasoning="Clean H1 pullback setup.",
        )
        legacy = ftp.to_trader_proposal()
        assert isinstance(legacy, TraderProposal)
        assert legacy.action == TraderAction.BUY
        assert legacy.entry_price == 1.0850
        assert legacy.stop_loss == 1.0820
        assert "1.0% risk" in legacy.position_sizing
        assert "0.50 lots" in legacy.position_sizing

    def test_from_trader_proposal(self):
        legacy = TraderProposal(
            action=TraderAction.SELL,
            entry_price=1.2650,
            stop_loss=1.2690,
            reasoning="Bearish market structure.",
        )
        ftp = from_trader_proposal(legacy, pair="GBPUSD", timeframe="H4")
        assert isinstance(ftp, ForexTraderProposal)
        assert ftp.pair == "GBPUSD"
        assert ftp.action == ForexAction.SHORT
        assert ftp.timeframe == "H4"
        assert ftp.entry_price == 1.2650
        assert ftp.stop_loss == 1.2690
        assert ftp.sl_pips == 40.0


# ---------------------------------------------------------------------------
# 5. ForexRiskDecision Tests
# ---------------------------------------------------------------------------


class TestForexRiskDecisionModel:
    """Test ForexRiskDecision validation and state representation."""

    def test_approved_decision(self):
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.45,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit=1.0910,
            risk_reward_ratio=2.0,
            risk_checks_passed=["R:R >= 1.5:1 (2.0:1)", "No Tier-1 news in 2h", "Spread acceptable (0.8 pips)"],
            executive_rationale="Trade proposal meets all institutional risk criteria.",
        )
        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.LONG
        assert decision.max_risk_percent == 1.0
        assert len(decision.risk_checks_passed) == 3

    def test_rejected_decision(self):
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision="REJECT",
            original_action="BUY",
            approved_action="HOLD",
            risk_violations=["High-impact NFP release in 45 minutes (Blackout Active)"],
            executive_rationale="Execution blocked due to pre-news blackout.",
        )
        assert decision.decision == ForexRiskDecisionAction.REJECT
        assert decision.approved_action == ForexAction.NO_TRADE
        assert len(decision.risk_violations) == 1

    def test_modified_decision(self):
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision="MODIFY",
            original_action="LONG",
            approved_action="LONG",
            max_risk_percent=0.5,
            approved_lot_size=0.20,
            modifications_required=["Reduced position size to 0.5% risk due to Asian session low liquidity"],
            executive_rationale="Approved with half-size allocation.",
        )
        assert decision.decision == ForexRiskDecisionAction.MODIFY
        assert decision.max_risk_percent == 0.5


# ---------------------------------------------------------------------------
# 6. Markdown Rendering Tests
# ---------------------------------------------------------------------------


class TestMarkdownRendering:
    """Test institutional markdown formatting for proposals and risk decisions."""

    def test_render_forex_trader_proposal(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.BUY_LIMIT,
            setup_type=SetupType.PULLBACK,
            timeframe="H1",
            entry_price=1.0850,
            entry_zone_low=1.0845,
            entry_zone_high=1.0855,
            stop_loss=1.0820,
            take_profit_1=1.0910,
            take_profit_2=1.0950,
            suggested_risk_percent=1.0,
            confluence_factors=["H4 50 EMA support", "London session open", "Bullish FVG fill"],
            invalidation_condition="H1 close below 1.0815",
            reasoning="Clean retest of breakout zone with bullish pin bar confirmation.",
        )
        rendered = render_forex_trader_proposal(proposal)

        assert "# Forex Trade Proposal: EURUSD" in rendered
        assert "- **Action**: **LONG**" in rendered
        assert "- **Order Type**: BUY_LIMIT" in rendered
        assert "- **Setup Classification**: PULLBACK" in rendered
        assert "- **Timeframe**: H1" in rendered
        assert "- **Entry Price**: 1.08500" in rendered
        assert "- **Entry Zone**: [1.08450 – 1.08550]" in rendered
        assert "- **Stop Loss**: 1.08200 (30.0 pips)" in rendered
        assert "- **Take Profit 1**: 1.09100 (60.0 pips)" in rendered
        assert "- **Risk:Reward Ratio**: 2.00:1" in rendered
        assert "### Confluence Factors" in rendered
        assert "✅ H4 50 EMA support" in rendered
        assert "FINAL FOREX PROPOSAL: **LONG**" in rendered

    def test_render_forex_risk_decision(self):
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.50,
            entry_price=1.0850,
            stop_loss=1.0820,
            take_profit=1.0910,
            risk_reward_ratio=2.0,
            risk_checks_passed=["R:R >= 1.5:1", "SL <= 50 pips"],
            executive_rationale="Trade is cleared for execution.",
        )
        rendered = render_forex_risk_decision(decision)

        assert "# Forex Risk Management Decision: EURUSD" in rendered
        assert "APPROVED" in rendered
        assert "- **Original Proposed Action**: LONG" in rendered
        assert "- **Authorized Execution Action**: **LONG**" in rendered
        assert "- **Authorized Risk Allocation**: 1.0% of equity" in rendered
        assert "- **Authorized Position Size**: 0.50 standard lots" in rendered
        assert "### Risk Controls Verified" in rendered
        assert "✅ R:R >= 1.5:1" in rendered
        assert "FINAL RISK DECISION: **APPROVE**" in rendered


# ---------------------------------------------------------------------------
# 7. Module Exports Tests
# ---------------------------------------------------------------------------


class TestModuleExports:
    """Verify that schemas are cleanly accessible from main packages."""

    def test_exports_from_forex_package(self):
        assert ForexActionExport is ForexAction
        assert ForexTraderProposalExport is ForexTraderProposal
        assert ForexRiskDecisionExport is ForexRiskDecision

    def test_exports_from_agents_schemas(self):
        from tradingagents.agents.schemas import (
            ForexAction as FA,
            ForexRiskDecision as FRD,
            ForexTraderProposal as FTP,
        )
        assert FA is ForexAction
        assert FTP is ForexTraderProposal
        assert FRD is ForexRiskDecision
