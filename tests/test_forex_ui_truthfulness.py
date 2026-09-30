from pathlib import Path


def test_forex_prices_use_canonical_formatter_and_no_fabricated_risk_checks():
    source = Path("web/static/app.js").read_text(encoding="utf-8")

    assert "toFixed(5)" not in source
    assert "function formatForexPrice" in source
    assert "function actionBadgeClass" in source
    assert "Risk checks unavailable" in source
    assert "Verified Risk:Reward threshold" not in source
    assert "Account equity protection ceiling respected" not in source


def test_missing_metrics_are_distinct_from_real_zero():
    source = Path("web/static/app.js").read_text(encoding="utf-8")

    assert "function availableMetric" in source
    assert "value == null" in source
    assert "Number(perf.expectancy || 0)" not in source
    assert "Number(perf.profit_factor || 0)" not in source
