from tradingagents.agents.schemas_forex import ForexAction, ForexTraderProposal
from tradingagents.risk.engine import ForexRiskEngine, ForexRiskLimits
from tradingagents.risk.sizing import (
    ForexAccountProfile,
    ForexPositionSizingEngine,
    OpenPosition,
    calculate_currency_risk_exposure,
)


def position(pair: str, action: ForexAction, risk: float | None, index: int = 1) -> OpenPosition:
    return OpenPosition(
        position_id=f"p-{index}",
        pair=pair,
        action=action,
        lots=0.1,
        entry_price=1.1,
        stop_loss=None if risk is None else 1.09,
        risk_amount=risk,
    )


def size(*, account: ForexAccountProfile, positions=(), action=ForexAction.LONG, **kwargs):
    return ForexPositionSizingEngine().compute_size(
        pair="EURUSD",
        action=action,
        entry_price=1.1000,
        stop_loss=1.0990,
        account=account,
        open_positions=positions,
        risk_percent=1.0,
        **kwargs,
    )


def test_currency_risk_attribution_for_three_usd_shorts_and_opposing_trade():
    positions = [
        position("EURUSD", ForexAction.LONG, 100, 1),
        position("GBPUSD", ForexAction.LONG, 100, 2),
        position("AUDUSD", ForexAction.LONG, 100, 3),
    ]
    exposure = calculate_currency_risk_exposure(positions)
    assert exposure == {"EUR": 100.0, "USD": -300.0, "GBP": 100.0, "AUD": 100.0}

    positions.append(position("GBPUSD", ForexAction.SHORT, 100, 4))
    offset = calculate_currency_risk_exposure(positions)
    assert offset["GBP"] == 0.0
    assert offset["USD"] == -200.0


def test_currency_concentration_is_percentage_of_equity_not_lots():
    account = ForexAccountProfile(
        balance=10_000,
        equity=10_000,
        max_currency_exposure_percent=2.5,
        max_account_risk_percent=10,
    )
    result = size(
        account=account,
        positions=[
            position("GBPUSD", ForexAction.LONG, 100, 1),
            position("AUDUSD", ForexAction.LONG, 100, 2),
        ],
    )
    assert result.is_executable is False
    assert "currency stop-risk concentration" in (result.rejection_reason or "")
    # Existing correlated GBPUSD exposure scales proposed EURUSD risk once.
    assert "USD 2.67%" in (result.rejection_reason or "")


def test_opposite_exposures_net_without_disabling_cumulative_risk():
    account = ForexAccountProfile(
        balance=10_000,
        equity=10_000,
        max_currency_exposure_percent=3.0,
        max_account_risk_percent=10,
    )
    result = size(
        account=account,
        positions=[
            position("GBPUSD", ForexAction.LONG, 200, 1),
            position("AUDUSD", ForexAction.SHORT, 200, 2),
        ],
    )
    assert result.is_executable is True


def test_cumulative_open_risk_rejects_and_unknown_risk_fails_conservatively():
    account = ForexAccountProfile(
        balance=10_000,
        equity=10_000,
        max_account_risk_percent=6.0,
        max_currency_exposure_percent=25.0,
    )
    cumulative = size(account=account, positions=[position("GBPUSD", ForexAction.LONG, 550)])
    assert cumulative.is_executable is False
    assert "Cumulative portfolio risk" in (cumulative.rejection_reason or "")

    unknown = size(account=account, positions=[position("GBPUSD", ForexAction.LONG, None)])
    assert unknown.is_executable is False
    assert "unbounded" in (unknown.rejection_reason or "")


def test_maximum_open_positions_excludes_pending_orders_by_contract():
    account = ForexAccountProfile(balance=10_000, equity=10_000, max_open_positions=2)
    result = size(
        account=account,
        positions=[
            position("GBPUSD", ForexAction.LONG, 100, 1),
            position("AUDUSD", ForexAction.SHORT, 100, 2),
        ],
    )
    assert result.is_executable is False
    assert "Maximum open positions reached (2/2)" in (result.rejection_reason or "")
    assert "pending orders are excluded" in (result.rejection_reason or "")

    proposal = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_price=1.1,
        stop_loss=1.099,
        take_profit_1=1.102,
        reasoning="maximum position regression",
    )
    decision = ForexRiskEngine().validate_proposal(
        proposal,
        limits=ForexRiskLimits(
            enforce_market_open=False,
            enforce_news_blackout=False,
            max_open_positions=2,
        ),
        open_position_count=2,
    )
    assert decision.decision.value == "REJECT"
    assert any("Maximum open positions reached" in item for item in decision.risk_violations)


def test_daily_loss_already_exceeded_and_near_limit():
    proposal = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_price=1.1,
        stop_loss=1.099,
        take_profit_1=1.102,
        reasoning="daily loss regression",
    )
    decision = ForexRiskEngine().validate_proposal(
        proposal,
        account_balance=10_000,
        limits=ForexRiskLimits(
            enforce_market_open=False,
            enforce_news_blackout=False,
            max_daily_loss_percent=2.0,
        ),
        daily_realized_pnl=-250,
        day_start_balance=10_000,
        daily_pnl_available=True,
    )
    assert decision.decision.value == "REJECT"
    assert any("Daily realized loss" in item for item in decision.risk_violations)

    account = ForexAccountProfile(
        balance=9_850,
        equity=9_850,
        max_daily_loss_percent=2.0,
        max_currency_exposure_percent=25.0,
    )
    near = size(
        account=account,
        daily_realized_pnl=-150,
        day_start_balance=10_000,
        daily_pnl_available=True,
    )
    assert near.is_executable is False
    assert "Daily loss plus proposed risk" in (near.rejection_reason or "")


def test_enabled_daily_rule_fails_when_pnl_unavailable():
    account = ForexAccountProfile(
        balance=10_000,
        equity=10_000,
        max_daily_loss_amount=500,
    )
    result = size(account=account)
    assert result.is_executable is False
    assert "Daily realized P&L is unavailable" in (result.rejection_reason or "")


def test_no_trade_remains_zero_risk_even_when_limits_are_full():
    account = ForexAccountProfile(
        balance=10_000,
        equity=10_000,
        max_open_positions=1,
        max_daily_loss_amount=100,
    )
    result = size(
        account=account,
        positions=[position("GBPUSD", ForexAction.LONG, None)],
        action=ForexAction.NO_TRADE,
        daily_realized_pnl=-500,
        day_start_balance=10_000,
        daily_pnl_available=True,
    )
    assert result.is_executable is True
    assert result.recommended_lot_size == 0.0
