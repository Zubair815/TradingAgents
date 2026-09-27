"""Risk management package for trading agents.

Provides deterministic risk engine and limit verification for institutional
currency trading (Phase 10).
"""

from tradingagents.risk.engine import (
    ForexRiskEngine,
    ForexRiskLimits,
    validate_forex_proposal,
)
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    ForexPositionSizingEngine,
    OpenPosition,
    PositionSizingMethod,
    PositionSizingResult,
    calculate_correlation_exposure_factor,
    calculate_forex_position_size,
    calculate_kelly_risk_percent,
    calculate_portfolio_currency_exposure,
    calculate_required_margin,
    calculate_volatility_adjusted_risk_percent,
    get_pair_correlation,
)

__all__ = [
    # Risk Engine (Phase 10)
    "ForexRiskEngine",
    "ForexRiskLimits",
    "validate_forex_proposal",
    # Position Sizing Engine (Phase 11)
    "PositionSizingMethod",
    "ForexAccountProfile",
    "BrokerExecutionConstraints",
    "OpenPosition",
    "PositionSizingResult",
    "ForexPositionSizingEngine",
    "calculate_required_margin",
    "calculate_volatility_adjusted_risk_percent",
    "calculate_kelly_risk_percent",
    "calculate_portfolio_currency_exposure",
    "calculate_correlation_exposure_factor",
    "calculate_forex_position_size",
    "get_pair_correlation",
]
