from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from tradingagents.backtest.ablation import AblationConfigVariant, ForexAblationRunner
from tradingagents.backtest.walk_forward import ForexWalkForwardValidator
from tradingagents.dataflows.forex_data import ForexBar
from web.server import app


def _bars(pair: str = "USDJPY", count: int = 80):
    start = datetime(2025, 1, 6, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        opened = start + timedelta(hours=index)
        price = 150.0 + index * 0.01
        bars.append(ForexBar(
            timestamp=opened,
            open=price,
            high=price + 0.02,
            low=price - 0.02,
            close=price + 0.01,
            volume=100,
            close_time=opened + timedelta(hours=1),
            is_closed=True,
            data_source="MT5",
        ))
    provenance = {
        "source": "MT5",
        "canonical_symbol": pair,
        "symbol": pair,
        "timeframe": "H1",
        "retrieved_at_utc": datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat(),
        "execution_market": True,
    }
    return bars, provenance


def test_walkforward_propagates_usdjpy_and_executes_every_split():
    bars, _ = _bars()
    seen: list[tuple[str, datetime, datetime]] = []

    def pipeline(pair, cutoff, history):
        seen.append((pair, cutoff, history[-1].timestamp))
        return None

    report = ForexWalkForwardValidator(
        config_snapshot={"sampling_interval": 5, "max_analysis_points": 2}
    ).validate(
        bars,
        pair="USDJPY",
        timeframe="H1",
        agent_pipeline_callable=pipeline,
        n_splits=3,
        include_forward_demo=False,
    )

    assert report.pair == "USDJPY"
    assert len(report.splits) == 3
    assert len([key for key in report.period_reports if key.endswith(":OUT_OF_SAMPLE")]) == 3
    assert seen and {item[0] for item in seen} == {"USDJPY"}
    assert all(last_seen <= cutoff for _, cutoff, last_seen in seen)


def test_walkforward_endpoint_injects_a_non_none_pipeline():
    bars, provenance = _bars("GBPUSD", 20)
    captured = {}

    class Report:
        validation_id = "wf_test"

        @staticmethod
        def to_dict():
            return {
                "validation_id": "wf_test",
                "validation_status": "PARTIALLY_VALIDATED",
                "validation_reasons": [],
                "markdown_summary": "ok",
                "splits": [],
            }

    def fake_validate(self, **kwargs):
        captured.update(kwargs)
        return Report()

    client = TestClient(app)
    client.get("/")
    with (
        patch("tradingagents.backtest.historical_data.load_historical_candles", return_value=(bars, provenance)),
        patch.object(ForexWalkForwardValidator, "validate", fake_validate),
    ):
        response = client.post("/api/forex/backtest/walkforward", json={
            "pair": "GBPUSD",
            "timeframe": "H1",
            "date_from": "2025-01-06",
            "date_to": "2025-01-10",
        })

    assert response.status_code == 200
    assert captured["pair"] == "GBPUSD"
    assert callable(captured["agent_pipeline_callable"])


def test_historical_ablation_uses_identical_candles_for_every_variant():
    bars, provenance = _bars(count=12)
    observed = {}
    variants = [
        AblationConfigVariant("a", "A", "A", max_analysis_points=3),
        AblationConfigVariant("b", "B", "B", max_analysis_points=3),
    ]

    def factory(variant):
        def pipeline(pair, cutoff, history):
            observed.setdefault(variant.variant_id, []).append(
                (pair, cutoff, tuple(bar.timestamp for bar in history))
            )
            return None

        return pipeline

    report = ForexAblationRunner(pair="USDJPY", timeframe="H1").run_ablation(
        bars,
        variants=variants,
        pipeline_factory=factory,
        market_data_provenance=provenance,
        historical=True,
    )

    assert report.data_source == "MT5"
    assert observed["a"] == observed["b"]
    assert all(metric.trade_count == 0 for metric in report.variants)
    assert report.winner_declared is False
