import pytest
from langchain_core.messages import AIMessage

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.llm_clients.base_client import normalize_content
from tradingagents.llm_clients.usage import (
    RunBudgetExceededError,
    UsageTracker,
    track_usage,
)


def _usage_message(total_tokens: int, response_id: str) -> AIMessage:
    return AIMessage(
        id=response_id,
        content="ok",
        usage_metadata={
            "input_tokens": total_tokens - 1,
            "output_tokens": 1,
            "total_tokens": total_tokens,
        },
    )


def test_usage_tracker_fails_closed_after_token_budget_is_crossed():
    tracker = UsageTracker("run-budget", max_total_tokens=10)
    with track_usage(tracker):
        normalize_content(_usage_message(6, "first"))
        with pytest.raises(RunBudgetExceededError, match="token budget exceeded"):
            normalize_content(_usage_message(5, "second"))

    assert tracker.summary()["total_tokens"] == 11


def test_usage_tracker_fails_closed_after_call_budget_is_crossed():
    tracker = UsageTracker("run-calls", max_calls=1)
    with track_usage(tracker):
        normalize_content(_usage_message(2, "first"))
        with pytest.raises(RunBudgetExceededError, match="call budget exceeded"):
            normalize_content(_usage_message(2, "second"))

    assert tracker.summary()["call_count"] == 2


def test_usage_tracker_records_provider_metadata_without_content():
    tracker = UsageTracker("run-1", provider="openai", model="test-model")
    message = AIMessage(
        content="private response text",
        usage_metadata={
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_token_details": {"cache_read": 3},
            "output_token_details": {"reasoning": 2},
        },
    )

    with track_usage(tracker):
        normalize_content(message)
        normalize_content(message)

    assert tracker.summary() == {
        "status": "available",
        "source": "provider_metadata",
        "call_count": 1,
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "cached_tokens": 3,
        "reasoning_tokens": 2,
    }
    assert "private response text" not in str(tracker.records())


def test_usage_tracker_reports_unavailable_instead_of_estimating():
    tracker = UsageTracker("run-2")
    with track_usage(tracker):
        normalize_content(AIMessage(content="no usage metadata"))
    assert tracker.summary()["status"] == "unavailable"
    assert tracker.summary()["total_tokens"] == 0


def test_call_budget_applies_when_provider_omits_usage_metadata():
    tracker = UsageTracker("run-no-metadata", max_calls=1)
    with track_usage(tracker):
        normalize_content(AIMessage(id="first", content="no usage metadata"))
        with pytest.raises(RunBudgetExceededError, match="call budget exceeded"):
            normalize_content(AIMessage(id="second", content="still no metadata"))

    assert tracker.summary()["status"] == "unavailable"


def test_usage_tracker_attributes_each_response_to_its_reported_model():
    tracker = UsageTracker("run-mixed", provider="openai", model="quick-model")
    with track_usage(tracker):
        for model in ("gpt-6-luna", "gpt-6.1-sol"):
            normalize_content(
                AIMessage(
                    content="ok",
                    response_metadata={
                        "model_provider": "openai",
                        "model_name": model,
                    },
                    usage_metadata={
                        "input_tokens": 5,
                        "output_tokens": 2,
                        "total_tokens": 7,
                    },
                )
            )

    records = tracker.records()
    assert [record["model"] for record in records] == ["gpt-6-luna", "gpt-6.1-sol"]
    assert {record["provider"] for record in records} == {"openai"}


def test_usage_records_persist_and_aggregate(tmp_path):
    journal = ForexTradeJournal(tmp_path / "journal.db")
    tracker = UsageTracker("run-3", provider="google", model="gemini-test")
    with track_usage(tracker):
        normalize_content(
            AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )
        )

    assert journal.save_llm_usage(tracker.records()) == 1
    assert journal.save_llm_usage(tracker.records()) == 0
    summary = journal.get_llm_usage_summary("run-3")
    assert summary["status"] == "available"
    assert summary["call_count"] == 1
    assert summary["total_tokens"] == 7
    journal.close()
