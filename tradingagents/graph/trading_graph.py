# TradingAgents/graph/trading_graph.py

import json
import logging
import math
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
from langgraph.prebuilt import ToolNode

from tradingagents.agents.analysts.forex_macro import FOREX_MACRO_TOOLS
from tradingagents.agents.analysts.forex_technical import FOREX_TECHNICAL_TOOLS

# Import the abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
    get_stock_data,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.forex_news_tools import FOREX_NEWS_TOOLS
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import build_config, config_scope
from tradingagents.dataflows.utils import get_current_date, safe_ticker_component
from tradingagents.llm_clients import create_llm_client
from tradingagents.reporting import write_report_tree

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor

logger = logging.getLogger(__name__)


def _with_run_config(method):
    """Scope work outside graph nodes (identity, settlement and initial state).

    Tolerates instances created without ``__init__`` (e.g. via
    ``object.__new__``) — if ``self.config`` hasn't been set yet the
    method runs without a config scope, which lets validation helpers
    like ``_validate_trade_date`` work in tests that bypass the
    constructor.
    """
    @wraps(method)
    def scoped(self, *args, **kwargs):
        cfg = getattr(self, "config", None)
        if cfg is not None:
            with config_scope(cfg):
                return method(self, *args, **kwargs)
        return method(self, *args, **kwargs)
    return scoped


def _validate_trade_date(trade_date) -> str:
    """The run date as a canonical ``YYYY-MM-DD`` string no later than today."""
    value = str(trade_date)
    try:
        canonical = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        canonical = False
    if not canonical:
        raise ValueError(f"trade_date must be a date in YYYY-MM-DD format, got {trade_date!r}")
    if value > get_current_date():
        raise ValueError(f"trade_date cannot be in the future: {value}")
    return value


def _coerce_max_retries(value):
    """Validate an ``llm_max_retries`` value to a non-negative int.

    Accepts an int or a numeric string (env vars arrive as strings). Rejects
    booleans and negatives loudly so a misconfiguration fails at startup rather
    than silently disabling retries.
    """
    if isinstance(value, bool):
        raise ValueError(f"llm_max_retries must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"llm_max_retries must be an integer, got {value!r}") from exc
    if n < 0:
        raise ValueError(f"llm_max_retries must be >= 0, got {n}")
    return n


def _coerce_max_tokens(value):
    """Validate a ``max_tokens`` value to a positive int (env vars are strings)."""
    if isinstance(value, bool):
        raise ValueError(f"max_tokens must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"max_tokens must be an integer, got {value!r}") from exc
    if n <= 0:
        raise ValueError(f"max_tokens must be > 0, got {n}")
    return n



def _deep_merge_chunks(chunks: list[dict]) -> dict:
    """Deep-merge streamed graph state chunks so nested dicts survive.

    ``graph.stream(stream_mode="values")`` yields full state snapshots per
    node.  A naive ``final.update(chunk)`` would overwrite nested TypedDicts
    like ``investment_debate_state`` with a later chunk's partial copy, losing
    fields set by earlier nodes.  This merge updates nested dicts one level
    deep (matching LangGraph's state semantics) instead of replacing them.
    """
    merged: dict = {}
    for chunk in chunks:
        for key, value in chunk.items():
            if (
                isinstance(value, dict)
                and isinstance(merged.get(key), dict)
            ):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return merged


deep_merge_chunks = _deep_merge_chunks


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = build_config(config)
        self.callbacks = callbacks or []

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            config=self.config,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict
        self._returns_cache = {}  # cache of historical returns to prevent redundant downloads

        # Graph-shape-affecting run choices, kept for the checkpoint signature.
        self.selected_analysts = tuple(selected_analysts)

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False

    def _get_provider_kwargs(self) -> dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        # Sampling temperature is cross-provider: forward it whenever set.
        # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
        # string ("0.2") works the same as a programmatic float.
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        # SDK retry budget is cross-provider. Forward it only when explicitly set
        # so each provider keeps its own default (usually 2) otherwise (#1091).
        max_retries = self.config.get("llm_max_retries")
        if max_retries is not None and max_retries != "":
            kwargs["max_retries"] = _coerce_max_retries(max_retries)

        # Output-token cap is cross-provider, but Gemini names it
        # ``max_output_tokens``; forward under the right key when set (#1204).
        max_tokens = self.config.get("max_tokens")
        if max_tokens is not None and max_tokens != "":
            key = "max_output_tokens" if provider == "google" else "max_tokens"
            kwargs[key] = _coerce_max_tokens(max_tokens)

        return kwargs

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Deterministic verification snapshot (bound to the analyst
                    # LLM and required by its prompt; must be executable here or
                    # the call fails and the model reports it "unavailable").
                    get_verified_market_snapshot,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    get_macro_indicators,
                    get_prediction_markets,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
            "forex_technical": ToolNode(
                FOREX_TECHNICAL_TOOLS
            ),
            "forex_macro": ToolNode(
                FOREX_MACRO_TOOLS
            ),
            "forex_news": ToolNode(
                FOREX_NEWS_TOOLS
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        explicit = self.config.get("benchmark_ticker")
        if explicit:
            # Same alias mapping as the analyzed ticker; an unmapped alias finds
            # no prices, and the decision would stay pending for good.
            return normalize_symbol(explicit)
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = normalize_symbol(ticker)
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> tuple[float | None, float | None, int | None, str | None]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        holding_days, resolution_date)`` — where ``resolution_date`` is the date
        of the last price bar used, i.e. when the outcome became known (#1251) —
        or ``(None, None, None, None)`` when the outcome cannot be settled yet:
        the full holding window has not traded (#1169), or the symbol is delisted
        or unreachable.
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        norm_ticker = normalize_symbol(ticker)
        cache_key = (norm_ticker, trade_date, holding_days, benchmark)
        if hasattr(self, "_returns_cache") and cache_key in self._returns_cache:
            return self._returns_cache[cache_key]

        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            # holding_days counts trading days, so ask for the calendar span they
            # occupy (about 7 for every 5) plus a week for holidays.
            end = start + timedelta(days=round(holding_days * 7 / 5) + 7)
            end_str = end.strftime("%Y-%m-%d")

            from tradingagents.dataflows.stockstats_utils import yf_retry

            # Normalize so the realized-return lookup hits the same instrument
            # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
            # already a canonical Yahoo symbol from ``_resolve_benchmark``.
            stock = yf_retry(lambda: yf.Ticker(normalize_symbol(ticker)).history(start=trade_date, end=end_str))
            # A crypto/foreign-market entry may fall on a benchmark holiday.
            # Value the benchmark at its last known close at each *asset*
            # endpoint, never at an independently counted future session.
            benchmark_start = (start - timedelta(days=7)).strftime("%Y-%m-%d")
            bench = yf_retry(lambda: yf.Ticker(benchmark).history(start=benchmark_start, end=end_str))

            def daily_close(frame):
                prices = frame["Close"].copy()
                index = pd.DatetimeIndex(prices.index)
                # Yahoo daily bars are local session labels, not UTC instants.
                # UTC conversion would move Tokyo's bars into the prior date.
                prices.index = index.tz_localize(None).normalize()
                if prices.index.has_duplicates or prices.index.hasnans:
                    raise ValueError("duplicate or missing daily price dates")
                return prices.sort_index().loc[:get_current_date()]

            stock_close = daily_close(stock).loc[trade_date:]
            bench_close = daily_close(bench)

            # Require the full holding window in both series. A rerun before it
            # has traded leaves the entry pending to retry next run, rather than
            # settling on a premature partial return (#1169).
            if isinstance(holding_days, bool) or not isinstance(holding_days, int) or holding_days < 1:
                raise ValueError("holding_days must be a positive integer")
            if len(stock_close) <= holding_days or bench_close.empty:
                return None, None, None, None
            entry_date, exit_date = stock_close.index[0], stock_close.index[holding_days]

            def benchmark_at(endpoint):
                known = bench_close.loc[:endpoint]
                if known.empty or (endpoint - known.index[-1]).days > 7:
                    raise ValueError("benchmark close unavailable within seven days of endpoint")
                return float(known.iloc[-1])

            stock_start, stock_end = float(stock_close.iloc[0]), float(stock_close.iloc[holding_days])
            bench_start, bench_end = benchmark_at(entry_date), benchmark_at(exit_date)
            if not all(math.isfinite(p) and p > 0 for p in (stock_start, stock_end, bench_start, bench_end)):
                raise ValueError("return endpoints must have finite positive closing prices")
            raw = stock_end / stock_start - 1
            bench_ret = bench_end / bench_start - 1
            alpha = raw - bench_ret
            if not all(math.isfinite(r) for r in (raw, bench_ret, alpha)):
                raise ValueError("non-finite price return")
            # The date of the last price bar used is when this outcome became
            # known — the point-in-time cutoff for injecting the lesson (#1251).
            resolution_date = exit_date.strftime("%Y-%m-%d")
            result = (raw, alpha, holding_days, resolution_date)
            if hasattr(self, "_returns_cache"):
                self._returns_cache[cache_key] = result
            return result
        except (ValueError, KeyError) as e:
            logger.warning(
                "Could not compute returns for %s on %s vs %s (invalid data, will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None, None
        except Exception as e:
            logger.warning(
                "Network or data error resolving outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days, resolution_date = self._fetch_returns(
                ticker, entry["date"], self.config.get("holding_period_days", 5),
                benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            try:
                reflection = self.reflector.reflect_on_final_decision(
                    final_decision=entry.get("decision", ""),
                    raw_return=raw,
                    alpha_return=alpha,
                    benchmark_name=benchmark,
                    holding_days=days,
                )
            except Exception as exc:
                # Reflection calls a provider, and this runs on the way into a
                # new run: a transient failure leaves the entry pending for the
                # next one rather than stopping the analysis that was asked for.
                logger.warning("Reflection failed for %s on %s: %s", ticker, entry["date"], exc)
                continue
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
                "resolution_date": resolution_date,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock",
                                   curr_date: str | None = None) -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity, curr_date)

    def _memory_as_of(self, trade_date) -> str | None:
        """Point-in-time cutoff for past-context lessons (#1251).

        A historical/backtest run (trade date before today) filters lessons to
        those already resolved by the trade date. A current-date run returns
        None, disabling the filter so live behavior and pre-migration entries
        (which have no stored resolution date) are unaffected.
        """
        td = str(trade_date)
        return td if td < datetime.now().strftime("%Y-%m-%d") else None

    def _run_signature(self, asset_type: str, portfolio=None) -> str:
        """Graph-shape inputs that must invalidate a checkpoint if changed.

        Keyed into the checkpoint thread ID so a resume under a different analyst
        selection, debate/risk depth, or asset mode starts fresh instead of
        silently continuing the previous graph (#1089).
        """
        return "|".join([
            "analysts=" + ",".join(self.selected_analysts),
            f"debate={self.config['max_debate_rounds']}",
            f"risk={self.config['max_risk_discuss_rounds']}",
            f"asset={asset_type}",
            # None, an empty book and a changed book are three different runs.
            f"portfolio={portfolio.fingerprint() if portfolio is not None else 'none'}",
        ])

    @_with_run_config
    def propagate(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        Returns ``(final_state, signal)`` where ``signal`` is one of the 5-tier
        ratings (Buy / Overweight / Hold / Underweight / Sell) or ``"REVIEW"``
        when the decision had no parseable rating (#1170); guard with
        ``tradingagents.agents.utils.rating.is_review`` before mapping it to the
        PortfolioRating enum.
        """
        trade_date = _validate_trade_date(trade_date)
        self.ticker = company_name

        with self.checkpoint_scope(company_name, trade_date, asset_type, portfolio) as thread_id_value:
            return self._run_graph(
                company_name, trade_date, asset_type=asset_type,
                checkpoint_thread_id=thread_id_value, portfolio=portfolio,
            )

    def begin_checkpoint(self, company_name, trade_date, asset_type: str = "stock", portfolio=None) -> str | None:
        """Recompile the graph with a per-ticker checkpointer and return the
        ``thread_id`` to inject into the stream/invoke ``config`` (or ``None``
        when checkpointing is disabled).

        Pair every call with :meth:`end_checkpoint` in a ``finally``. Both
        ``propagate`` (via :meth:`checkpoint_scope`) and the CLI stream path use
        this so ``--checkpoint`` actually resumes (#1249); previously the setup
        lived only inside ``propagate`` and the CLI streamed the checkpointer-less
        graph, making the flag a no-op.
        """
        self._resuming = False
        if not self.config.get("checkpoint_enabled"):
            return None
        signature = self._run_signature(asset_type, portfolio)
        self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], company_name)
        saver = self._checkpointer_ctx.__enter__()
        self.graph = self.workflow.compile(checkpointer=saver)

        step = checkpoint_step(
            self.config["data_cache_dir"], company_name, str(trade_date), signature, saver=saver
        )
        self._resuming = step is not None
        if step is not None:
            logger.info("Resuming from step %d for %s on %s", step, company_name, trade_date)
        else:
            logger.info("Starting fresh for %s on %s", company_name, trade_date)
        return thread_id(company_name, str(trade_date), signature)

    def checkpoint_input(self, init_state):
        """The value to stream/invoke: ``None`` to resume an existing checkpoint,
        else the initial state for a fresh run.

        LangGraph resumes an interrupted thread when invoked with ``None``;
        re-passing the initial state instead appends it through the message
        reducer, duplicating messages in the resumed state (#1249).
        """
        return None if self._resuming else init_state

    def end_checkpoint(self):
        """Restore the plain uncheckpointed graph after a checkpointed run."""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)
            self._checkpointer_ctx = None
            self.graph = self.workflow.compile()
        self._resuming = False

    @contextmanager
    def checkpoint_scope(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Context-manager form of begin/end_checkpoint for the propagate path."""
        try:
            yield self.begin_checkpoint(company_name, trade_date, asset_type, portfolio)
        finally:
            self.end_checkpoint()

    def clear_checkpoint_on_success(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Drop a completed run's checkpoint so a later run starts fresh (#1249)."""
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date),
                self._run_signature(asset_type, portfolio),
            )

    def save_reports(self, final_state, ticker, save_path=None, trade_date: str | None = None) -> Path:
        """Write the markdown report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``.
        """
        resolved_date = trade_date or (final_state.get("trade_date") if isinstance(final_state, dict) else None)
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "reports"
                / f"{safe_ticker_component(ticker)}_{stamp}"
            )
        return write_report_tree(final_state, ticker, save_path, trade_date=resolved_date)

    @_with_run_config
    def create_run_state(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Build a run's initial state; propagate() and the CLI both start here.

        Settles this ticker's pending decisions first, then injects the lessons
        known by the trade date for the Portfolio Manager (#1251) and the
        resolved instrument identity for every agent (#814). An entry point that
        assembled the state itself would skip the decision log.
        """
        self._resolve_pending_entries(company_name)
        return self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=self.memory_log.get_past_context(
                company_name, as_of=self._memory_as_of(trade_date)
            ),
            instrument_context=self.resolve_instrument_context(company_name, asset_type, trade_date),
            portfolio_context=portfolio.render(company_name) if portfolio is not None else "",
        )

    @_with_run_config
    def settle_pending(self, company_name):
        """Settle this ticker's decisions whose holding window has now traded.

        A run settles the ticker's earlier decisions on its way in, so the most
        recent one stays pending until the next run for that ticker. A caller
        that is done analyzing a ticker (a backtest sweep, a scheduled job) calls
        this to settle it now.
        """
        self._resolve_pending_entries(company_name)

    def record_decision(self, company_name, trade_date, final_state):
        """Log a finished run's decision for reflection on the next same-ticker run."""
        decision = final_state.get("final_trade_decision")
        if not decision:
            logger.warning("No final decision for %s on %s; nothing logged", company_name, trade_date)
            return
        self.memory_log.store_decision(
            ticker=company_name, trade_date=trade_date, final_trade_decision=decision
        )

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock",
                   checkpoint_thread_id: str | None = None, portfolio=None):
        """Execute the graph and write the resulting state to disk and memory log.

        Success-only operations (decision recording, checkpoint cleanup) happen
        in the ``else`` clause so a failed graph run preserves its checkpoint
        for retry and never records a partial result as a successful decision.
        """
        init_agent_state = self.create_run_state(company_name, trade_date, asset_type, portfolio)
        args = self.propagator.get_graph_args()

        # Inject the checkpoint thread_id (from checkpoint_scope) so the same
        # ticker+date+graph-shape resumes; a different one starts fresh (#1089).
        if checkpoint_thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_thread_id

        # None resumes an existing checkpoint; init_agent_state starts fresh (#1249).
        graph_input = self.checkpoint_input(init_agent_state)

        try:
            if self.debug:
                trace = []
                last_printed = None
                for chunk in self.graph.stream(graph_input, **args):
                    if chunk["messages"]:
                        msg = chunk["messages"][-1]
                        # Nodes after the trader don't append to messages, so the
                        # same trailing message repeats across chunks. Print it only
                        # when it changes (#1027); the trace/state merge is unchanged.
                        signature = (type(msg).__name__, getattr(msg, "content", None))
                        if signature != last_printed:
                            msg.pretty_print()
                            last_printed = signature
                        trace.append(chunk)
                # Streamed chunks are per-node deltas. Deep-merge them so nested
                # dicts (investment_debate_state, risk_debate_state) are preserved
                # instead of overwritten by a later chunk's partial update.
                final_state = _deep_merge_chunks(trace)
            else:
                final_state = self.graph.invoke(graph_input, **args)
        except Exception:
            # Preserve checkpoint for retry — do NOT clear or record anything.
            logger.error(
                "Graph execution failed for %s on %s; checkpoint preserved for retry",
                company_name, trade_date, exc_info=True,
            )
            raise
        else:
            # Success-only: store, log, record, and clear checkpoint.
            self.curr_state = final_state
            self._log_state(trade_date, final_state)
            self.record_decision(company_name, trade_date, final_state)
            self.clear_checkpoint_on_success(company_name, trade_date, asset_type, portfolio)

        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            # Reports can be in any language and this file is read by a person.
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4, ensure_ascii=False)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
