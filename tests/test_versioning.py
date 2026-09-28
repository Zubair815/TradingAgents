"""Institutional Verification Suite for Model, Prompt, and Strategy Versioning (Phase 23).

Validates:
1. Proposal retention of experiment configuration:
   - provider, quick_model, deep_model, temperature, reasoning_effort, max_tokens
   - active analysts list
   - prompt version and deterministic prompt hash
   - strategy version and system version
   - data source metadata
2. Strict secret scrubbing:
   - api_key, auth, password, secret, token keys are never stored in proposals or metadata.
3. Persistent journal round-trip:
   - ProposalRecord and ForexTraderProposal bidirectional mapping preserves version fields.
4. Trade linkage and inheritance:
   - TradeJournalRecord automatically inherits version metadata from its source proposal.
5. Multi-dimensional analytics grouping by version:
   - Grouping by model, provider, prompt_version, strategy_version, and system_version.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    sanitize_secrets,
)
from tradingagents.analytics.performance import ForexPerformanceEngine
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord, TradeJournalRecord, TradeStatus

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_sanitize_secrets_scrubs_credentials() -> None:
    """Sanitizer removes API keys, tokens, and passwords from nested structures."""
    dirty = {
        "provider": "openai",
        "api_key": "sk-secret12345",
        "auth_token": "bearer xyz",
        "nested": {
            "password": "supersecret",
            "safe_param": 42,
            "secret_key": "hidden",
        },
        "items": [
            {"token": "bad", "name": "good"},
            "plain_value",
        ],
    }
    clean = sanitize_secrets(dirty)

    assert "api_key" not in clean
    assert "auth_token" not in clean
    assert clean["provider"] == "openai"
    assert clean["nested"] == {"safe_param": 42}
    assert clean["items"][0] == {"name": "good"}
    assert clean["items"][1] == "plain_value"


def test_proposal_retains_version_metadata_and_sanitizes() -> None:
    """ForexTraderProposal retains version fields and scrubs credentials from data_source_metadata."""
    prop = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.1000,
        stop_loss=1.0970,
        take_profit_1=1.1060,
        reasoning="Test pullback setup",
        provider="openai",
        quick_model="gpt-4.1-mini",
        deep_model="gpt-4.1",
        temperature=0.2,
        reasoning_effort="high",
        max_tokens=4000,
        analysts=["forex_technical", "forex_macro"],
        prompt_version="2026.09.v1",
        prompt_hash="sha256_abcdef123456",
        strategy_version="2.1.0",
        system_version="1.5.0",
        data_source_metadata={
            "vendor": "YahooFinance",
            "api_key": "sk-12345",  # Should be scrubbed
            "latency_ms": 120,
        },
    )

    assert prop.provider == "openai"
    assert prop.quick_model == "gpt-4.1-mini"
    assert prop.deep_model == "gpt-4.1"
    assert prop.temperature == 0.2
    assert prop.reasoning_effort == "high"
    assert prop.max_tokens == 4000
    assert prop.analysts == ["forex_technical", "forex_macro"]
    assert prop.prompt_version == "2026.09.v1"
    assert prop.prompt_hash == "sha256_abcdef123456"
    assert prop.strategy_version == "2.1.0"
    assert prop.system_version == "1.5.0"

    # Verify secret scrubbing
    assert "api_key" not in prop.data_source_metadata
    assert prop.data_source_metadata["vendor"] == "YahooFinance"
    assert prop.data_source_metadata["latency_ms"] == 120


def test_proposal_record_bidirectional_conversion() -> None:
    """ProposalRecord preserves version fields when converting to/from domain proposal."""
    prop = ForexTraderProposal(
        pair="GBPUSD",
        action=ForexAction.SHORT,
        order_type=OrderType.MARKET,
        entry_price=1.2800,
        stop_loss=1.2830,
        take_profit_1=1.2720,
        reasoning="Trend continuation breakdown",
        provider="anthropic",
        quick_model="claude-3-5-haiku",
        deep_model="claude-3-5-sonnet",
        prompt_version="v3.0",
        strategy_version="3.0.0",
        data_source_metadata={"source": "MT5_FEED"},
    )

    rec = ProposalRecord.from_forex_trader_proposal(prop)
    assert rec.provider == "anthropic"
    assert rec.quick_model == "claude-3-5-haiku"
    assert rec.deep_model == "claude-3-5-sonnet"
    assert rec.strategy_version == "3.0.0"
    assert rec.data_source_metadata["source"] == "MT5_FEED"

    # Reconstruct domain proposal
    reconstructed = rec.to_forex_trader_proposal()
    assert reconstructed.provider == "anthropic"
    assert reconstructed.quick_model == "claude-3-5-haiku"
    assert reconstructed.strategy_version == "3.0.0"


def test_journal_persists_and_reloads_version_metadata() -> None:
    """In-memory journal round-trips proposal and retrieves version metadata."""
    journal = ForexTradeJournal(db_path=":memory:")
    prop = ForexTraderProposal(
        pair="USDJPY",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=150.00,
        stop_loss=149.50,
        take_profit_1=151.20,
        reasoning="BoJ carry trade continuation",
        provider="google",
        quick_model="gemini-2.5-flash",
        deep_model="gemini-2.5-pro",
        prompt_version="2026.09.boj",
        prompt_hash="hash_jpy_123",
        strategy_version="4.0.0",
        system_version="2.0.0",
        data_source_metadata={"feed": "EODHD"},
    )

    pid = journal.save_proposal(prop)
    loaded = journal.get_proposal(pid)

    assert loaded is not None
    assert loaded.pair == "USDJPY"
    assert loaded.provider == "google"
    assert loaded.quick_model == "gemini-2.5-flash"
    assert loaded.deep_model == "gemini-2.5-pro"
    assert loaded.prompt_version == "2026.09.boj"
    assert loaded.strategy_version == "4.0.0"
    assert loaded.system_version == "2.0.0"
    assert loaded.data_source_metadata.get("feed") == "EODHD"


def test_journal_trade_open_inherits_proposal_version_metadata() -> None:
    """Opening a trade linked to a proposal automatically inherits its version metadata."""
    journal = ForexTradeJournal(db_path=":memory:")
    prop = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.1000,
        stop_loss=1.0970,
        take_profit_1=1.1060,
        reasoning="Test setup",
        provider="openai",
        quick_model="gpt-4.1-mini",
        strategy_version="v2.5",
        prompt_hash="p_hash_999",
    )
    pid = journal.save_proposal(prop)

    trade = journal.record_trade_open(
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.1001,
        stop_loss=1.0970,
        lots=0.1,
        proposal_id=pid,
    )

    assert trade.proposal_id == pid
    assert trade.metadata["provider"] == "openai"
    assert trade.metadata["quick_model"] == "gpt-4.1-mini"
    assert trade.metadata["strategy_version"] == "v2.5"
    assert trade.metadata["prompt_hash"] == "p_hash_999"


def test_analytics_segmentation_groups_by_version() -> None:
    """Performance engine groups metrics across model, provider, prompt, and strategy versions."""
    engine = ForexPerformanceEngine()

    trades = [
        TradeJournalRecord(
            trade_id="t1",
            pair="EURUSD",
            action=ForexAction.LONG,
            status=TradeStatus.CLOSED,
            open_price=1.1000,
            close_price=1.1050,
            stop_loss=1.0970,
            lots=0.1,
            net_profit=50.0,
            metadata={
                "provider": "openai",
                "quick_model": "gpt-4.1-mini",
                "prompt_version": "v1.0",
                "strategy_version": "strat_alpha",
                "system_version": "sys_1.0",
            },
        ),
        TradeJournalRecord(
            trade_id="t2",
            pair="EURUSD",
            action=ForexAction.LONG,
            status=TradeStatus.CLOSED,
            open_price=1.1000,
            close_price=1.0970,
            stop_loss=1.0970,
            lots=0.1,
            net_profit=-30.0,
            metadata={
                "provider": "openai",
                "quick_model": "gpt-4.1-mini",
                "prompt_version": "v1.0",
                "strategy_version": "strat_alpha",
                "system_version": "sys_1.0",
            },
        ),
        TradeJournalRecord(
            trade_id="t3",
            pair="EURUSD",
            action=ForexAction.SHORT,
            status=TradeStatus.CLOSED,
            open_price=1.1000,
            close_price=1.0950,
            stop_loss=1.1030,
            lots=0.1,
            net_profit=50.0,
            metadata={
                "provider": "anthropic",
                "quick_model": "claude-3-5-haiku",
                "prompt_version": "v2.0",
                "strategy_version": "strat_beta",
                "system_version": "sys_2.0",
            },
        ),
    ]

    segmentation = engine.calculate_segmentation(trades)

    # By Model
    assert "gpt-4.1-mini" in segmentation.by_model
    assert "claude-3-5-haiku" in segmentation.by_model
    assert segmentation.by_model["gpt-4.1-mini"].trade_count == 2
    assert segmentation.by_model["claude-3-5-haiku"].trade_count == 1

    # By Provider
    assert "openai" in segmentation.by_provider
    assert "anthropic" in segmentation.by_provider
    assert segmentation.by_provider["openai"].trade_count == 2
    assert segmentation.by_provider["anthropic"].trade_count == 1

    # By Prompt Version
    assert "v1.0" in segmentation.by_prompt_version
    assert "v2.0" in segmentation.by_prompt_version

    # By Strategy Version
    assert "strat_alpha" in segmentation.by_strategy_version
    assert "strat_beta" in segmentation.by_strategy_version
    assert segmentation.by_strategy_version["strat_alpha"].net_profit == pytest.approx(20.0, rel=1e-2)

    # By System Version
    assert "sys_1.0" in segmentation.by_system_version
    assert "sys_2.0" in segmentation.by_system_version
