from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.agents.schemas_forex import ForexAction, ForexTraderProposal
from tradingagents.graph.forex_graph import create_forex_risk_evaluator
from tradingagents.risk.context import (
    AvailabilityStatus,
    ForexConversionRate,
    ForexMarketContext,
    ForexPortfolioContext,
    ForexRiskContext,
)
from tradingagents.risk.engine import ForexRiskLimits
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    OpenPosition,
)

AS_OF = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def _context(**updates):
    values = {
        "pair": "USDJPY",
        "as_of_utc": AS_OF,
        "market": ForexMarketContext(
            quote_status=AvailabilityStatus.AVAILABLE,
            bid=149.10,
            ask=149.12,
            spread_pips=2.0,
            atr_status=AvailabilityStatus.AVAILABLE,
            atr_pips=42.0,
            source="MT5",
            observed_at=AS_OF,
        ),
        "account": ForexAccountProfile(
            balance=10_000,
            equity=9_800,
            used_margin=800,
            free_margin=9_000,
            currency="USD",
            leverage=100,
        ),
        "broker": BrokerExecutionConstraints(
            broker_symbol="USDJPYm",
            digits=3,
            point=0.001,
            pip_size=0.01,
            contract_size=100_000,
            min_volume=0.01,
            max_volume=50,
            volume_step=0.01,
        ),
        "portfolio": ForexPortfolioContext(
            open_positions=(
                OpenPosition(
                    position_id="7",
                    pair="EURUSD",
                    action=ForexAction.LONG,
                    lots=0.2,
                    entry_price=1.1,
                    stop_loss=1.095,
                    risk_amount=100,
                ),
            )
        ),
        "conversions": (
            ForexConversionRate(
                from_currency="JPY",
                to_currency="USD",
                status=AvailabilityStatus.AVAILABLE,
                rate=0.006705,
                conversion_path=("JPYUSD",),
                source="MT5",
                observed_at=AS_OF,
            ),
        ),
    }
    values.update(updates)
    return ForexRiskContext(**values)


def test_context_validates_and_preserves_live_inputs():
    context = _context()

    assert context.pair == "USDJPY"
    assert context.market.spread_pips == 2.0
    assert context.quote_to_account_rate() == 0.006705
    assert context.sizing_quote_price() == pytest.approx(149.14243)
    assert context.portfolio.open_position_count == 1


def test_unavailable_conversion_is_distinguishable_from_zero_and_not_applicable():
    unavailable = ForexConversionRate(
        from_currency="JPY",
        to_currency="USD",
        status=AvailabilityStatus.UNAVAILABLE,
        conversion_path=("USDJPY", "inverse"),
    )
    context = _context(conversions=(unavailable,))

    assert context.quote_to_account_conversion().status == AvailabilityStatus.UNAVAILABLE
    assert context.quote_to_account_rate() is None
    with pytest.raises(ValidationError):
        ForexConversionRate(
            from_currency="JPY",
            to_currency="USD",
            status=AvailabilityStatus.UNAVAILABLE,
            rate=0.0,
        )


def test_market_availability_distinguishes_unavailable_from_known_zero_spread():
    zero_spread = ForexMarketContext(
        quote_status=AvailabilityStatus.AVAILABLE,
        bid=1.1,
        ask=1.1,
        spread_pips=0.0,
        atr_status=AvailabilityStatus.UNAVAILABLE,
        atr_pips=None,
        source="deterministic-fixture",
        observed_at=AS_OF,
    )

    assert zero_spread.spread_pips == 0.0
    assert zero_spread.atr_status == AvailabilityStatus.UNAVAILABLE
    invalid = zero_spread.model_dump()
    invalid["atr_pips"] = 20.0
    with pytest.raises(ValidationError, match="unavailable.*ATR"):
        ForexMarketContext.model_validate(invalid)


def test_free_margin_is_preserved_instead_of_replaced_by_balance():
    context = _context()

    assert context.account.balance == 10_000
    assert context.account.free_margin == 9_000


def test_broker_constraints_and_positions_survive_serialization():
    restored = ForexRiskContext.model_validate_json(_context().model_dump_json())

    assert restored.broker.broker_symbol == "USDJPYm"
    assert restored.broker.digits == 3
    assert restored.broker.point == 0.001
    assert restored.broker.pip_size == 0.01
    assert restored.broker.contract_size == 100_000
    assert restored.broker.volume_step == 0.01
    assert restored.portfolio.open_positions[0].position_id == "7"
    assert restored.portfolio.open_position_count == 1


def test_utc_timestamps_are_preserved_and_naive_values_rejected():
    restored = ForexRiskContext.model_validate_json(_context().model_dump_json())

    assert restored.as_of_utc == AS_OF
    assert restored.market.observed_at == AS_OF
    assert restored.conversions[0].observed_at == AS_OF
    with pytest.raises(ValidationError, match="UTC offset"):
        _context(as_of_utc=datetime(2026, 9, 30, 10, 0))


def test_mutable_caller_models_and_lists_do_not_mutate_stored_context():
    account = ForexAccountProfile(balance=1000, equity=900, free_margin=700)
    positions = [
        OpenPosition(
            position_id="original",
            pair="EURUSD",
            action=ForexAction.LONG,
            lots=0.1,
            entry_price=1.1,
            stop_loss=1.09,
            risk_amount=100,
        )
    ]
    context = _context(
        account=account,
        portfolio={"open_positions": positions},
    )

    account.free_margin = 1
    positions[0].risk_amount = 999
    positions.append(positions[0])

    assert context.account.free_margin == 700
    assert context.portfolio.open_position_count == 1
    assert context.portfolio.open_positions[0].risk_amount == 100


def test_graph_uses_pair_price_for_account_base_pair_without_external_conversion():
    unavailable = ForexConversionRate(
        from_currency="JPY",
        to_currency="USD",
        status=AvailabilityStatus.UNAVAILABLE,
        conversion_path=("USDJPY", "inverse"),
    )
    holder = {}
    evaluator = create_forex_risk_evaluator(
        risk_context=_context(conversions=(unavailable,)),
        risk_limits=ForexRiskLimits(
            enforce_market_open=False,
            enforce_news_blackout=False,
        ),
        sizing_result_holder=holder,
    )
    proposal = ForexTraderProposal(
        pair="USDJPY",
        action=ForexAction.LONG,
        entry_price=149.10,
        stop_loss=148.70,
        take_profit_1=149.90,
        suggested_risk_percent=1.0,
        reasoning="Deterministic context wiring test",
    )

    result = evaluator(
        {
            "company_of_interest": "USDJPY",
            "trade_date": "2026-09-30",
            "forex_proposal": proposal.model_dump(),
        }
    )

    assert holder["latest"].is_executable is True
    assert holder["latest"].rejection_reason is None
    assert result["forex_risk_decision"]["decision"] == "APPROVE"
