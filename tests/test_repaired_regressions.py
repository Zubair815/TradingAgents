"""Regression tests for audit-repaired behaviors.

Covers:
- Issue 5: Nested debug/stream state merging (InvestDebateState, RiskDebateState)
- Issue 8: SQLite checkpoint repeated operations and connection safety without Windows locks
- Issue 4: Memory log concurrent access and atomic writes
- Issue 14: Canonical debater and deprecated debator alias behavior
"""

import warnings
from concurrent.futures import ThreadPoolExecutor

from tradingagents.agents.risk_mgmt.aggressive_debater import (
    create_aggressive_debater,
    create_aggressive_debator,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.graph.checkpointer import (
    checkpoint_step,
    clear_checkpoint,
    get_checkpointer,
)
from tradingagents.graph.trading_graph import _deep_merge_chunks


def test_nested_debug_state_merge_preserves_debate_fields():
    """_deep_merge_chunks must preserve nested debate state fields across chunks."""
    chunks = [
        {
            "messages": ["m1"],
            "investment_debate_state": {
                "bull_history": "Bull says growth is high.",
                "bear_history": "",
                "history": "Bull says growth is high.",
                "current_response": "Bull says growth is high.",
                "last_speaker": "bull",
                "count": 1,
            },
        },
        {
            "messages": ["m1", "m2"],
            "investment_debate_state": {
                "bear_history": "Bear counters margin compression.",
                "history": "Bull says growth is high.\nBear counters margin compression.",
                "current_response": "Bear counters margin compression.",
                "last_speaker": "bear",
                "count": 2,
            },
        },
        {
            "messages": ["m1", "m2", "m3"],
            "investment_debate_state": {
                "judge_decision": "Recommendation: Buy with tight stop.",
                "count": 2,
            },
        },
    ]

    merged = _deep_merge_chunks(chunks)
    debate = merged["investment_debate_state"]

    assert debate["bull_history"] == "Bull says growth is high."
    assert debate["bear_history"] == "Bear counters margin compression."
    assert debate["judge_decision"] == "Recommendation: Buy with tight stop."
    assert debate["last_speaker"] == "bear"
    assert debate["count"] == 2
    assert len(merged["messages"]) == 3


def test_nested_debug_state_merge_preserves_risk_debate_fields():
    """_deep_merge_chunks must preserve all 3 debater histories in RiskDebateState."""
    chunks = [
        {
            "risk_debate_state": {
                "aggressive_history": "Aggressive: Max allocation.",
                "latest_speaker": "Aggressive",
                "count": 1,
            }
        },
        {
            "risk_debate_state": {
                "conservative_history": "Conservative: Capital preservation.",
                "latest_speaker": "Conservative",
                "count": 2,
            }
        },
        {
            "risk_debate_state": {
                "neutral_history": "Neutral: Balanced hedge.",
                "latest_speaker": "Neutral",
                "count": 3,
            }
        },
        {
            "risk_debate_state": {
                "judge_decision": "Portfolio Manager: 4% weight.",
                "count": 3,
            }
        },
    ]

    merged = _deep_merge_chunks(chunks)
    risk = merged["risk_debate_state"]

    assert risk["aggressive_history"] == "Aggressive: Max allocation."
    assert risk["conservative_history"] == "Conservative: Capital preservation."
    assert risk["neutral_history"] == "Neutral: Balanced hedge."
    assert risk["judge_decision"] == "Portfolio Manager: 4% weight."
    assert risk["latest_speaker"] == "Neutral"


def test_repeated_checkpoint_operations_no_sqlite_locking(tmp_path):
    """Repeated begin/step/clear operations must not trigger SQLite locking on Windows."""
    data_dir = tmp_path / "cache"
    ticker = "NVDA"
    trade_date = "2026-03-01"
    sig = "analysts=market|debate=1|risk=1|asset=stock|portfolio=none"

    # Perform 10 consecutive checkpoint cycles
    for _ in range(10):
        with get_checkpointer(data_dir, ticker) as saver:
            # Reuse the existing saver with zero nested connection locks
            step = checkpoint_step(data_dir, ticker, trade_date, sig, saver=saver)
            assert step is None


            # Clear checkpoint
            clear_checkpoint(data_dir, ticker, trade_date, sig)


def test_memory_log_concurrent_writes(tmp_path):
    """Multiple threads writing to TradingMemoryLog concurrently must not corrupt the file."""
    log_file = tmp_path / "trading_memory.md"
    mem_log = TradingMemoryLog({"memory_log_path": str(log_file)})

    tickers = [f"TICK{i}" for i in range(20)]

    def write_entry(ticker):
        mem_log.store_decision(ticker, "2026-03-01", f"Rating: Buy\nInvest in {ticker}")
        mem_log.update_with_outcome(
            ticker, "2026-03-01", 0.05, 0.02, 5, f"Reflection for {ticker}", "2026-03-08"
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(write_entry, tickers))

    entries = mem_log.load_entries()
    assert len(entries) == 20
    resolved_tickers = {e["ticker"] for e in entries}
    assert resolved_tickers == set(tickers)
    for e in entries:
        assert not e["pending"]
        assert e["rating"] == "Buy"
        assert e["resolved"] == "2026-03-08"


def test_canonical_debater_no_warning_and_deprecated_alias_emits_warning():
    """create_aggressive_debater runs cleanly; create_aggressive_debator warns."""
    class DummyLLM:
        def invoke(self, prompt):
            from unittest.mock import MagicMock
            return MagicMock(content="Analysis argument")

    llm = DummyLLM()

    # Canonical debater: no deprecation warning
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        _ = create_aggressive_debater(llm)
        dep_warnings = [r for r in records if issubclass(r.category, DeprecationWarning)]
        assert len(dep_warnings) == 0

    # Deprecated debator: issues DeprecationWarning
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        _ = create_aggressive_debator(llm)
        dep_warnings = [r for r in records if issubclass(r.category, DeprecationWarning)]
        assert len(dep_warnings) == 1
        assert "deprecated" in str(dep_warnings[0].message)

