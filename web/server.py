"""TradingAgents Web Dashboard — FastAPI backend.

Provides REST endpoints and Server-Sent Events (SSE) for the browser UI
to launch analyses, track live agent progress, and retrieve reports.
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so tradingagents imports resolve.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.dataflows.utils import safe_ticker_component  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

logger = logging.getLogger("tradingagents.web")

# ---------------------------------------------------------------------------
# Authentication & session management
# ---------------------------------------------------------------------------
DASHBOARD_API_KEY = os.environ.get("TRADINGAGENTS_DASHBOARD_API_KEY") or os.environ.get("DASHBOARD_API_KEY")
_SESSION_TOKEN = secrets.token_hex(24)


def verify_auth(request: Request) -> bool:
    """Verify that the request is authorized to launch analyses.

    If TRADINGAGENTS_DASHBOARD_API_KEY is configured in the environment,
    require an explicit matching API key header (X-API-Key or Authorization Bearer).
    Otherwise, require the session token (issued via SameSite cookie or X-Session-Token).
    """
    api_key_header = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    bearer_token = None
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()

    provided_token = api_key_header or bearer_token

    if DASHBOARD_API_KEY:
        if provided_token and secrets.compare_digest(provided_token, DASHBOARD_API_KEY):
            return True
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: invalid or missing API key (configure TRADINGAGENTS_DASHBOARD_API_KEY)",
        )

    # When no explicit DASHBOARD_API_KEY is configured, require session token authentication
    session_cookie = request.cookies.get("tradingagents_session")
    session_header = request.headers.get("X-Session-Token") or provided_token

    if session_cookie and secrets.compare_digest(session_cookie, _SESSION_TOKEN):
        return True
    if session_header and secrets.compare_digest(session_header, _SESSION_TOKEN):
        return True

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: missing session authentication. Access dashboard in browser or provide API key.",
    )


# ---------------------------------------------------------------------------
# In-memory run store (sufficient for a single-user local dashboard)
# ---------------------------------------------------------------------------
_runs: dict[str, dict[str, Any]] = {}         # run_id -> run metadata
_run_events: dict[str, list[dict]] = {}        # run_id -> ordered event list
_completed_reports: dict[str, dict] = {}       # run_id -> final_state snapshot


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class AnalysisRequest(BaseModel):
    ticker: str = Field(default="NVDA", description="Ticker symbol")
    date: str | None = Field(default=None, description="Analysis date YYYY-MM-DD")
    analysts: list[str] = Field(
        default=["market", "social", "news", "fundamentals"],
        description="Analysts to include",
    )
    provider: str | None = Field(default=None)
    quick_model: str | None = Field(default=None)
    deep_model: str | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    temperature: float | None = Field(default=None)

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not v:
            raise ValueError("Ticker symbol cannot be empty")
        try:
            return safe_ticker_component(v)
        except ValueError as exc:
            raise ValueError(f"Invalid ticker: {exc}") from exc


class RunSummary(BaseModel):
    run_id: str
    ticker: str
    date: str
    status: str
    provider: str
    quick_model: str
    deep_model: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    signal: str | None = None


# ---------------------------------------------------------------------------
# Agent progress callback (captures node transitions for SSE)
# ---------------------------------------------------------------------------
# Map internal node names to user-friendly labels + ordering
_NODE_LABELS = {
    "market_analyst": ("Market Analyst", 1),
    "market_analyst_tools": ("Market Analyst — fetching data", 1),
    "social_media_analyst": ("Sentiment Analyst", 2),
    "social_media_analyst_tools": ("Sentiment Analyst — fetching data", 2),
    "news_analyst": ("News Analyst", 3),
    "news_analyst_tools": ("News Analyst — fetching data", 3),
    "fundamentals_analyst": ("Fundamentals Analyst", 4),
    "fundamentals_analyst_tools": ("Fundamentals Analyst — fetching data", 4),
    "bull_researcher": ("Bull Researcher", 5),
    "bull_researcher_tools": ("Bull Researcher — fetching data", 5),
    "bear_researcher": ("Bear Researcher", 6),
    "bear_researcher_tools": ("Bear Researcher — fetching data", 6),
    "research_manager": ("Research Manager", 7),
    "trader": ("Trader", 8),
    "aggressive_debater": ("Aggressive Risk Debater", 9),
    "conservative_debater": ("Conservative Risk Debater", 10),
    "neutral_debater": ("Neutral Risk Debater", 11),
    "risk_manager": ("Risk Manager", 12),
    "portfolio_manager": ("Portfolio Manager", 13),
}

_TOTAL_NODES = 13  # unique pipeline stages


def _emit(run_id: str, event_type: str, data: dict):
    """Append an SSE-ready event to the run's event stream."""
    evt = {"type": event_type, "data": data, "ts": time.time()}
    _run_events.setdefault(run_id, []).append(evt)


# ---------------------------------------------------------------------------
# Background analysis runner
# ---------------------------------------------------------------------------
def _run_analysis(run_id: str, req: AnalysisRequest):
    """Execute analysis in a background thread, emitting progress events."""
    try:
        trade_date = req.date or datetime.now().strftime("%Y-%m-%d")
        _runs[run_id]["date"] = trade_date

        # Build config overrides
        config: dict[str, Any] = {}
        if req.provider:
            config["llm_provider"] = req.provider
        if req.quick_model:
            config["quick_think_llm"] = req.quick_model
        if req.deep_model:
            config["deep_think_llm"] = req.deep_model
        if req.max_tokens:
            config["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            config["temperature"] = req.temperature

        # Resolve provider info for display
        provider = config.get("llm_provider", DEFAULT_CONFIG.get("llm_provider", "openai"))
        quick = config.get("quick_think_llm", DEFAULT_CONFIG.get("quick_think_llm", ""))
        deep = config.get("deep_think_llm", DEFAULT_CONFIG.get("deep_think_llm", ""))
        _runs[run_id].update(provider=provider, quick_model=quick, deep_model=deep)

        _emit(run_id, "status", {"status": "initializing", "message": "Building agent graph..."})

        graph = TradingAgentsGraph(
            selected_analysts=tuple(req.analysts),
            debug=True,
            config=config,
        )

        _emit(run_id, "status", {
            "status": "running",
            "message": f"Analyzing {req.ticker} on {trade_date}",
            "provider": provider,
            "quick_model": quick,
            "deep_model": deep,
        })

        # Run the graph with streaming so we can capture node-level progress
        graph.ticker = req.ticker

        with graph.checkpoint_scope(req.ticker, trade_date) as tid:
            init_state = graph.create_run_state(req.ticker, trade_date)
            graph_input = graph.checkpoint_input(init_state)
            args = graph.propagator.get_graph_args()
            if tid is not None:
                args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

            trace = []
            node_count = 0
            seen_nodes = set()

            first_analyst = req.analysts[0] if req.analysts else "market"
            analyst_key_map = {
                "market": "market_analyst",
                "social": "social_media_analyst",
                "news": "news_analyst",
                "fundamentals": "fundamentals_analyst",
            }
            first_node = analyst_key_map.get(first_analyst, "market_analyst")
            seen_nodes.add(first_node)
            label_info = _NODE_LABELS.get(first_node, (first_node, 1))
            node_count = 1
            progress = round(node_count / _TOTAL_NODES, 2)
            _emit(run_id, "node", {
                "node": first_node,
                "label": label_info[0],
                "order": label_info[1],
                "progress": progress,
            })

            for chunk in graph.graph.stream(graph_input, **args):
                trace.append(chunk)

                # Identify nodes from state changes across analyst reports, debates, and decisions
                detected_nodes = []

                if chunk.get("market_report"):
                    if "market_analyst" not in seen_nodes:
                        detected_nodes.append("market_analyst")
                    if "social" in req.analysts and "social_media_analyst" not in seen_nodes:
                        detected_nodes.append("social_media_analyst")

                if chunk.get("sentiment_report"):
                    if "social_media_analyst" not in seen_nodes:
                        detected_nodes.append("social_media_analyst")
                    if "news" in req.analysts and "news_analyst" not in seen_nodes:
                        detected_nodes.append("news_analyst")

                if chunk.get("news_report"):
                    if "news_analyst" not in seen_nodes:
                        detected_nodes.append("news_analyst")
                    if "fundamentals" in req.analysts and "fundamentals_analyst" not in seen_nodes:
                        detected_nodes.append("fundamentals_analyst")

                if chunk.get("fundamentals_report") and "fundamentals_analyst" not in seen_nodes:
                    detected_nodes.append("fundamentals_analyst")

                inv_state = chunk.get("investment_debate_state") or {}
                inv_speaker = (inv_state.get("last_speaker") or "").lower()
                if inv_speaker == "bull" or inv_state.get("bull_history"):
                    detected_nodes.append("bull_researcher")
                if inv_speaker == "bear" or inv_state.get("bear_history"):
                    detected_nodes.append("bear_researcher")
                if inv_state.get("judge_decision"):
                    detected_nodes.append("research_manager")

                if chunk.get("trader_investment_plan"):
                    detected_nodes.append("trader")

                risk_state = chunk.get("risk_debate_state") or {}
                risk_speaker = (risk_state.get("latest_speaker") or "").lower()
                if "aggressive" in risk_speaker or risk_state.get("aggressive_history"):
                    detected_nodes.append("aggressive_debater")
                if "conservative" in risk_speaker or risk_state.get("conservative_history"):
                    detected_nodes.append("conservative_debater")
                if "neutral" in risk_speaker or risk_state.get("neutral_history"):
                    detected_nodes.append("neutral_debater")
                if risk_state.get("judge_decision"):
                    detected_nodes.append("risk_manager")

                if chunk.get("final_trade_decision"):
                    detected_nodes.append("portfolio_manager")

                for node_hint in detected_nodes:
                    if node_hint and node_hint not in seen_nodes:
                        seen_nodes.add(node_hint)
                        label_info = _NODE_LABELS.get(node_hint, (node_hint, node_count + 1))
                        node_count += 1
                        progress = min(node_count / _TOTAL_NODES, 0.99)

                        _emit(run_id, "node", {
                            "node": node_hint,
                            "label": label_info[0],
                            "order": label_info[1],
                            "progress": round(progress, 2),
                        })

            # Merge streamed chunks
            from tradingagents.graph.trading_graph import _deep_merge_chunks
            final_state = _deep_merge_chunks(trace)

            # Record decision & cleanup
            graph.curr_state = final_state
            graph._log_state(trade_date, final_state)
            graph.record_decision(req.ticker, trade_date, final_state)
            graph.clear_checkpoint_on_success(req.ticker, trade_date)

        # Process signal
        signal = graph.process_signal(final_state.get("final_trade_decision", ""))

        # Save reports
        report_path = graph.save_reports(final_state, req.ticker)

        # Store the state for retrieval
        report_data = {
            "ticker": req.ticker,
            "date": trade_date,
            "signal": signal,
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_debate": final_state.get("investment_debate_state", {}),
            "risk_debate": final_state.get("risk_debate_state", {}),
            "trader_plan": final_state.get("trader_investment_plan", ""),
            "investment_plan": final_state.get("investment_plan", ""),
            "final_decision": final_state.get("final_trade_decision", ""),
            "report_path": str(report_path),
        }
        _completed_reports[run_id] = report_data

        _runs[run_id]["status"] = "completed"
        _runs[run_id]["signal"] = signal
        _runs[run_id]["finished_at"] = datetime.now().isoformat()
        _emit(run_id, "complete", {
            "signal": signal,
            "message": f"Analysis complete: {signal}",
        })

    except Exception as exc:
        error_msg = str(exc)
        # Sanitize: never leak API keys from error messages
        if "openrouter.ai/workspaces" in error_msg:
            error_msg = "OpenRouter credit limit exceeded. Add credits and retry."
        elif "402" in error_msg:
            error_msg = "Provider returned 402 — credit/billing limit reached."

        _runs[run_id]["status"] = "failed"
        _runs[run_id]["error"] = error_msg
        _runs[run_id]["finished_at"] = datetime.now().isoformat()
        _emit(run_id, "error", {"error": error_msg})
        logger.error("Analysis failed for run %s: %s", run_id, error_msg, exc_info=True)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TradingAgents Web Dashboard starting")
    yield
    logger.info("TradingAgents Web Dashboard shutting down")


app = FastAPI(
    title="TradingAgents Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

DEFAULT_CORS_ORIGINS = [
    "http://localhost:8050",
    "http://127.0.0.1:8050",
]
raw_origins = os.environ.get("TRADINGAGENTS_CORS_ORIGINS", "")
if raw_origins.strip():
    cors_origins = [orig.strip() for orig in raw_origins.split(",") if orig.strip()]
else:
    cors_origins = DEFAULT_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(response: Response):
    """Serve the main dashboard page and establish session token."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="Frontend not built")
    response.set_cookie(
        key="tradingagents_session",
        value=_SESSION_TOKEN,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return index_path.read_text(encoding="utf-8")


@app.get("/api/config")
async def get_config(response: Response):
    """Return safe config (no secrets) for the frontend."""
    response.set_cookie(
        key="tradingagents_session",
        value=_SESSION_TOKEN,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {
        "auth_required": bool(DASHBOARD_API_KEY),
        "session_token": _SESSION_TOKEN if not DASHBOARD_API_KEY else None,
        "provider": DEFAULT_CONFIG.get("llm_provider", "openai"),
        "quick_model": DEFAULT_CONFIG.get("quick_think_llm", ""),
        "deep_model": DEFAULT_CONFIG.get("deep_think_llm", ""),
        "max_tokens": DEFAULT_CONFIG.get("max_tokens"),
        "temperature": DEFAULT_CONFIG.get("temperature"),
        "analysts": ["market", "social", "news", "fundamentals"],
        "providers": [
            {"id": "openai", "name": "OpenAI", "models": ["gpt-4o", "gpt-4o-mini", "o3-mini", "gpt-5.6", "gpt-5.6-luna"]},
            {"id": "openrouter", "name": "OpenRouter", "models": ["deepseek/deepseek-chat-v3-0324", "anthropic/claude-3.5-sonnet", "google/gemini-2.5-flash", "openai/gpt-4o"]},
            {"id": "google", "name": "Google", "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]},
            {"id": "anthropic", "name": "Anthropic", "models": ["claude-sonnet-4-5", "claude-sonnet-4-6"]},
            {"id": "deepseek", "name": "DeepSeek", "models": ["deepseek-chat", "deepseek-reasoner"]},
            {"id": "ollama", "name": "Ollama (Local)", "models": ["llama3", "mistral", "codestral"]},
        ],
    }


@app.post("/api/analyze")
async def start_analysis(req: AnalysisRequest, request: Request):
    """Launch a new analysis run in a background thread."""
    verify_auth(request)

    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {
        "run_id": run_id,
        "ticker": req.ticker.upper(),
        "date": req.date or datetime.now().strftime("%Y-%m-%d"),
        "status": "queued",
        "provider": req.provider or DEFAULT_CONFIG.get("llm_provider", "openai"),
        "quick_model": req.quick_model or DEFAULT_CONFIG.get("quick_think_llm", ""),
        "deep_model": req.deep_model or DEFAULT_CONFIG.get("deep_think_llm", ""),
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "error": None,
        "signal": None,
    }
    _run_events[run_id] = []

    thread = threading.Thread(target=_run_analysis, args=(run_id, req), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs")
async def list_runs():
    """List all runs (most recent first)."""
    runs = sorted(_runs.values(), key=lambda r: r["started_at"], reverse=True)
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Get run details."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return _runs[run_id]


def _load_on_disk_report(run_id: str) -> dict | None:
    results_dir = Path(DEFAULT_CONFIG.get("results_dir", ""))
    reports_dir = results_dir / "reports"
    if not reports_dir.exists():
        return None

    clean_id = Path(run_id).name
    item_dir = reports_dir / clean_id
    if not item_dir.exists() or not item_dir.is_dir():
        return None

    report_file = item_dir / "complete_report.md"
    if not report_file.exists():
        return None

    try:
        content = report_file.read_text(encoding="utf-8")
        parts = clean_id.split("_")
        ticker = parts[0] if parts else clean_id
        date_str = ""
        if len(parts) > 1 and len(parts[1]) == 8:
            date_str = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"

        def _read_sub(subpath: str) -> str:
            p = item_dir / subpath
            return p.read_text(encoding="utf-8") if p.exists() else ""

        market_report = _read_sub("1_analysts/market.md")
        sentiment_report = _read_sub("1_analysts/sentiment.md")
        news_report = _read_sub("1_analysts/news.md")
        fundamentals_report = _read_sub("1_analysts/fundamentals.md")

        bull_history = _read_sub("2_research/bull.md")
        bear_history = _read_sub("2_research/bear.md")
        judge_decision = _read_sub("2_research/manager.md")

        trader_plan = _read_sub("3_trading/trader.md")

        aggressive_history = _read_sub("4_risk/aggressive.md")
        conservative_history = _read_sub("4_risk/conservative.md")
        neutral_history = _read_sub("4_risk/neutral.md")
        portfolio_decision = _read_sub("5_portfolio/decision.md")

        from tradingagents.agents.utils.rating import parse_rating
        parsed = parse_rating(portfolio_decision or content)
        signal = parsed if parsed and parsed != "REVIEW" else None

        return {
            "run_id": clean_id,
            "ticker": ticker,
            "date": date_str or datetime.now().strftime("%Y-%m-%d"),
            "signal": signal,
            "market_report": market_report,
            "sentiment_report": sentiment_report,
            "news_report": news_report,
            "fundamentals_report": fundamentals_report,
            "investment_debate": {
                "bull_history": bull_history,
                "bear_history": bear_history,
                "judge_decision": judge_decision,
            },
            "trader_plan": trader_plan,
            "risk_debate": {
                "aggressive_history": aggressive_history,
                "conservative_history": conservative_history,
                "neutral_history": neutral_history,
                "judge_decision": portfolio_decision,
            },
            "final_decision": portfolio_decision or content,
            "report_path": str(report_file),
        }
    except Exception as exc:
        logger.warning("Error reading saved report %s: %s", clean_id, exc)
        return None


@app.get("/api/runs/{run_id}/report")
async def get_report(run_id: str):
    """Get the full report for a completed run or saved report."""
    if run_id in _completed_reports:
        return _completed_reports[run_id]
    disk_report = _load_on_disk_report(run_id)
    if disk_report is not None:
        return disk_report
    raise HTTPException(status_code=404, detail="Report not available")


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request):
    """SSE endpoint for live progress updates."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        last_idx = 0
        while True:
            if await request.is_disconnected():
                break

            events = _run_events.get(run_id, [])
            while last_idx < len(events):
                evt = events[last_idx]
                yield f"event: {evt['type']}\ndata: {json.dumps(evt['data'])}\n\n"
                last_idx += 1

                # Stop streaming after terminal events
                if evt["type"] in ("complete", "error"):
                    return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history():
    """Return past analysis results from results_dir."""
    results_dir = Path(DEFAULT_CONFIG.get("results_dir", ""))
    reports_dir = results_dir / "reports"
    history = []

    if reports_dir.exists():
        for item in sorted(reports_dir.iterdir(), reverse=True):
            if item.is_dir():
                report_file = item / "complete_report.md"
                if report_file.exists():
                    try:
                        content = report_file.read_text(encoding="utf-8")
                        parts = item.name.split("_")
                        ticker = parts[0] if parts else item.name
                        date_str = ""
                        time_str = ""
                        if len(parts) > 1 and len(parts[1]) == 8:
                            date_str = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
                        if len(parts) > 2 and len(parts[2]) >= 4:
                            time_str = f"{parts[2][:2]}:{parts[2][2:4]}"

                        from tradingagents.agents.utils.rating import parse_rating
                        parsed = parse_rating(content)
                        signal = parsed if parsed and parsed != "REVIEW" else None

                        history.append({
                            "id": item.name,
                            "run_id": item.name,
                            "ticker": ticker,
                            "date": date_str or datetime.now().strftime("%Y-%m-%d"),
                            "time": time_str,
                            "timestamp": "_".join(parts[1:]) if len(parts) > 1 else "",
                            "signal": signal,
                            "status": "completed",
                            "provider": "Saved",
                            "report": content,
                            "path": str(report_file),
                        })
                    except Exception as exc:
                        logger.warning("Failed to parse history report %s: %s", item.name, exc)

    return {"history": history[:50]}  # cap at 50


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("TRADINGAGENTS_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADINGAGENTS_DASHBOARD_PORT", "8050"))
    uvicorn.run(app, host=host, port=port, log_level="info")
