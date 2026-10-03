"""Unit and integration test suite for Institutional Forex Position Sizing Engine (Phase 11).

Tests:
1. Fixed Risk Percentage model.
2. Fixed Monetary Amount model.
3. Volatility-Adjusted (ATR-based) sizing.
4. Kelly Criterion & Fractional Kelly sizing.
5. Account Balance Tier Step sizing.
6. Broker volume normalization (flooring, min_volume, max_volume, volume_step).
7. Required margin & leverage calculations (USD-quoted, USD-base, cross pairs).
8. Margin adequacy & margin call safety rejection.
9. Portfolio currency net exposure tracking.
10. Pair correlation co-exposure discount scaling.
11. Cumulative portfolio risk ceiling enforcement.
12. Sizing directly from ForexTraderProposal.
13. Module-level convenience helper calculate_forex_position_size().
"""

from __future__ import annotations

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.forex import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    ForexPositionSizingEngine,
    OpenPosition,
    PendingExposure,
    PositionSizingMethod,
    PositionSizingResult,
    calculate_correlation_exposure_factor,
    calculate_currency_risk_exposure,
    calculate_forex_position_size,
    calculate_kelly_risk_percent,
    calculate_portfolio_currency_exposure,
    calculate_required_margin,
    calculate_volatility_adjusted_risk_percent,
    get_pair_correlation,
)

# ---------------------------------------------------------------------------
# 1. Fixed Risk Percentage Sizing Tests
# ---------------------------------------------------------------------------


def test_missing_free_margin_derives_from_supplied_equity_not_default_balance():
    account = ForexAccountProfile(balance=50.0, equity=45.0, used_margin=15.0)
    assert account.free_margin == 30.0
    assert account.free_margin != 10000.0


def test_explicit_small_account_free_margin_is_preserved():
    account = ForexAccountProfile(balance=50.0, equity=45.0, free_margin=30.0, leverage=1000)
    assert account.model_dump()["free_margin"] == 30.0


class TestFixedRiskPercentSizing:
    def test_standard_eurusd_one_percent_risk(self):
        engine = ForexPositionSizingEngine()
        # $10,000 equity, 1% risk ($100), 25 pips stop on EURUSD ($10/pip per std lot)
        # raw lots = 100 / (25 * 10) = 0.40 lots
        account = ForexAccountProfile(equity=10000.0, balance=10000.0, free_margin=10000.0)

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,    # 25 pips
            sizing_method=PositionSizingMethod.FIXED_RISK_PERCENT,
            account=account,
            risk_percent=1.0,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size == 0.40
        assert result.units == 40000.0
        assert result.risk_amount == 100.0
        assert result.risk_percent == 1.0
        assert result.stop_distance_pips == 25.0
        assert result.margin_required > 0.0
        assert result.free_margin_remaining < 10000.0

    def test_risk_percent_exceeding_account_limit_is_clamped(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0, max_single_trade_risk_percent=2.0)

        # Requests 3.5% risk -> should be clamped to 2.0% ($200 risk)
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,    # 25 pips
            account=account,
            risk_percent=3.5,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size == 0.80  # $200 / (25 * 10) = 0.80
        assert result.risk_percent == 2.0
        assert any("clamped to account limit" in a for a in result.adjustments_applied)


# ---------------------------------------------------------------------------
# 2. Fixed Monetary Amount Sizing Tests
# ---------------------------------------------------------------------------


class TestFixedMonetaryAmountSizing:
    def test_fixed_monetary_amount(self):
        engine = ForexPositionSizingEngine()
        # $25,000 equity, fixed $150 dollar risk, 30 pips stop on GBPUSD ($10/pip)
        # raw lots = 150 / (30 * 10) = 0.50 lots
        account = ForexAccountProfile(equity=25000.0, balance=25000.0, free_margin=25000.0)

        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.SHORT,
            entry_price=1.27000,
            stop_loss=1.27300,    # 30 pips
            sizing_method=PositionSizingMethod.FIXED_MONETARY_AMOUNT,
            account=account,
            monetary_amount=150.0,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size == 0.50
        assert result.risk_amount == 150.0
        assert result.risk_percent == 0.60  # $150 on $25,000 = 0.60%


# ---------------------------------------------------------------------------
# 3. Volatility-Adjusted (ATR) Sizing Tests
# ---------------------------------------------------------------------------


class TestVolatilityAdjustedSizing:
    def test_high_volatility_scales_down_size(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0)

        # Baseline ATR is 50 pips. Current ATR is 100 pips (high volatility).
        # Vol factor = 50 / 100 = 0.50x. Base risk 1.0% -> 0.50% risk ($50).
        # Stop distance = 50 pips. Lots = 50 / (50 * 10) = 0.10 lots.
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08000,    # 50 pips
            sizing_method=PositionSizingMethod.VOLATILITY_ADJUSTED,
            account=account,
            risk_percent=1.0,
            atr_pips=100.0,
            baseline_atr_pips=50.0,
        )

        assert result.is_executable is True
        assert result.risk_percent == 0.50
        assert result.recommended_lot_size == 0.10
        assert any("Volatility adjustment" in a for a in result.adjustments_applied)

    def test_low_volatility_scales_up_size_within_clamp(self):
        # Baseline ATR 50 pips. Current ATR 25 pips.
        # Vol factor = 50 / 25 = 2.0x, clamped to max_scale 1.5x.
        # Risk 1.0% -> 1.50% risk.
        adj_risk, factor = calculate_volatility_adjusted_risk_percent(
            base_risk_percent=1.0,
            atr_pips=25.0,
            baseline_atr_pips=50.0,
            max_scale=1.5,
        )

        assert factor == 1.50
        assert adj_risk == 1.50


# ---------------------------------------------------------------------------
# 4. Kelly Criterion Sizing Tests
# ---------------------------------------------------------------------------


class TestKellyCriterionSizing:
    def test_positive_edge_quarter_kelly(self):
        # 60% win rate, 1.5 win/loss ratio.
        # Full Kelly = (0.60 * 1.5 - 0.40) / 1.5 = 0.50 / 1.5 = 0.3333 (33.33%).
        # Quarter Kelly (0.25x) = 8.33%, clamped to max_risk 2.0%.
        risk_pct, full_k = calculate_kelly_risk_percent(
            win_rate=0.60,
            win_loss_ratio=1.5,
            kelly_fraction=0.25,
            max_risk_percent=2.0,
        )

        assert full_k == 0.3333
        assert risk_pct == 2.0  # Capped at max risk

    def test_moderate_edge_under_max_risk(self):
        # 52% win rate, 1.2 win/loss ratio.
        # Full Kelly = (0.52 * 1.2 - 0.48) / 1.2 = (0.624 - 0.48) / 1.2 = 0.144 / 1.2 = 0.12 (12%).
        # Quarter Kelly (0.25x) = 3%... capped at 2.0%
        # Let's test with 0.10x fraction: 0.12 * 0.10 = 1.2%
        risk_pct, full_k = calculate_kelly_risk_percent(
            win_rate=0.52,
            win_loss_ratio=1.2,
            kelly_fraction=0.10,
            max_risk_percent=2.0,
        )

        assert full_k == 0.12
        assert risk_pct == 1.20

    def test_negative_edge_kelly_rejects(self):
        # 35% win rate, 1.0 payoff ratio -> Negative expectancy.
        risk_pct, full_k = calculate_kelly_risk_percent(
            win_rate=0.35,
            win_loss_ratio=1.0,
        )

        assert full_k < 0.0
        assert risk_pct == 0.0

        engine = ForexPositionSizingEngine()
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            sizing_method=PositionSizingMethod.KELLY_CRITERION,
            win_rate=0.35,
            win_loss_ratio=1.0,
        )

        assert result.is_executable is False
        assert result.recommended_lot_size == 0.0
        assert "Negative Kelly edge" in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# 5. Balance Step Sizing Tests
# ---------------------------------------------------------------------------


class TestBalanceStepSizing:
    def test_balance_step_brackets(self):
        engine = ForexPositionSizingEngine()

        # Bracket 1: < $5k -> 0.5% risk
        res1 = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            sizing_method=PositionSizingMethod.BALANCE_STEP,
            account=ForexAccountProfile(equity=3000.0, balance=3000.0, free_margin=3000.0),
        )
        assert res1.risk_percent == 0.5

        # Bracket 2: $5k - $25k -> 1.0% risk
        res2 = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            sizing_method=PositionSizingMethod.BALANCE_STEP,
            account=ForexAccountProfile(equity=15000.0, balance=15000.0, free_margin=15000.0),
        )
        assert res2.risk_percent == 1.0

        # Bracket 3: $25k - $100k -> 1.5% risk
        res3 = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            sizing_method=PositionSizingMethod.BALANCE_STEP,
            account=ForexAccountProfile(equity=50000.0, balance=50000.0, free_margin=50000.0),
        )
        assert res3.risk_percent == 1.5

        # Bracket 4: >= $100k -> 2.0% risk
        res4 = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            sizing_method=PositionSizingMethod.BALANCE_STEP,
            account=ForexAccountProfile(equity=200000.0, balance=200000.0, free_margin=200000.0),
        )
        assert res4.risk_percent == 2.0


# ---------------------------------------------------------------------------
# 6. Broker Volume Normalization & Bounds Tests
# ---------------------------------------------------------------------------


class TestBrokerVolumeNormalization:
    def test_volume_step_flooring_prevents_risk_overrun(self):
        engine = ForexPositionSizingEngine()
        # raw lot = $100 / (23 pips * $10) = 0.43478... lots
        # Step is 0.01: must floor down to 0.43 lots (not 0.44 to prevent risk overrun)
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08270,    # 23 pips
            account=ForexAccountProfile(equity=10000.0),
            risk_percent=1.0,
        )

        assert result.recommended_lot_size == 0.43
        assert result.raw_lot_size > 0.434
        assert result.risk_amount <= 100.0

    def test_below_min_volume_rejected(self):
        engine = ForexPositionSizingEngine()
        # $100 account with 1% risk ($1.00) and 30 pips stop:
        # raw lot = 1.00 / 300 = 0.0033 lots < 0.01 min_volume
        account = ForexAccountProfile(equity=100.0, balance=100.0, free_margin=100.0)

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,    # 30 pips
            account=account,
            risk_percent=1.0,
        )

        assert result.is_executable is False
        assert result.recommended_lot_size == 0.0
        assert "below broker minimum volume" in (result.rejection_reason or "")

    def test_above_max_volume_clamped(self):
        engine = ForexPositionSizingEngine()
        # Huge $10,000,000 account, 2% risk ($200,000), 10 pip stop -> raw 2,000 lots
        account = ForexAccountProfile(equity=10000000.0, balance=10000000.0, free_margin=10000000.0)
        constraints = BrokerExecutionConstraints(max_volume=50.0)

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08400,
            account=account,
            constraints=constraints,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size == 50.0
        assert any("clamped from" in a and "50.00" in a for a in result.adjustments_applied)


# ---------------------------------------------------------------------------
# 7. Required Margin & Leverage Tests
# ---------------------------------------------------------------------------


class TestMarginAndLeverage:
    def test_usd_quoted_pair_margin(self):
        # EURUSD: 1.0 lot, entry 1.08500, leverage 1:100
        # Notional in USD = 100,000 * 1.08500 = $108,500. Margin = $1,085.00
        margin = calculate_required_margin(
            pair="EURUSD",
            lot_size=1.0,
            entry_price=1.08500,
            leverage=100.0,
            account_currency="USD",
        )
        assert margin == 1085.00

    def test_usd_base_pair_margin(self):
        # USDJPY: 1.0 lot, entry 155.00, leverage 1:100
        # Notional in USD = 100,000. Margin = $1,000.00
        margin = calculate_required_margin(
            pair="USDJPY",
            lot_size=1.0,
            entry_price=155.00,
            leverage=100.0,
            account_currency="USD",
        )
        assert margin == 1000.00

    def test_insufficient_free_margin_rejected(self):
        engine = ForexPositionSizingEngine()
        # $1,000 equity, but only $50 free margin left!

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,    # 25 pips -> 0.04 lots -> requires ~$43.40 margin...
            # let's set free margin to $10.0
            account=ForexAccountProfile(equity=1000.0, balance=1000.0, free_margin=10.0, leverage=100.0),
        )

        assert result.is_executable is False
        assert result.recommended_lot_size == 0.0
        assert "Insufficient free margin" in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# 8. Portfolio Currency Exposure & Correlation Tests
# ---------------------------------------------------------------------------


class TestPortfolioExposureAndCorrelation:
    def test_currency_exposure_aggregation(self):
        positions = [
            OpenPosition(
                position_id="pos-1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.0850,
                stop_loss=1.0800,
                risk_amount=500.0,
            ),
            OpenPosition(
                position_id="pos-2",
                pair="USDJPY",
                action=ForexAction.SHORT,
                lots=0.5,
                entry_price=155.00,
                stop_loss=156.00,
                risk_amount=320.0,
            ),
        ]

        # Long 1.0 EURUSD -> +1.0 EUR, -1.0 USD
        # Short 0.5 USDJPY -> -0.5 USD, +0.5 JPY
        # Net USD exposure: -1.0 + (-0.5) = -1.5 lots
        exposures = calculate_portfolio_currency_exposure(positions)

        assert exposures["EUR"] == 1.0
        assert exposures["USD"] == -1.5
        assert exposures["JPY"] == 0.5

    def test_correlation_exposure_scaling_for_same_direction(self):
        # Open long EURUSD (1.0 lot)
        positions = [
            OpenPosition(
                position_id="pos-1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.0850,
                stop_loss=1.0800,
                risk_amount=500.0,
            ),
        ]

        # Evaluating Long GBPUSD (correlation +0.82)
        factor, correlated = calculate_correlation_exposure_factor(
            new_pair="GBPUSD",
            new_action=ForexAction.LONG,
            open_positions=positions,
        )

        assert factor < 1.0
        assert len(correlated) == 1
        assert "EURUSD" in correlated[0]
        assert "corr: +0.82" in correlated[0]

    def test_portfolio_risk_capacity_ceiling_enforced(self):
        engine = ForexPositionSizingEngine()
        # $10,000 equity, max portfolio risk 6.0% ($600).
        # Existing open positions already risk $550 (5.5%).
        # New trade would risk $100 (total $650 = 6.5% > 6.0%) -> REJECT
        account = ForexAccountProfile(
            equity=10000.0,
            balance=10000.0,
            free_margin=8000.0,
            max_account_risk_percent=6.0,
        )
        positions = [
            OpenPosition(
                position_id="p1",
                pair="USDCHF",
                action=ForexAction.LONG,
                lots=0.8,
                entry_price=0.9000,
                stop_loss=0.8950,
                risk_amount=550.0,
            )
        ]

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            account=account,
            risk_percent=1.0,
            open_positions=positions,
        )

        assert result.is_executable is False
        assert "Cumulative portfolio risk" in (result.rejection_reason or "")


# ---------------------------------------------------------------------------
# 9. ForexTraderProposal Sizing & Convenience Helpers
# ---------------------------------------------------------------------------


class TestProposalSizingAndConvenience:
    def test_size_forex_trader_proposal(self):
        engine = ForexPositionSizingEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.MARKET,
            setup_type=SetupType.PULLBACK,
            entry_price=1.08500,
            stop_loss=1.08200,    # 30 pips
            take_profit_1=1.09000,
            suggested_risk_percent=1.5,
            reasoning="H1 Bullish setup",
        )

        account = ForexAccountProfile(equity=20000.0, balance=20000.0, free_margin=20000.0)
        result = engine.size_proposal(proposal=proposal, account=account)

        assert result.is_executable is True
        assert result.pair == "EURUSD"
        assert result.action == ForexAction.LONG
        assert result.stop_distance_pips == 30.0
        # 1.5% of $20,000 = $300. $300 / (30 * 10) = 1.00 lot
        assert result.recommended_lot_size == 1.00
        assert result.risk_amount == 300.0

    def test_size_no_trade_proposal(self):
        engine = ForexPositionSizingEngine()
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            reasoning="High impact news event, no trade setup.",
        )

        result = engine.size_proposal(proposal=proposal)
        assert result.is_executable is True
        assert result.recommended_lot_size == 0.0
        assert result.risk_amount == 0.0

    def test_calculate_forex_position_size_convenience_function(self):
        result = calculate_forex_position_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            account_balance=10000.0,
            risk_percent=1.0,
        )

        assert isinstance(result, PositionSizingResult)
        assert result.is_executable is True
        # 1% of $10,000 = $100. $100 / (30 * 10) = 0.33 lots
        assert result.recommended_lot_size == 0.33
        assert result.risk_amount == 99.0

    def test_get_pair_correlation_matrix(self):
        assert get_pair_correlation("EURUSD", "GBPUSD") == 0.82
        assert get_pair_correlation("GBPUSD", "EURUSD") == 0.82
        assert get_pair_correlation("EURUSD", "USDCHF") == -0.88
        assert get_pair_correlation("EURUSD", "EURUSD") == 1.0
        assert get_pair_correlation("EURUSD", "UNKNOWN") == 0.0


# ---------------------------------------------------------------------------
# 10. Portfolio Risk Boundary & Fail-Closed Tests (Phase 4 / PORT-005, PORT-006, PORT-010, PORT-011, PORT-012)
# ---------------------------------------------------------------------------


class TestPortfolioFailClosedAndBoundaryRisks:
    def test_open_position_without_stop_loss_fails_closed(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0, balance=10000.0)
        positions = [
            OpenPosition(
                position_id="unhedged-pos-1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.08500,
                stop_loss=None,  # Unbounded risk
                risk_amount=None,
            )
        ]

        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.LONG,
            entry_price=1.27000,
            stop_loss=1.26700,
            account=account,
            open_positions=positions,
        )

        assert result.is_executable is False
        assert "unbounded because an open position has no objective stop-loss risk" in (result.rejection_reason or "")

    def test_open_position_without_risk_amount_fails_closed(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0, balance=10000.0)
        positions = [
            OpenPosition(
                position_id="unhedged-pos-2",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.08500,
                stop_loss=1.08000,
                risk_amount=None,  # Uncomputed/unknown risk amount
            )
        ]

        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.LONG,
            entry_price=1.27000,
            stop_loss=1.26700,
            account=account,
            open_positions=positions,
        )

        assert result.is_executable is False
        assert "unbounded because an open position has no objective stop-loss risk" in (result.rejection_reason or "")

    def test_calculate_currency_risk_exposure_raises_on_unbounded_risk(self):
        positions = [
            OpenPosition(
                position_id="p1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.0850,
                stop_loss=None,
                risk_amount=None,
            )
        ]
        with pytest.raises(ValueError, match="currency risk exposure is unknown for an unbounded position"):
            calculate_currency_risk_exposure(positions)

    def test_pending_order_without_stop_loss_fails_closed(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0, balance=10000.0)
        pending = [
            PendingExposure(
                order_id="pending-no-sl",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=0.5,
                trigger_price=1.08500,
                stop_loss=None,
                reserved_risk=None,
            )
        ]

        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.LONG,
            entry_price=1.27000,
            stop_loss=1.26700,
            account=account,
            pending_exposures=pending,
        )

        assert result.is_executable is False
        assert "unbounded because a pending order has no objective stop-loss risk" in (result.rejection_reason or "")


class TestCurrencyRiskConcentration:
    def test_net_stop_risk_exposure_multi_positions_and_pending(self):
        # Open positions:
        # Long EURUSD, risk $200 -> +EUR 200, -USD 200
        # Long GBPUSD, risk $150 -> +GBP 150, -USD 150
        positions = [
            OpenPosition(
                position_id="p1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.0,
                entry_price=1.0850,
                stop_loss=1.0830,
                risk_amount=200.0,
            ),
            OpenPosition(
                position_id="p2",
                pair="GBPUSD",
                action=ForexAction.LONG,
                lots=0.8,
                entry_price=1.2700,
                stop_loss=1.2670,
                risk_amount=150.0,
            ),
        ]
        # Pending exposure: Short EURGBP, risk $100 -> -EUR 100, +GBP 100
        pending = [
            PendingExposure(
                order_id="ord-1",
                pair="EURGBP",
                action=ForexAction.SHORT,
                lots=0.5,
                trigger_price=0.8550,
                stop_loss=0.8600,
                reserved_risk=100.0,
            )
        ]

        # Calculate exposure before new trade:
        # EUR: +200 - 100 = +100
        # GBP: +150 + 100 = +250
        # USD: -200 - 150 = -350
        exposure = calculate_currency_risk_exposure(positions, pending_exposures=pending)
        assert exposure["EUR"] == 100.0
        assert exposure["GBP"] == 250.0
        assert exposure["USD"] == -350.0

        # Now include new proposed trade: Long USDJPY, risk $100 -> +USD 100, -JPY 100
        # USD: -350 + 100 = -250
        # JPY: -100
        exposure_with_new = calculate_currency_risk_exposure(
            positions,
            pending_exposures=pending,
            new_pair="USDJPY",
            new_action=ForexAction.LONG,
            new_risk_amount=100.0,
        )
        assert exposure_with_new["USD"] == -250.0
        assert exposure_with_new["JPY"] == -100.0
        assert exposure_with_new["EUR"] == 100.0
        assert exposure_with_new["GBP"] == 250.0

    def test_currency_concentration_limit_strictly_enforced(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(
            equity=10000.0,
            balance=10000.0,
            max_currency_exposure_percent=5.0,
            max_account_risk_percent=10.0,
        )
        # Open position risking $450 on EURUSD (USD exposure -450).
        positions = [
            OpenPosition(
                position_id="p1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.5,
                entry_price=1.08500,
                stop_loss=1.08200,
                risk_amount=450.0,
            )
        ]

        # New trade risking $100 on GBPUSD (USD exposure -100).
        # Cumulative USD exposure becomes -$550 = 5.5% > 5.0% -> REJECT
        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.LONG,
            entry_price=1.27000,
            stop_loss=1.26700,
            account=account,
            risk_percent=1.0,
            open_positions=positions,
        )

        assert result.is_executable is False
        assert "Net currency stop-risk concentration exceeds 5.00% of equity: USD" in (result.rejection_reason or "")

    def test_opposing_positions_net_currency_stop_risk_allowing_execution(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(
            equity=10000.0,
            balance=10000.0,
            max_currency_exposure_percent=5.0,
            max_account_risk_percent=10.0,
        )
        # Open position: Long EURUSD risking $450 (-USD 450)
        positions = [
            OpenPosition(
                position_id="p1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=1.5,
                entry_price=1.08500,
                stop_loss=1.08200,
                risk_amount=450.0,
            )
        ]

        # New trade: Short EURUSD risking $100 (+USD 100)
        # Net USD risk reduces from -$450 to -$350 = 3.5% <= 5.0% -> ALLOWED
        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.SHORT,
            entry_price=1.08500,
            stop_loss=1.08800,
            account=account,
            risk_percent=1.0,
            open_positions=positions,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size > 0.0


class TestPendingExposureReservationAndDeduplication:
    def test_pending_order_reserves_cumulative_risk(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(
            equity=10000.0,
            balance=10000.0,
            max_account_risk_percent=6.0,
        )
        positions = [
            OpenPosition(
                position_id="open-1",
                pair="USDCHF",
                action=ForexAction.LONG,
                lots=0.5,
                entry_price=0.9000,
                stop_loss=0.8950,
                risk_amount=300.0,
            )
        ]
        pending = [
            PendingExposure(
                order_id="pending-1",
                pair="EURUSD",
                action=ForexAction.LONG,
                lots=0.5,
                trigger_price=1.08500,
                stop_loss=1.0800,
                reserved_risk=250.0,
            )
        ]

        result = engine.compute_size(
            pair="GBPUSD",
            action=ForexAction.LONG,
            entry_price=1.27000,
            stop_loss=1.26700,
            account=account,
            risk_percent=1.0,
            open_positions=positions,
            pending_exposures=pending,
        )

        assert result.is_executable is False
        assert "Cumulative portfolio risk (6.5%) would exceed account maximum risk ceiling" in (result.rejection_reason or "")

    def test_pending_order_deduplicated_by_ticket_id(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(
            equity=10000.0,
            balance=10000.0,
            max_account_risk_percent=6.0,
        )
        positions = [
            OpenPosition(
                position_id="ticket-100",
                pair="USDCHF",
                action=ForexAction.LONG,
                lots=0.5,
                entry_price=0.9000,
                stop_loss=0.8950,
                risk_amount=300.0,
            )
        ]
        pending = [
            PendingExposure(
                order_id="ticket-100",
                pair="USDCHF",
                action=ForexAction.LONG,
                lots=0.5,
                trigger_price=0.9000,
                stop_loss=0.8950,
                reserved_risk=300.0,
            )
        ]

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08200,
            account=account,
            risk_percent=1.0,
            open_positions=positions,
            pending_exposures=pending,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size > 0.0


class TestBrokerConstraintsAndVolumeStepping:
    def test_volume_step_flooring_avoids_risk_overrun(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=10000.0, balance=10000.0)
        constraints = BrokerExecutionConstraints(
            volume_step=0.05,
            min_volume=0.05,
            max_volume=100.0,
        )

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08250,
            account=account,
            risk_percent=0.35,
            constraints=constraints,
        )

        assert result.is_executable is True
        assert result.recommended_lot_size == 0.10
        assert result.risk_amount <= 35.0

    def test_lot_size_below_broker_min_volume_rejected(self):
        engine = ForexPositionSizingEngine()
        account = ForexAccountProfile(equity=500.0, balance=500.0)
        constraints = BrokerExecutionConstraints(min_volume=0.01)

        result = engine.compute_size(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.07500,
            account=account,
            risk_percent=0.5,
            constraints=constraints,
        )

        assert result.is_executable is False
        assert "is below broker minimum volume (0.01 lots)" in (result.rejection_reason or "")
