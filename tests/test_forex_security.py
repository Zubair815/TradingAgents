"""Phase 32 security boundaries, including every registered Forex operation."""

import builtins
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.mt5.connection import MT5ConnectionManager
from web import server
from web.forex_routes import reset_forex_state, set_forex_dependencies

ROUTES = [(method.upper(), path) for path, operations in server.app.openapi()["paths"].items()
          if path.startswith("/api/forex/") for method in operations
          if method in ("get", "post", "put", "patch", "delete")]
assert len(ROUTES) >= 40, "Forex route inventory must not silently become empty"


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_API_KEY", None)
    reset_forex_state()
    journal = ForexTradeJournal(":memory:")
    observer = MagicMock()
    observer.connection.is_connected.return_value = False
    observer.connection.login = None
    observer.connection.server = None
    observer.connection.path = None
    set_forex_dependencies(journal=journal, mt5_observer=observer)
    yield observer
    reset_forex_state()
    journal.close()


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_forex_operation_requires_auth(method, path):
    client = TestClient(server.app)
    path = re.sub(r"\{[^}]+\}", "test", path)
    with patch.object(server, "DASHBOARD_API_KEY", None):
        response = client.request(method, path, json={})
    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("mode", ["session", "api_key", "bearer"])
def test_valid_auth_can_create_proposal_and_read_private_data(isolated, monkeypatch, mode):
    client = TestClient(server.app)
    if mode == "session":
        client.get("/")
    else:
        monkeypatch.setattr(server, "DASHBOARD_API_KEY", "example-test-key")
        client.headers.update({"X-API-Key": "example-test-key"} if mode == "api_key" else
                              {"Authorization": "Bearer example-test-key"})
    response = client.post("/api/forex/proposals", json={
        "pair": "EURUSD", "action": "LONG", "entry_price": 1.08,
        "stop_loss": 1.07, "take_profit": 1.10,
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/forex/journal/trades").status_code == 200
    assert client.get("/api/forex/proposals").json()["count"] == 1
    client.headers["X-API-Key"] = "wrong"
    assert client.get("/api/forex/proposals").status_code == 401


def test_auth_infrastructure_failures_deny_access(isolated, monkeypatch):
    client = TestClient(server.app)
    client.get("/")
    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "web.server":
            raise ImportError("sensitive implementation detail")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=unavailable):
        response = client.get("/api/forex/proposals")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    for broken in (None, False):
        with patch.object(server, "verify_auth", return_value=broken):
            assert client.get("/api/forex/proposals").status_code == 503
    monkeypatch.setattr(server, "_SESSION_TOKEN", None)
    assert client.get("/api/forex/proposals").status_code == 503


def test_cookies_expire_and_are_not_exposed(isolated, monkeypatch):
    for scheme in ("http", "https"):
        client = TestClient(server.app, base_url=f"{scheme}://testserver")
        response = client.get("/")
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=28800" in cookie
        assert ("Secure" in cookie) == (scheme == "https")
        assert server._SESSION_TOKEN not in response.text
        assert client.cookies.get("tradingagents_session") not in response.text
        assert client.get("/api/forex/proposals").status_code == 200
        assert client.get("/api/forex/proposals", headers={"Origin": "https://attacker.example"}).status_code == 401
        with patch.object(server.time, "time", return_value=server.time.time() + 28801):
            assert client.get("/api/forex/proposals").status_code == 401
    remote = TestClient(server.app, base_url="http://attacker.example")
    assert "set-cookie" not in remote.get("/").headers


def test_key_session_supports_eventsource_without_url_secrets(isolated, monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_API_KEY", "configured-key")
    client = TestClient(server.app)
    assert "set-cookie" not in client.get("/").headers
    assert client.post("/api/auth/session").status_code == 401
    response = client.post("/api/auth/session", headers={"X-API-Key": "configured-key"})
    assert response.status_code == 200
    assert "configured-key" not in response.text + response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/forex/runs").status_code == 200
    assert client.get("/api/forex/runs/missing/events").status_code == 404  # Auth passed.
    monkeypatch.setattr(server, "DASHBOARD_API_KEY", "rotated-key")
    assert client.get("/api/forex/runs").status_code == 401


def test_credentials_validation_and_exceptions_are_safe(isolated, caplog):
    client = TestClient(server.app)
    client.get("/")
    secret = "SENTINEL_BROKER_PASSWORD"
    response = client.post("/api/forex/mt5/connect", json={"password": {"secret": secret}})
    assert response.status_code == 422
    assert secret not in response.text
    isolated.connection.connect.side_effect = RuntimeError(f"Traceback: password={secret}")
    response = client.post("/api/forex/mt5/connect", json={"password": secret})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MT5_CONNECTION_FAILED"
    isolated.get_account_info.side_effect = RuntimeError(f"Traceback: {secret}")
    response = client.get("/api/forex/mt5/account")
    assert response.status_code == 500
    assert secret not in response.text + caplog.text
    assert "Traceback" not in response.text
    assert secret not in client.get("/api/forex/mt5/status").text


def test_mt5_password_consumed_once_and_omitted_on_terminal_reuse():
    api = MagicMock()
    api.initialize.return_value = True
    api.login.return_value = True
    connection = MT5ConnectionManager(password="one-shot", mt5_api=api)
    connection.connect(login=123)
    assert api.login.call_args.kwargs["password"] == "one-shot"
    assert connection.password is None
    connection.disconnect()
    connection.connect()
    assert "password" not in api.login.call_args.kwargs


def test_config_html_and_frontend_secret_contract(isolated, monkeypatch):
    secret = "SENTINEL_LLM_KEY"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setitem(server.DEFAULT_CONFIG, "api_key", secret)
    client = TestClient(server.app)
    assert secret not in client.get("/api/config").text + client.get("/").text
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/static/app.js").read_text(encoding="utf-8")
    assert not re.search(r"(?:localStorage|sessionStorage)\s*\.\s*setItem", source)
    assert not re.search(r"[?&](?:api_key|token|password)=", source, re.I)
    if not shutil.which("node"):
        pytest.skip("Node.js is required for frontend syntax and parser execution")
    subprocess.run(["node", "--check", str(root / "web/static/app.js")], check=True, capture_output=True)
    # Exercise the real error parser without a browser or rendering the UI.
    parser = source[source.index("  function apiErrorMessage"):source.index("  async function apiFetch")]
    script = parser + '''
const assert = require('node:assert/strict');
assert.equal(apiErrorMessage({error:{message:'Safe message'}}), 'Safe message');
assert.equal(apiErrorMessage({detail:'Legacy error'}), 'Legacy error');
assert.equal(apiErrorMessage({detail:[{input:'secret'}]}, 'Fallback'), 'Fallback');
'''
    subprocess.run(["node", "-e", script], check=True, capture_output=True)
    helpers = source[source.index("  let sessionKey"):source.index("  // ---- DOM refs ----")]
    script = "let inMemoryApiKey = 'memory-only-key';\n" + helpers + '''
const assert = require('node:assert/strict');
const calls = [];
global.window = {location: {origin: 'http://localhost:8050'},
  fetch: async (url, options) => {calls.push({url, options}); return {ok:true};}};
(async () => {
  await apiFetch('/api/forex/proposals');
  assert.equal(calls[0].url, '/api/auth/session');
  assert.equal(calls[1].options.headers.get('X-API-Key'), inMemoryApiKey);
  await apiFetch('/api/forex/runs');
  assert.equal(calls.length, 3); // Cookie exchange reused for subsequent requests.
  await apiFetch('https://external.example/resource');
  assert.equal(calls[3].options.headers, undefined);
  assert.ok(calls.every(call => !call.url.includes(inMemoryApiKey)));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(["node", "-e", script], check=True, capture_output=True)


def test_env_and_databases_excluded_from_git_tracking():
    """SEC-002: .env and user databases must be excluded from Git by default."""
    root = Path(__file__).resolve().parents[1]
    gitignore_path = root / ".gitignore"
    assert gitignore_path.exists(), ".gitignore must exist"
    gitignore_content = gitignore_path.read_text(encoding="utf-8")
    assert re.search(r"^\.env\b", gitignore_content, re.MULTILINE), ".env must be ignored in .gitignore"
    assert re.search(r"\*\.db\b", gitignore_content, re.MULTILINE), "*.db must be ignored in .gitignore"

    # Verify with git if git is available
    git_bin = shutil.which("git")
    if git_bin:
        # Verify .env is not currently tracked
        res = subprocess.run(
            [git_bin, "ls-files", "--error-unmatch", ".env"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0, ".env must NEVER be tracked by git"

        # Verify .env is recognized as ignored
        check_ignore = subprocess.run(
            [git_bin, "check-ignore", "-v", ".env"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        assert check_ignore.returncode == 0, ".env must be matched by gitignore"


def test_cors_default_origins_restrictive(monkeypatch):
    """SEC-005: CORS origins must be allowlisted and default to localhost/local deployment."""
    monkeypatch.delenv("TRADINGAGENTS_CORS_ORIGINS", raising=False)
    origins = server.get_cors_origins()
    assert origins == ["http://localhost:8050", "http://127.0.0.1:8050"]
    assert "http://0.0.0.0:8050" not in origins
    assert "*" not in origins

    # Test custom configuration
    monkeypatch.setenv("TRADINGAGENTS_CORS_ORIGINS", "http://internal.example:8050, https://dashboard.example.com")
    custom_origins = server.get_cors_origins()
    assert custom_origins == ["http://internal.example:8050", "https://dashboard.example.com"]


def test_dashboard_bind_address_local_default(monkeypatch):
    """DEPLOY-002: Dashboard must bind to 127.0.0.1 by default."""
    monkeypatch.delenv("TRADINGAGENTS_DASHBOARD_HOST", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DASHBOARD_PORT", raising=False)
    host, port = server.get_dashboard_bind_address()
    assert host == "127.0.0.1"
    assert port == 8050

    # Test custom configuration
    monkeypatch.setenv("TRADINGAGENTS_DASHBOARD_HOST", "10.0.0.5")
    monkeypatch.setenv("TRADINGAGENTS_DASHBOARD_PORT", "9090")
    custom_host, custom_port = server.get_dashboard_bind_address()
    assert custom_host == "10.0.0.5"
    assert custom_port == 9090
