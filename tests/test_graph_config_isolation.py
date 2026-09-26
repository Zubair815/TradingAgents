"""Real compiled graph nodes and tool executors must keep their owner's config."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

import tradingagents.agents.utils.core_stock_tools as stock_tools
import tradingagents.graph.trading_graph as tg
from tradingagents.dataflows.config import config_scope, get_config, set_config

pytestmark = pytest.mark.unit


@pytest.fixture
def graph_pair(monkeypatch, tmp_path):
    seen = []

    class FakeLLM:
        def bind_tools(self, tools):
            def reply(prompt):
                messages = prompt.to_messages()
                cfg = get_config()
                seen.append(("model", cfg))
                if isinstance(messages[-1], ToolMessage):
                    return AIMessage(content=cfg["output_language"])
                return AIMessage(content="", tool_calls=[{
                    "name": "get_stock_data", "id": "prices", "args": {
                        "symbol": "AAPL", "start_date": "2026-01-01", "end_date": "2026-01-05",
                    },
                }])
            return RunnableLambda(reply)

    monkeypatch.setattr(tg, "create_llm_client", lambda **kw: SimpleNamespace(get_llm=FakeLLM))

    def route(*args, **kwargs):
        cfg = get_config()
        seen.append(("tool", cfg))
        return cfg["output_language"]

    monkeypatch.setattr(stock_tools, "route_to_vendor", route)
    set_config({"output_language": "global", "tool_vendors": {"get_stock_data": "global-vendor"}})
    configs = [{
        "results_dir": str(tmp_path / label / "results"),
        "data_cache_dir": str(tmp_path / label / "cache"),
        "memory_log_path": str(tmp_path / label / "memory.md"),
        "output_language": label,
        "tool_vendors": {"get_stock_data": "alpha_vantage"} if label == "French" else {},
    } for label in ("French", "English")]
    graphs = [tg.TradingAgentsGraph(["market"], config=c) for c in configs]
    # Caller dictionaries must not remain aliases to a graph's nested values.
    configs[0]["tool_vendors"]["get_stock_data"] = "changed-after-construction"
    return graphs, seen


def _state(graph):
    return graph.propagator.create_initial_state("AAPL", "2026-01-05")


def _market_stream(graph):
    return graph.graph.stream(_state(graph), stream_mode="values")


def _finish_market(stream):
    try:
        for state in stream:
            if state.get("market_report"):
                return state["market_report"]
    finally:
        stream.close()


def _assert_configs(seen):
    assert {stage for stage, _ in seen} == {"model", "tool"}
    assert {cfg["output_language"] for _, cfg in seen} == {"French", "English"}
    for _, cfg in seen:
        expected = {"get_stock_data": "alpha_vantage"} if cfg["output_language"] == "French" else {}
        assert cfg["tool_vendors"] == expected
        assert cfg["output_language"] in cfg["data_cache_dir"]
    assert get_config()["output_language"] == "global"
    assert get_config()["tool_vendors"] == {"get_stock_data": "global-vendor"}


def test_interleaved_cli_style_streams_use_their_own_config(graph_pair):
    graphs, seen = graph_pair
    streams = [_market_stream(graph) for graph in graphs]
    # Advance both through their tool request before resuming the older graph.
    for stream in streams:
        next(stream)
        next(stream)
    assert [_finish_market(stream) for stream in streams] == ["French", "English"]
    _assert_configs(seen)


def test_concurrent_tool_threads_keep_graph_config(graph_pair):
    graphs, seen = graph_pair
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda graph: _finish_market(_market_stream(graph)), graphs))
    assert reports == ["French", "English"]
    _assert_configs(seen)


def test_async_graph_streams_keep_graph_config(graph_pair):
    graphs, seen = graph_pair

    async def run(graph):
        stream = graph.graph.astream(_state(graph), stream_mode="values")
        try:
            async for state in stream:
                if state.get("market_report"):
                    return state["market_report"]
        finally:
            await stream.aclose()

    async def both():
        return await asyncio.gather(*(run(graph) for graph in graphs))

    assert asyncio.run(both()) == ["French", "English"]
    _assert_configs(seen)


def test_scoped_config_restores_after_nested_failure():
    set_config({"output_language": "global"})
    with config_scope({"output_language": "outer"}):
        with pytest.raises(RuntimeError), config_scope({"output_language": "inner"}):
            assert get_config()["output_language"] == "inner"
            raise RuntimeError("failed node")
        assert get_config()["output_language"] == "outer"
    assert get_config()["output_language"] == "global"
