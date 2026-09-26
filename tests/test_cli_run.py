"""Single analyses must also work in piped and embedded terminals."""

import datetime

import pytest
from typer.testing import CliRunner

import cli.main as m


@pytest.fixture
def single_run(monkeypatch, tmp_path):
    calls = []

    class Graph:
        def __init__(self, analysts, config):
            calls.append(("setup", analysts, config))

        def propagate(self, ticker, date, asset_type, portfolio=None):
            calls.append(("run", ticker, date, asset_type, portfolio))
            return {"final_trade_decision": "Rating: Hold"}, "Hold"

        def save_reports(self, state, ticker):
            return tmp_path / "complete_report.md"

    monkeypatch.setattr(m, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(m, "get_user_selections", lambda: pytest.fail("run must never prompt"))
    return CliRunner(), calls


def test_run_uses_today_and_never_prompts(single_run):
    runner, calls = single_run
    result = runner.invoke(m.app, ["run", "NVDA", "--analysts", "market", "--checkpoint"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == ["market"]
    assert calls[0][2]["checkpoint_enabled"] is True
    assert calls[1][1:4] == ("NVDA", datetime.date.today().isoformat(), "stock")
    assert "Report:" in result.output


def test_crypto_default_omits_company_fundamentals(single_run):
    runner, calls = single_run
    result = runner.invoke(m.app, ["run", "BTC-USD", "--date", "2026-01-01"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == ["market", "social", "news"]
    assert calls[1][3] == "crypto"


@pytest.mark.parametrize("args", [
    ["--date", "not-a-date"], ["--date", "2999-01-01"],
    ["--analysts", ",,"], ["--analysts", "astrology"], ["--asset-type", "invalid"],
])
def test_invalid_run_never_constructs_graph(single_run, args):
    runner, calls = single_run
    result = runner.invoke(m.app, ["run", "NVDA", *args])
    assert result.exit_code == 1
    assert not calls


def test_provider_budget_failure_is_clear_and_has_no_payload(monkeypatch):
    class BudgetError(Exception):
        status_code = 402

    def fail(*args, **kwargs):
        raise BudgetError("private provider payload")

    monkeypatch.setattr(m, "TradingAgentsGraph", fail)
    result = CliRunner().invoke(m.app, ["run", "NVDA"])
    assert result.exit_code == 1
    assert "HTTP 402" in result.output
    assert "credit" in result.output
    assert "private provider payload" not in result.output
    assert "Traceback" not in result.output


def test_incomplete_run_is_a_failure(monkeypatch):
    from tradingagents.llm_clients.response_validation import IncompleteResponseError

    def fail(*args, **kwargs):
        raise IncompleteResponseError("Output reached max_tokens")

    monkeypatch.setattr(m, "TradingAgentsGraph", fail)
    result = CliRunner().invoke(m.app, ["run", "NVDA"])
    assert result.exit_code == 1
    assert "max_tokens" in result.output


def test_unreadable_final_rating_is_not_success(single_run, monkeypatch):
    runner, _calls = single_run
    monkeypatch.setattr(m.TradingAgentsGraph, "propagate", lambda *a, **k: ({}, "REVIEW"))
    result = runner.invoke(m.app, ["run", "NVDA"])
    assert result.exit_code == 2
    assert "needs review" in result.output


def test_run_accepts_sentiment_analyst_name(single_run):
    runner, calls = single_run
    result = runner.invoke(m.app, ["run", "NVDA", "--analysts", "market,sentiment"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == ["market", "social"]
