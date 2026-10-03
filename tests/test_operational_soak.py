from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.main import app
from tradingagents.operations import OperationalSoakRunner, SoakThresholds


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_soak_pass_requires_real_workload_reconnect_analysis_and_stable_resources():
    clock = FakeClock()
    reconnects = []
    analyses = []
    sample_number = 0

    def resources():
        nonlocal sample_number
        sample_number += 1
        return {
            "rss_bytes": 1000 + sample_number,
            "private_bytes": 2000 + sample_number,
            "cpu_seconds": sample_number / 10,
            "thread_count": 5,
            "handle_count": 10,
            "log_file_count": 2,
            "log_total_bytes": 1500,
            "log_largest_file_bytes": 900,
            "log_max_bytes": 1024,
            "log_backup_count": 2,
        }

    report = OperationalSoakRunner(
        duration_seconds=4,
        sample_interval_seconds=1,
        workload_probe=lambda: {"mt5_connected": True, "observation_running": True},
        resource_probe=resources,
        reconnect_probe=lambda: reconnects.append(True),
        reconnect_interval_seconds=1,
        analysis_probe=lambda: analyses.append(True),
        analysis_interval_seconds=1,
        thresholds=SoakThresholds(minimum_duration_seconds=3),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ).run()

    assert report.status == "PASS"
    assert len(report.samples) == 4
    assert reconnects and analyses
    assert all(report.checks.values())


def test_short_or_incomplete_run_cannot_be_reported_as_pass():
    clock = FakeClock()
    report = OperationalSoakRunner(
        duration_seconds=2,
        sample_interval_seconds=1,
        workload_probe=lambda: {"mt5_connected": True},
        resource_probe=lambda: {
            "rss_bytes": 1000,
            "thread_count": 1,
            "handle_count": None,
            "log_file_count": 1,
            "log_total_bytes": 10,
            "log_largest_file_bytes": 10,
            "log_max_bytes": 1024,
            "log_backup_count": 2,
        },
        thresholds=SoakThresholds(minimum_duration_seconds=60),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ).run()

    assert report.status == "FAIL"
    assert not report.checks["minimum_duration"]
    assert not report.checks["reconnect_exercised"]
    assert not report.checks["analysis_exercised"]


def test_probe_errors_are_sanitized_and_fail_acceptance():
    clock = FakeClock()

    def failed_probe():
        raise RuntimeError("secret vendor response")

    report = OperationalSoakRunner(
        duration_seconds=2,
        sample_interval_seconds=1,
        workload_probe=failed_probe,
        resource_probe=lambda: {},
        thresholds=SoakThresholds(minimum_duration_seconds=0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ).run()

    rendered = json.dumps(report.to_dict())
    assert report.status == "FAIL"
    assert report.sanitized_errors == {"RuntimeError": 2}
    assert "secret vendor response" not in rendered


def test_report_is_written_atomically_as_json(tmp_path):
    clock = FakeClock()
    report = OperationalSoakRunner(
        duration_seconds=2,
        sample_interval_seconds=1,
        workload_probe=lambda: {},
        resource_probe=lambda: {
            "rss_bytes": 1,
            "thread_count": 1,
            "handle_count": None,
            "log_file_count": 1,
            "log_total_bytes": 1,
            "log_largest_file_bytes": 1,
            "log_max_bytes": 1024,
            "log_backup_count": 1,
        },
        thresholds=SoakThresholds(minimum_duration_seconds=0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ).run()
    destination = report.write_json(tmp_path / "nested" / "evidence.json")

    assert json.loads(destination.read_text(encoding="utf-8"))["status"] == "FAIL"
    assert not destination.with_suffix(".json.tmp").exists()


def test_operations_soak_command_is_exposed():
    result = CliRunner().invoke(app, ["operations", "soak-dashboard", "--help"])
    assert result.exit_code == 0
    assert "--duration-hours" in result.output
