from __future__ import annotations

import threading
import time

import pytest

from web.analysis_admission import (
    AdmissionRejected,
    AnalysisAdmissionController,
    IdempotencyConflict,
)


def test_admission_bounds_active_and_queued_work():
    controller = AnalysisAdmissionController(max_active=1, max_queued=1)
    controller.admit("run-1")
    controller.admit("run-2")

    with pytest.raises(AdmissionRejected, match="queue is full"):
        controller.admit("run-3")

    assert controller.start("run-1", lambda: False) is True
    assert controller.snapshot() == {
        "active": 1,
        "queued": 1,
        "max_active": 1,
        "max_queued": 1,
    }

    started = threading.Event()

    def start_second():
        if controller.start("run-2", lambda: False):
            started.set()

    thread = threading.Thread(target=start_second)
    thread.start()
    time.sleep(0.05)
    assert not started.is_set()

    controller.release("run-1")
    thread.join(timeout=1)
    assert started.is_set()
    controller.release("run-2")
    assert controller.snapshot()["active"] == 0


def test_idempotency_returns_original_run_and_rejects_changed_payload():
    controller = AnalysisAdmissionController(max_active=1, max_queued=1)
    first = controller.admit("run-1", idempotency_key="same", fingerprint="abc")
    duplicate = controller.admit("run-2", idempotency_key="same", fingerprint="abc")

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.run_id == "run-1"

    with pytest.raises(IdempotencyConflict, match="different analysis request"):
        controller.admit("run-3", idempotency_key="same", fingerprint="changed")


def test_cancelled_queued_run_releases_capacity():
    controller = AnalysisAdmissionController(max_active=1, max_queued=0)
    controller.admit("run-1")
    assert controller.start("run-1", lambda: True) is False
    assert controller.snapshot()["queued"] == 0
    controller.admit("run-2")
