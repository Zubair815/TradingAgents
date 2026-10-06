"""Tests for TradingAgents Web Dashboard backend."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from web import server
from web.server import AnalysisRequest, app


@pytest.fixture
def client():
    test_client = TestClient(app)
    test_client.get("/")
    return test_client


# ---------------------------------------------------------------------------
# Ticker Validation Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestTickerValidation:
    def test_valid_tickers_accepted(self):
        for ticker in ("NVDA", "AAPL", "BRK-B", "BRK.A", "0700.HK", "GC=F", "EURUSD+"):
            req = AnalysisRequest(ticker=ticker)
            assert req.ticker == ticker.upper()

    def test_empty_ticker_rejected(self):
        with pytest.raises(ValueError):
            AnalysisRequest(ticker="")

    def test_xss_and_malicious_tickers_rejected(self):
        for bad in (
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "../../../etc/passwd",
            "AAPL\x00",
            "AAPL; rm -rf /",
            "A B C",
            "A" * 35,
            "..",
            "...",
        ):
            with pytest.raises(ValueError):
                AnalysisRequest(ticker=bad)

    def test_api_rejects_malicious_ticker_with_422(self, client):
        res = client.get("/")
        assert res.status_code == 200

        bad_payload = {"ticker": "<script>alert('xss')</script>"}
        res = client.post("/api/analyze", json=bad_payload)
        assert res.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"ticker": "AAPL", "date": "not-a-date"},
            {"ticker": "AAPL", "date": "2999-01-01"},
            {"ticker": "AAPL", "analysts": []},
            {"ticker": "AAPL", "analysts": ["bogus"]},
            {"ticker": "AAPL", "provider": "made-up"},
            {"ticker": "AAPL", "max_tokens": 0},
            {"ticker": "AAPL", "temperature": 2.1},
        ],
    )
    def test_invalid_analysis_contract_rejected(self, client, payload):
        response = client.post("/api/analyze", json=payload)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Authentication Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDashboardAuth:
    def test_unauthenticated_request_rejected_with_401(self, client):
        # A direct client without session cookies or API key
        fresh_client = TestClient(app)
        res = fresh_client.post("/api/analyze", json={"ticker": "AAPL"})
        assert res.status_code == 401
        assert "Unauthorized" in res.json().get("detail", "")

    def test_session_cookie_authenticates_browser_client(self, client):
        # Visiting / establishes session cookie
        res = client.get("/")
        assert res.status_code == 200
        assert "tradingagents_session" in client.cookies

        with patch("web.server._run_analysis"):
            post_res = client.post("/api/analyze", json={"ticker": "AAPL"})
            assert post_res.status_code == 200
            assert "run_id" in post_res.json()

    def test_session_token_header_authenticates(self):
        fresh_client = TestClient(app)
        headers = {"X-Session-Token": server._SESSION_TOKEN}
        with patch("web.server._run_analysis"):
            res = fresh_client.post("/api/analyze", json={"ticker": "AAPL"}, headers=headers)
            assert res.status_code == 200

    def test_configured_api_key_enforced(self):
        fresh_client = TestClient(app)
        with patch.object(server, "DASHBOARD_API_KEY", "super-secret-key-123"):
            # Session cookie alone must NOT suffice when explicit API key is configured
            fresh_client.cookies.set("tradingagents_session", server._SESSION_TOKEN)
            res = fresh_client.post("/api/analyze", json={"ticker": "AAPL"})
            assert res.status_code == 401

            # Wrong key rejected
            res = fresh_client.post(
                "/api/analyze", json={"ticker": "AAPL"}, headers={"X-API-Key": "wrong"}
            )
            assert res.status_code == 401

            # Correct key accepted
            with patch("web.server._run_analysis"):
                res = fresh_client.post(
                    "/api/analyze",
                    json={"ticker": "AAPL"},
                    headers={"X-API-Key": "super-secret-key-123"},
                )
                assert res.status_code == 200

            # Bearer token also accepted
            with patch("web.server._run_analysis"):
                res = fresh_client.post(
                    "/api/analyze",
                    json={"ticker": "AAPL"},
                    headers={"Authorization": "Bearer super-secret-key-123"},
                )
                assert res.status_code == 200

    def test_config_endpoint_does_not_leak_session_token_or_set_cookie(self):
        fresh_client = TestClient(app)
        res = fresh_client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert "session_token" not in data or data["session_token"] is None
        assert "tradingagents_session" not in fresh_client.cookies

        # Verify a fresh client cannot POST to /api/analyze using anything from /api/config
        with patch("web.server._run_analysis"):
            post_res = fresh_client.post("/api/analyze", json={"ticker": "AAPL"})
            assert post_res.status_code == 401

    def test_analysis_results_require_authentication(self):
        fresh_client = TestClient(app)
        assert fresh_client.get("/api/runs").status_code == 401
        assert fresh_client.get("/api/history").status_code == 401
        assert fresh_client.get("/api/runs/missing").status_code == 401
        assert fresh_client.get("/api/runs/missing/report").status_code == 401
        assert fresh_client.get("/api/runs/missing/events").status_code == 401


@pytest.mark.unit
class TestHealthEndpoints:
    def test_liveness_is_public_and_has_stable_contract(self, client):
        response = client.get("/api/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"
        assert response.json()["service"] == "tradingagents-dashboard"

    def test_readiness_is_authenticated_and_mt5_disconnect_is_degraded(self):
        client = TestClient(app)
        assert client.get("/api/health/ready").status_code == 401

        journal = SimpleNamespace(health_check=lambda: {"status": "ready"})
        connection = SimpleNamespace(is_connected=lambda: False)
        runtime = SimpleNamespace(
            observer=SimpleNamespace(connection=connection),
            service=SimpleNamespace(is_running=False),
            closed=False,
        )
        with (
            patch("web.forex_routes.get_journal", return_value=journal),
            patch("web.forex_routes.get_forex_runtime", return_value=runtime),
        ):
            response = client.get(
                "/api/health/ready",
                headers={"X-Session-Token": server._SESSION_TOKEN},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["components"]["database"]["status"] == "ready"
        assert body["components"]["mt5"] == {"status": "degraded", "connected": False}

    def test_resource_sample_is_authenticated_and_contains_no_configuration(self):
        client = TestClient(app)
        assert client.get("/api/health/resources").status_code == 401

        response = client.get(
            "/api/health/resources",
            headers={"X-Session-Token": server._SESSION_TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["process_id"] > 0
        assert body["thread_count"] >= 1
        assert "environment" not in body
        assert "api_key" not in body


# ---------------------------------------------------------------------------
# CORS Configuration Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCORSConfig:
    def test_cors_does_not_allow_all_origins(self):
        for middleware in app.user_middleware:
            if "CORSMiddleware" in str(middleware.cls):
                kwargs = getattr(middleware, "kwargs", {})
                allow_origins = kwargs.get("allow_origins", [])
                assert "*" not in allow_origins
                assert "http://localhost:8050" in allow_origins
                assert "http://127.0.0.1:8050" in allow_origins


# ---------------------------------------------------------------------------
# History and Report Retrieval from Disk Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestHistoryAndReportsPersistence:
    def test_history_and_report_loaded_from_disk_after_restart(self, client, tmp_path):
        # Create a mock report tree on disk under tmp_path
        reports_dir = tmp_path / "reports"
        run_folder = reports_dir / "TSLA_20260926_153000"
        run_folder.mkdir(parents=True)

        analysts_dir = run_folder / "1_analysts"
        analysts_dir.mkdir()
        (analysts_dir / "market.md").write_text("Bullish market trend", encoding="utf-8")

        research_dir = run_folder / "2_research"
        research_dir.mkdir()
        (research_dir / "bull.md").write_text("Strong growth catalysts", encoding="utf-8")
        (research_dir / "manager.md").write_text("Approve long thesis", encoding="utf-8")

        portfolio_dir = run_folder / "5_portfolio"
        portfolio_dir.mkdir()
        (portfolio_dir / "decision.md").write_text(
            "Final Trade Decision: BUY\nTarget allocation: 5%", encoding="utf-8"
        )

        complete_md = run_folder / "complete_report.md"
        complete_md.write_text(
            "# Trading Analysis Report: TSLA\n\nGenerated: 2026-09-26 15:30:00\n\nFinal Trade Decision: BUY",
            encoding="utf-8",
        )

        with patch.dict(DEFAULT_CONFIG, {"results_dir": str(tmp_path)}):
            # 1. /api/history returns the saved report
            res = client.get("/api/history")
            assert res.status_code == 200
            data = res.json()
            history = data.get("history", [])
            assert len(history) >= 1
            item = next((h for h in history if h["ticker"] == "TSLA"), None)
            assert item is not None
            assert item["ticker"] == "TSLA"
            assert item["date"] == "2026-09-26"
            assert item["signal"] == "Buy"
            assert item["status"] == "completed"

            # 2. /api/runs/{run_id}/report retrieves the report from disk even with empty _completed_reports
            report_res = client.get(f"/api/runs/{item['id']}/report")
            assert report_res.status_code == 200
            report_data = report_res.json()
            assert report_data["ticker"] == "TSLA"
            assert report_data["signal"] == "Buy"
            assert report_data["market_report"] == "Bullish market trend"
            assert report_data["investment_debate"]["bull_history"] == "Strong growth catalysts"
            assert "BUY" in report_data["final_decision"]

    def test_saved_report_with_historical_analysis_date_parsed_correctly(self, client, tmp_path):
        # Folder timestamp is 2026-09-26, but the analysis was for historical date 2024-01-15
        reports_dir = tmp_path / "reports"
        run_folder = reports_dir / "NVDA_20260926_120000"
        run_folder.mkdir(parents=True)

        complete_md = run_folder / "complete_report.md"
        complete_md.write_text(
            "# Trading Analysis Report: NVDA\n\nAnalysis Date: 2024-01-15\nGenerated: 2026-09-26 12:00:00\n\nFinal Trade Decision: BUY",
            encoding="utf-8",
        )

        with patch.dict(DEFAULT_CONFIG, {"results_dir": str(tmp_path)}):
            res = client.get("/api/history")
            assert res.status_code == 200
            history = res.json().get("history", [])
            item = next((h for h in history if h["ticker"] == "NVDA"), None)
            assert item is not None
            # Must be the requested analysis date 2024-01-15, NOT the folder's 2026-09-26
            assert item["date"] == "2024-01-15"

            report_res = client.get(f"/api/runs/{item['id']}/report")
            assert report_res.status_code == 200
            report_data = report_res.json()
            assert report_data["date"] == "2024-01-15"

    def test_completed_run_records_report_id_and_links_history(self, client, tmp_path):
        from unittest.mock import MagicMock

        from web.server import _completed_reports, _run_analysis, _runs

        run_id = "liverun1"
        _runs[run_id] = {
            "run_id": run_id,
            "ticker": "AAPL",
            "date": "2024-06-01",
            "status": "queued",
            "provider": "openai",
            "quick_model": "gpt-4o",
            "deep_model": "gpt-4o",
            "started_at": "2026-09-26T12:00:00",
            "finished_at": None,
            "error": None,
            "signal": None,
        }

        folder_name = "AAPL_20260926_120000"
        fake_report_path = tmp_path / "reports" / folder_name / "complete_report.md"
        fake_report_path.parent.mkdir(parents=True)
        fake_report_path.write_text(
            "# Trading Analysis Report: AAPL\n\nAnalysis Date: 2024-06-01\nGenerated: 2026-09-26 12:00:00\n\nFinal Trade Decision: HOLD",
            encoding="utf-8",
        )

        mock_graph = MagicMock()
        mock_graph.checkpoint_scope.return_value.__enter__.return_value = "thread-1"
        mock_graph.create_run_state.return_value = {}
        mock_graph.checkpoint_input.return_value = {}
        mock_graph.propagator.get_graph_args.return_value = {}
        mock_graph.graph.stream.return_value = iter([{"final_trade_decision": "Rating: Hold"}])
        mock_graph.process_signal.return_value = "Hold"
        mock_graph.save_reports.return_value = fake_report_path

        req = AnalysisRequest(ticker="AAPL", date="2024-06-01", analysts=["market"])

        with patch("web.server.TradingAgentsGraph", return_value=mock_graph):
            _run_analysis(run_id, req)

        # 1. Run metadata records report_id and report_path
        assert _runs[run_id]["status"] == "completed"
        assert _runs[run_id]["report_id"] == folder_name
        assert _runs[run_id]["report_path"] == str(fake_report_path)

        # 2. _completed_reports indexed by both run_id and report_id
        assert run_id in _completed_reports
        assert folder_name in _completed_reports

        # 3. /api/history links live_run_id
        with patch.dict(DEFAULT_CONFIG, {"results_dir": str(tmp_path)}):
            res = client.get("/api/history")
            assert res.status_code == 200
            history = res.json().get("history", [])
            aapl_item = next((h for h in history if h["ticker"] == "AAPL"), None)
            assert aapl_item is not None
            assert aapl_item["live_run_id"] == run_id


# ---------------------------------------------------------------------------
# Pipeline Progress Event Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestPipelineProgress:
    def test_pipeline_progress_emits_increasing_node_stages(self):
        from unittest.mock import MagicMock

        from web.server import AnalysisRequest, _run_analysis, _run_events, _runs

        run_id = "testrun1"
        _runs[run_id] = {
            "run_id": run_id,
            "ticker": "NVDA",
            "date": "2026-09-26",
            "status": "queued",
            "started_at": "2026-09-26T12:00:00",
        }
        _run_events[run_id] = []

        mock_chunks = [
            {"market_report": "market report"},
            {"sentiment_report": "sentiment report"},
            {"news_report": "news report"},
            {"fundamentals_report": "fundamentals report"},
            {"investment_debate_state": {"bull_history": "bull", "last_speaker": "bull"}},
            {"investment_debate_state": {"bear_history": "bear", "last_speaker": "bear"}},
            {"investment_debate_state": {"judge_decision": "manager approved"}},
            {"trader_investment_plan": "trader plan"},
            {"risk_debate_state": {"aggressive_history": "agg", "latest_speaker": "Aggressive"}},
            {"risk_debate_state": {"conservative_history": "cons", "latest_speaker": "Conservative"}},
            {"risk_debate_state": {"neutral_history": "neu", "latest_speaker": "Neutral"}},
            {"risk_debate_state": {"judge_decision": "portfolio approved"}},
            {"final_trade_decision": "Rating: Buy"},
        ]

        mock_graph = MagicMock()
        mock_graph.checkpoint_scope.return_value.__enter__.return_value = "thread-1"
        mock_graph.create_run_state.return_value = {}
        mock_graph.checkpoint_input.return_value = {}
        mock_graph.propagator.get_graph_args.return_value = {}
        mock_graph.graph.stream.return_value = iter(mock_chunks)
        mock_graph.process_signal.return_value = "Buy"
        mock_graph.save_reports.return_value = Path("reports/NVDA_20260926_120000/complete_report.md")

        req = AnalysisRequest(ticker="NVDA", analysts=["market", "social", "news", "fundamentals"])

        with patch("web.server.TradingAgentsGraph", return_value=mock_graph):
            _run_analysis(run_id, req)

        events = _run_events[run_id]
        node_events = [e for e in events if e["type"] == "node"]
        assert len(node_events) > 5

        # Verify progress advances
        progresses = [e["data"]["progress"] for e in node_events]
        assert progresses[0] > 0.0
        assert progresses[-1] > progresses[0]
        # Verify node identifiers include analysts and debate participants
        emitted_nodes = [e["data"]["node"] for e in node_events]
        assert "market_analyst" in emitted_nodes
        assert "social_media_analyst" in emitted_nodes
        assert "bull_researcher" in emitted_nodes
        assert "trader" in emitted_nodes
        assert "portfolio_manager" in emitted_nodes


# ---------------------------------------------------------------------------
# Frontend Structure & 9-Tab Dashboard Tests (Phase 24)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFrontendStructure:
    def test_root_serves_html_with_nine_subsystem_tabs(self, client):
        res = client.get("/")
        assert res.status_code == 200
        html = res.text

        # Verify all 9 navigation tabs exist
        expected_tabs = [
            'data-view="dashboard"',
            'data-view="analyze"',
            'data-view="proposals"',
            'data-view="mt5"',
            'data-view="journal"',
            'data-view="performance"',
            'data-view="backtest"',
            'data-view="learning"',
            'data-view="settings"',
        ]
        for tab in expected_tabs:
            assert tab in html, f"Missing expected tab navigation: {tab}"

    def test_root_serves_all_nine_subsystem_views(self, client):
        res = client.get("/")
        assert res.status_code == 200
        html = res.text

        # Verify all 9 view containers exist
        expected_views = [
            'id="view-dashboard"',
            'id="view-analyze"',
            'id="view-proposals"',
            'id="view-mt5"',
            'id="view-journal"',
            'id="view-performance"',
            'id="view-backtest"',
            'id="view-learning"',
            'id="view-settings"',
        ]
        for view_id in expected_views:
            assert view_id in html, f"Missing expected view container: {view_id}"

    def test_static_assets_served(self, client):
        css = client.get("/static/styles.css")
        assert css.status_code == 200
        assert "tabs-wrapper" in css.text

        js = client.get("/static/app.js")
        assert js.status_code == 200
        assert "loadDashboardOverview" in js.text

