"""Deterministic operational-soak measurement and acceptance reporting."""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProcessResources:
    captured_at_utc: str
    process_id: int
    rss_bytes: int
    private_bytes: int | None
    cpu_seconds: float
    thread_count: int
    handle_count: int | None
    log_file_count: int
    log_total_bytes: int
    log_largest_file_bytes: int
    log_max_bytes: int | None
    log_backup_count: int | None


def sample_process_resources() -> ProcessResources:
    """Sample this process without adding a production dependency on psutil."""
    rss_bytes = 0
    private_bytes: int | None = None
    handle_count: int | None = None
    cpu_seconds = time.process_time()

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        process = ctypes.windll.kernel32.GetCurrentProcess()
        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        ):
            rss_bytes = int(counters.WorkingSetSize)
            private_bytes = int(counters.PrivateUsage)
        handles = wintypes.DWORD()
        if ctypes.windll.kernel32.GetProcessHandleCount(process, ctypes.byref(handles)):
            handle_count = int(handles.value)
    else:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux reports KiB; macOS reports bytes.
        scale = 1 if platform.system() == "Darwin" else 1024
        rss_bytes = int(usage.ru_maxrss * scale)

    log_files: list[Path] = []
    log_max_bytes: int | None = None
    log_backup_count: int | None = None
    for handler in logging.getLogger().handlers:
        base_filename = getattr(handler, "baseFilename", None)
        if not base_filename or not hasattr(handler, "maxBytes"):
            continue
        base = Path(base_filename)
        log_files.extend(item for item in base.parent.glob(f"{base.name}*") if item.is_file())
        log_max_bytes = int(handler.maxBytes)
        log_backup_count = int(handler.backupCount)
        break
    sizes = [item.stat().st_size for item in log_files]

    return ProcessResources(
        captured_at_utc=_utc_now(),
        process_id=os.getpid(),
        rss_bytes=rss_bytes,
        private_bytes=private_bytes,
        cpu_seconds=cpu_seconds,
        thread_count=threading.active_count(),
        handle_count=handle_count,
        log_file_count=len(log_files),
        log_total_bytes=sum(sizes),
        log_largest_file_bytes=max(sizes, default=0),
        log_max_bytes=log_max_bytes,
        log_backup_count=log_backup_count,
    )


@dataclass(frozen=True)
class SoakThresholds:
    minimum_duration_seconds: float = 4 * 60 * 60
    maximum_failure_rate: float = 0.01
    maximum_consecutive_failures: int = 3
    maximum_rss_growth_bytes: int = 128 * 1024 * 1024
    maximum_thread_growth: int = 4
    maximum_handle_growth: int = 32


@dataclass
class OperationalSoakReport:
    started_at_utc: str
    finished_at_utc: str
    requested_duration_seconds: float
    observed_duration_seconds: float
    host: dict[str, str] = field(default_factory=dict)
    release_revision: str | None = None
    working_tree_clean: bool | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)
    workload_attempts: int = 0
    workload_failures: int = 0
    reconnect_attempts: int = 0
    reconnect_failures: int = 0
    analysis_attempts: int = 0
    analysis_failures: int = 0
    maximum_consecutive_failures: int = 0
    sanitized_errors: dict[str, int] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    status: str = "NOT_EVALUATED"
    disclosures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, destination: str | Path) -> Path:
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)
        return path


class OperationalSoakRunner:
    """Run bounded probes and evaluate objective resource/recovery thresholds."""

    def __init__(
        self,
        *,
        duration_seconds: float,
        sample_interval_seconds: float,
        workload_probe: Callable[[], dict[str, Any]],
        resource_probe: Callable[[], dict[str, Any]],
        reconnect_probe: Callable[[], None] | None = None,
        reconnect_interval_seconds: float | None = None,
        analysis_probe: Callable[[], None] | None = None,
        analysis_interval_seconds: float | None = None,
        thresholds: SoakThresholds | None = None,
        release_revision: str | None = None,
        working_tree_clean: bool | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if duration_seconds <= 0 or sample_interval_seconds <= 0:
            raise ValueError("duration and sample interval must be positive")
        self.duration = float(duration_seconds)
        self.sample_interval = float(sample_interval_seconds)
        self.workload_probe = workload_probe
        self.resource_probe = resource_probe
        self.reconnect_probe = reconnect_probe
        self.reconnect_interval = reconnect_interval_seconds
        self.analysis_probe = analysis_probe
        self.analysis_interval = analysis_interval_seconds
        self.thresholds = thresholds or SoakThresholds()
        self.release_revision = release_revision
        self.working_tree_clean = working_tree_clean
        self.monotonic = monotonic
        self.sleep = sleep

    @staticmethod
    def _growth(samples: list[dict[str, Any]], field_name: str) -> int | None:
        values = [sample.get(field_name) for sample in samples]
        values = [int(value) for value in values if value is not None]
        return (max(values) - values[0]) if values else None

    def run(self) -> OperationalSoakReport:
        started_wall = _utc_now()
        started = self.monotonic()
        next_sample = started
        next_reconnect = (
            started + self.reconnect_interval
            if self.reconnect_probe and self.reconnect_interval
            else None
        )
        next_analysis = (
            started + self.analysis_interval
            if self.analysis_probe and self.analysis_interval
            else None
        )
        report = OperationalSoakReport(
            started_at_utc=started_wall,
            finished_at_utc=started_wall,
            requested_duration_seconds=self.duration,
            observed_duration_seconds=0,
            host={
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            release_revision=self.release_revision,
            working_tree_clean=self.working_tree_clean,
        )
        consecutive = 0

        while True:
            now = self.monotonic()
            if now - started >= self.duration:
                break
            if now >= next_sample:
                report.workload_attempts += 1
                try:
                    status = self.workload_probe()
                    resources = self.resource_probe()
                    report.samples.append({"elapsed_seconds": now - started, **resources, "status": status})
                    consecutive = 0
                except Exception as exc:
                    report.workload_failures += 1
                    consecutive += 1
                    name = type(exc).__name__
                    report.sanitized_errors[name] = report.sanitized_errors.get(name, 0) + 1
                report.maximum_consecutive_failures = max(
                    report.maximum_consecutive_failures, consecutive
                )
                next_sample += self.sample_interval
            if next_reconnect is not None and now >= next_reconnect:
                report.reconnect_attempts += 1
                try:
                    self.reconnect_probe()
                except Exception as exc:
                    report.reconnect_failures += 1
                    name = type(exc).__name__
                    report.sanitized_errors[name] = report.sanitized_errors.get(name, 0) + 1
                next_reconnect += float(self.reconnect_interval)
            if next_analysis is not None and now >= next_analysis:
                report.analysis_attempts += 1
                try:
                    self.analysis_probe()
                except Exception as exc:
                    report.analysis_failures += 1
                    name = type(exc).__name__
                    report.sanitized_errors[name] = report.sanitized_errors.get(name, 0) + 1
                next_analysis += float(self.analysis_interval)
            wait_for = min(next_sample, next_reconnect or next_sample, next_analysis or next_sample)
            self.sleep(max(0.001, min(wait_for - self.monotonic(), 0.25)))

        report.observed_duration_seconds = self.monotonic() - started
        report.finished_at_utc = _utc_now()
        attempts = report.workload_attempts + report.reconnect_attempts + report.analysis_attempts
        failures = report.workload_failures + report.reconnect_failures + report.analysis_failures
        failure_rate = failures / attempts if attempts else 1.0
        rss_growth = self._growth(report.samples, "rss_bytes")
        thread_growth = self._growth(report.samples, "thread_count")
        handle_growth = self._growth(report.samples, "handle_count")
        logs_bounded = bool(report.samples)
        for sample in report.samples:
            max_bytes = sample.get("log_max_bytes")
            backup_count = sample.get("log_backup_count")
            if max_bytes is None or backup_count is None:
                logs_bounded = False
                break
            if sample.get("log_file_count", 0) > int(backup_count) + 1:
                logs_bounded = False
                break
            if sample.get("log_largest_file_bytes", 0) > int(max_bytes):
                logs_bounded = False
                break
        threshold = self.thresholds
        report.checks = {
            "minimum_duration": report.observed_duration_seconds >= threshold.minimum_duration_seconds,
            "samples_recorded": len(report.samples) >= 2,
            "failure_rate": failure_rate <= threshold.maximum_failure_rate,
            "consecutive_failures": report.maximum_consecutive_failures <= threshold.maximum_consecutive_failures,
            "rss_growth": rss_growth is not None and rss_growth <= threshold.maximum_rss_growth_bytes,
            "thread_growth": thread_growth is not None and thread_growth <= threshold.maximum_thread_growth,
            "handle_growth": handle_growth is None or handle_growth <= threshold.maximum_handle_growth,
            "logs_bounded": logs_bounded,
            "reconnect_exercised": report.reconnect_attempts > 0 and report.reconnect_failures == 0,
            "analysis_exercised": report.analysis_attempts > 0 and report.analysis_failures == 0,
        }
        report.status = "PASS" if all(report.checks.values()) else "FAIL"
        report.disclosures = [
            "This report contains measurements, not simulated acceptance evidence.",
            "Credentials, account identifiers, response bodies, and vendor error text are not recorded.",
            "A PASS applies only to the recorded host, workload, duration, and release state.",
        ]
        return report
