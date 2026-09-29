import threading
import time
from contextlib import contextmanager

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency for browser smoke tests
    sync_playwright = None


@pytest.mark.e2e
@pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed")
def test_dashboard_browser_smoke():
    import requests
    import uvicorn

    @contextmanager
    def live_server():
        config = uvicorn.Config("web.server:app", host="127.0.0.1", port=8765, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                res = requests.get("http://127.0.0.1:8765/api/config", timeout=1)
                if res.status_code == 200:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("Dashboard server did not start in time")

        try:
            yield
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    with live_server(), sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto("http://127.0.0.1:8765/", wait_until="domcontentloaded", timeout=15000)
                config = page.evaluate("""
                    async function loadConfig() {
                        const res = await fetch('/api/config');
                        return {
                            status: res.status,
                            payload: await res.json(),
                        };
                    }
                    loadConfig();
                """)
                assert config["status"] == 200
                assert config["payload"]["auth_required"] is False
                assert "runtime_settings" in config["payload"]
                assert page.locator("#view-analyze").is_visible()
            finally:
                browser.close()
