"""Deterministic pip and lot-size calculations for Forex instruments.

All functions are pure Python — no LLM calls, no network I/O.  This module
is the single source of truth for:

  - pip size derivation from digit count
  - price-to-pips and pips-to-price conversions
  - pip value in account currency
  - risk-based lot sizing

Why deterministic?  The LLM must never calculate position size, pip distance,
or account risk.  These are unambiguous arithmetic operations and belong in
code, not prose (see engineering rule §7).

Notes on precision
------------------
Forex prices are expressed in the broker's digit precision (typically 5 or 3
for JPY pairs).  All intermediate calculations use Python float (IEEE-754
double) which is exact enough for pip arithmetic.  Where rounding to a broker's
volume step is required, callers supply ``volume_step`` explicitly.
"""

from __future__ import annotations

from tradingagents.forex.domain import ForexPair, get_forex_pair

# ---------------------------------------------------------------------------
# Pip size derivation
# ---------------------------------------------------------------------------


def pip_size_for(symbol: Union[ForexPair, str]) -> float:
    """Return the pip size for *symbol* (ForexPair object or string symbol).

    Falls back to the standard 5-digit convention (0.0001) for unknown pairs
    rather than raising, so callers can continue with a sensible default while
    logging a warning.  The pair catalogue covers all majors, minors, and
    common exotics; unknown symbols should be registered via
    :func:`~tradingagents.forex.domain.register_custom_pair`.
    """
    if isinstance(symbol, ForexPair):
        return symbol.pip_size

    pair = get_forex_pair(symbol)
    if pair is not None:
        return pair.pip_size

    # Heuristic for unknown symbols: JPY pairs → 0.01, all others → 0.0001.
    s = str(symbol).strip().upper()
    # Strip broker suffix and =X
    base6 = s[:6] if len(s) >= 6 else s
    quote = base6[3:6] if len(base6) >= 6 else ""
    return 0.01 if "JPY" in quote else 0.0001



# ---------------------------------------------------------------------------
# Price ↔ pip conversions
# ---------------------------------------------------------------------------


def pips_between(price_a: float, price_b: float, pip_size: float) -> float:
    """Number of pips between two price levels.

    The result is always non-negative; use the signed version for directional
    calculations (see :func:`pips_directional`).

    Parameters
    ----------
    price_a, price_b:
        Absolute price levels in the instrument's quote currency.
    pip_size:
        Obtained via :func:`pip_size_for` or ``ForexPair.pip_size``.

    Raises
    ------
    ValueError
        When ``pip_size`` is zero or negative.
    """
    if pip_size <= 0:
        raise ValueError(f"pip_size must be positive, got {pip_size!r}")
    return abs(price_a - price_b) / pip_size


def pips_directional(from_price: float, to_price: float, pip_size: float) -> float:
    """Signed pip distance: positive when price moved from lower to higher.

    Useful for LONG trades (entry → exit where positive = profit) and for
    MAE/MFE calculations.
    """
    if pip_size <= 0:
        raise ValueError(f"pip_size must be positive, got {pip_size!r}")
    return (to_price - from_price) / pip_size


def pips_to_price(pips: float, pip_size: Union[float, ForexPair, str]) -> float:
    """Convert a pip count to an absolute price distance.

    Can accept either a numeric pip_size (e.g. 0.0001) or a ForexPair / symbol string.

    Example::

        pips_to_price(25, 0.0001)    # → 0.0025
        pips_to_price(25, "EURUSD")  # → 0.0025
        pips_to_price(30, 0.01)      # → 0.30
    """
    if isinstance(pip_size, (ForexPair, str)):
        effective_pip = pip_size_for(pip_size)
    else:
        effective_pip = float(pip_size)

    if effective_pip <= 0:
        raise ValueError(f"pip_size must be positive, got {pip_size!r}")
    return pips * effective_pip


def price_to_pips(price_distance: float, pip_size: Union[float, ForexPair, str]) -> float:
    """Convert an absolute price distance to pips.

    Can accept either a numeric pip_size (e.g. 0.0001) or a ForexPair / symbol string.

    Example::

        price_to_pips(0.0025, 0.0001)    # → 25.0
        price_to_pips(0.0025, "EURUSD")  # → 25.0
        price_to_pips(0.30, 0.01)        # → 30.0
    """
    if isinstance(pip_size, (ForexPair, str)):
        effective_pip = pip_size_for(pip_size)
    else:
        effective_pip = float(pip_size)

    if effective_pip <= 0:
        raise ValueError(f"pip_size must be positive, got {pip_size!r}")
    return abs(price_distance) / effective_pip



# ---------------------------------------------------------------------------
# Pip value in account currency
# ---------------------------------------------------------------------------


def pip_value_in_account_currency(
    pair: ForexPair | str,
    lot_size: float,
    account_currency: str = "USD",
    current_quote_price: float | None = None,
) -> float:
    """Value of one pip move for ``lot_size`` lots in the account currency.

    For USD-quoted pairs (EURUSD, GBPUSD, etc.) the pip value is simply::

        pip_size × contract_size × lot_size

    For USD-base pairs (USDJPY, USDCHF, USDCAD) the result needs dividing by
    the current quote price::

        pip_size × contract_size × lot_size / current_quote

    For cross pairs (GBPJPY, EURJPY) you need the USD/JPY quote to convert.
    In that case, supply ``current_quote_price`` as the price of the pair's
    quote currency vs the account currency (e.g. USDJPY spot for JPY-quoted
    crosses when account is USD).

    When ``current_quote_price`` is None, the function assumes the pair is
    already expressed in account currency (a simplification that works for
    EURUSD/GBPUSD from a USD account).  For full accuracy always supply the
    cross rate.

    Returns
    -------
    float
        Pip value per lot in account currency.  Multiply by lot_size to get
        total pip value for the position.
    """
    if isinstance(pair, str):
        pair_obj = get_forex_pair(pair)
        if pair_obj is None:
            # Fallback: construct minimal pair for pip calculation
            from tradingagents.forex.domain import ForexPair as _FP
            sym = pair.strip().upper()[:6]
            base, quote = sym[:3], sym[3:]
            pair_obj = _FP(symbol=sym, base_currency=base, quote_currency=quote)
    else:
        pair_obj = pair

    ps = pair_obj.pip_size
    cs = pair_obj.contract_size
    acc = account_currency.upper()

    # If the quote currency equals the account currency, direct calculation.
    if pair_obj.quote_currency == acc:
        return ps * cs * lot_size

    # If the base currency equals account currency (e.g. USD base, JPY quote).
    if pair_obj.base_currency == acc and current_quote_price and current_quote_price > 0:
        return (ps * cs * lot_size) / current_quote_price

    # Cross pair or missing rate: use approximation.
    if current_quote_price and current_quote_price > 0:
        # current_quote_price is quote/account rate (e.g. USDJPY for JPY-quoted crosses)
        return (ps * cs * lot_size) / current_quote_price

    # Last resort: assume quote ≈ account currency (accurate for USD-account + USD-quoted).
    return ps * cs * lot_size


# ---------------------------------------------------------------------------
# Risk-based lot sizing
# ---------------------------------------------------------------------------


def lot_size_from_risk(
    account_equity: float,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
    pair: ForexPair | str,
    account_currency: str = "USD",
    current_quote_price: float | None = None,
    volume_step: float = 0.01,
    min_volume: float = 0.01,
    max_volume: float = 100.0,
) -> dict:
    """Compute a risk-normalised lot size.

    This is a deterministic calculation.  The LLM must never call this or
    replicate it in prose — it uses code.

    Parameters
    ----------
    account_equity:
        Current account equity in account currency.
    risk_percent:
        Risk as a percentage of equity, e.g. 1.0 for 1 %.
    entry_price:
        Intended entry price.
    stop_loss_price:
        Stop-loss price level.
    pair:
        :class:`~tradingagents.forex.domain.ForexPair` or symbol string.
    account_currency:
        ISO code for the account deposit currency.
    current_quote_price:
        Live quote of the pair's quote currency vs account currency.
        Required for non-USD-quoted pairs from a USD account; see
        :func:`pip_value_in_account_currency`.
    volume_step:
        Broker's minimum volume increment (e.g. 0.01 lots).
    min_volume:
        Broker's minimum allowed lot size.
    max_volume:
        Broker's maximum allowed lot size.

    Returns
    -------
    dict with keys:
        risk_amount         — absolute risk in account currency
        stop_distance_price — |entry - stop| in price terms
        stop_distance_pips  — |entry - stop| in pips
        pip_value_per_lot   — value of 1 pip × 1 lot in account currency
        raw_lot_size        — unrounded calculated lot size
        lot_size            — broker-normalised lot (volume_step-rounded, clamped)
        actual_risk         — risk amount at normalised lot size
    """
    if account_equity <= 0:
        raise ValueError(f"account_equity must be positive, got {account_equity}")
    if not (0 < risk_percent <= 100):
        raise ValueError(f"risk_percent must be 0 < x ≤ 100, got {risk_percent}")
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("entry_price and stop_loss_price must be positive")
    if entry_price == stop_loss_price:
        raise ValueError("entry_price and stop_loss_price must differ")

    pair_obj = pair if isinstance(pair, ForexPair) else get_forex_pair(pair)
    if pair_obj is None:
        raise ValueError(f"Unknown pair {pair!r}; register it first via register_custom_pair()")

    risk_amount = account_equity * (risk_percent / 100.0)
    stop_distance_price = abs(entry_price - stop_loss_price)
    pip_sz = pair_obj.pip_size
    stop_distance_pips = stop_distance_price / pip_sz

    pip_val = pip_value_in_account_currency(
        pair_obj, 1.0, account_currency, current_quote_price
    )
    if pip_val <= 0:
        raise ValueError(f"pip_value_per_lot must be positive, got {pip_val}")

    # Lot size = risk_amount / (stop_distance_pips × pip_value_per_lot)
    raw_lot = risk_amount / (stop_distance_pips * pip_val)

    # Normalise to broker volume step.
    import math
    if volume_step <= 0:
        volume_step = 0.01
    # Round ratio to 8dp first to eliminate IEEE-754 floating-point dust
    # (e.g. 0.02/0.01 = 1.9999999... → floor = 1, not 2).
    ratio = round(raw_lot / volume_step, 8)
    normalised = math.floor(ratio) * volume_step
    normalised = max(min_volume, min(normalised, max_volume))
    normalised = round(normalised, 10)  # avoid trailing dust

    actual_risk = stop_distance_pips * pip_val * normalised

    return {
        "risk_amount": round(risk_amount, 2),
        "stop_distance_price": round(stop_distance_price, pair_obj.digits),
        "stop_distance_pips": round(stop_distance_pips, 1),
        "pip_value_per_lot": round(pip_val, 4),
        "raw_lot_size": round(raw_lot, 6),
        "lot_size": round(normalised, 2),
        "actual_risk": round(actual_risk, 2),
    }


# ---------------------------------------------------------------------------
# Risk:Reward ratio
# ---------------------------------------------------------------------------


def risk_reward_ratio(
    entry: float,
    stop_loss: float,
    take_profit: float,
) -> float:
    """Return the Risk:Reward ratio as a positive float.

    Validates that entry/SL/TP are directionally consistent but does not
    enforce LONG/SHORT — the caller supplies the correct prices for the intended
    direction.

    Examples::

        risk_reward_ratio(1.1000, 1.0950, 1.1100)  # LONG, 50 pip stop, 100 pip TP → 2.0
        risk_reward_ratio(1.1000, 1.1050, 1.0900)  # SHORT, 50 pip stop, 100 pip TP → 2.0

    Raises
    ------
    ValueError
        When stop distance is zero, or TP is on the wrong side of entry.
    """
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    if risk == 0:
        raise ValueError("Stop loss must differ from entry price")
    if reward == 0:
        raise ValueError("Take profit must differ from entry price")
    # Validate directional consistency
    long_direction = stop_loss < entry
    if long_direction:
        if take_profit <= entry:
            raise ValueError(
                f"For a LONG trade (SL {stop_loss} < entry {entry}), "
                f"TP must be above entry, got {take_profit}"
            )
    else:
        if take_profit >= entry:
            raise ValueError(
                f"For a SHORT trade (SL {stop_loss} > entry {entry}), "
                f"TP must be below entry, got {take_profit}"
            )
    return round(reward / risk, 2)
