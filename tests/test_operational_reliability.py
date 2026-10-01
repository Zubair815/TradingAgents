from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.database import backup
from tradingagents.database.backup import recover_stale_maintenance_marker
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS
from tradingagents.mt5.connection import MT5ConnectionManager
from tradingagents.mt5.models import MT5ConnectionStatus
from tradingagents.mt5.observer import MT5Observer
from tradingagents.operations.logging import configure_bounded_logging


def _remove_operational_handler() -> None:
    root = logging.getLogger()
    loggers = [root, logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")]
    removed = set()
    for logger in loggers:
        for handler in list(logger.handlers):
            if getattr(handler, "name", None) == "tradingagents-rotating-file":
                logger.removeHandler(handler)
                removed.add(handler)
    for handler in removed:
        handler.close()


def test_application_log_rotates_and_configuration_is_idempotent(tmp_path, monkeypatch):
    _remove_operational_handler()
    monkeypatch.setenv("TRADINGAGENTS_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("TRADINGAGENTS_LOG_BACKUP_COUNT", "2")
    try:
        path = configure_bounded_logging(tmp_path)
        assert configure_bounded_logging(tmp_path) == path
        logger = logging.getLogger("tradingagents.soak")
        for index in range(500):
            logger.info("bounded-log-record-%04d %s", index, "x" * 80)
        for handler in logging.getLogger().handlers:
            handler.flush()
        files = sorted(tmp_path.glob("tradingagents.log*"))
        assert 1 <= len(files) <= 3
        assert all(item.stat().st_size <= 1024 for item in files)
        assert configure_bounded_logging(tmp_path) == path
    finally:
        _remove_operational_handler()


def test_abandoned_maintenance_marker_is_recovered_only_after_grace(tmp_path, monkeypatch):
    database = tmp_path / "journal.db"
    marker = Path(f"{database}.maintenance.lock")
    marker.write_text("pid=424242\ncreated_at=old\n", encoding="utf-8")
    monkeypatch.setattr(backup, "_process_is_alive", lambda pid: False)

    assert not recover_stale_maintenance_marker(database, grace_seconds=300)
    old = time.time() - 301
    os.utime(marker, (old, old))
    assert recover_stale_maintenance_marker(database, grace_seconds=300)
    assert not marker.exists()


def test_unknown_or_live_maintenance_marker_is_never_removed(tmp_path, monkeypatch):
    database = tmp_path / "journal.db"
    marker = Path(f"{database}.maintenance.lock")
    marker.write_text("unparseable", encoding="utf-8")
    old = time.time() - 3600
    os.utime(marker, (old, old))
    assert not recover_stale_maintenance_marker(database, grace_seconds=0)

    marker.write_text("pid=42\n", encoding="utf-8")
    os.utime(marker, (old, old))
    monkeypatch.setattr(backup, "_process_is_alive", lambda pid: True)
    assert not recover_stale_maintenance_marker(database, grace_seconds=0)
    assert marker.exists()


def test_every_openai_compatible_provider_accepts_bounded_timeout_and_retries():
    assert OPENAI_COMPATIBLE_PROVIDERS
    # Provider constructors share the same allowlist; this protects the common
    # operational knobs from being silently discarded.
    from tradingagents.llm_clients.openai_client import _PASSTHROUGH_KWARGS

    assert {"timeout", "max_retries"} <= set(_PASSTHROUGH_KWARGS)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "bedrock"])
def test_graph_forwards_bounded_provider_operations(provider):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {
        "llm_provider": provider,
        "llm_max_retries": 2,
        "llm_timeout_seconds": 45,
    }
    kwargs = graph._get_provider_kwargs()
    assert kwargs["max_retries"] == 2
    assert kwargs["timeout"] == 45


@pytest.mark.parametrize("name,value", [
    ("TRADINGAGENTS_LOG_MAX_BYTES", "0"),
    ("TRADINGAGENTS_LOG_BACKUP_COUNT", "100"),
])
def test_invalid_log_bounds_fail_loudly(tmp_path, monkeypatch, name, value):
    _remove_operational_handler()
    monkeypatch.setenv(name, value)
    try:
        with pytest.raises(ValueError):
            configure_bounded_logging(tmp_path)
    finally:
        _remove_operational_handler()


def test_native_mt5_reads_are_serialized_across_observer_threads():
    class ConcurrencyDetectingAPI:
        def __init__(self):
            self.active = 0
            self.overlapped = False

        def terminal_info(self):
            return SimpleNamespace(connected=True)

        def _read(self, value):
            self.active += 1
            self.overlapped = self.overlapped or self.active > 1
            time.sleep(0.02)
            self.active -= 1
            return value

        def account_info(self):
            return self._read({"login": 1, "currency": "USD"})

        def history_deals_get(self, *args, **kwargs):
            return self._read(())

    api = ConcurrencyDetectingAPI()
    connection = MT5ConnectionManager(mt5_api=api)
    connection.status = MT5ConnectionStatus.CONNECTED
    observer = MT5Observer(connection=connection, auto_connect=False)
    second_observer = MT5Observer(connection=connection, auto_connect=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        account = pool.submit(observer.get_account_info)
        deals = pool.submit(second_observer.get_deals)
        assert account.result().login == 1
        assert deals.result() == []

    assert not api.overlapped
