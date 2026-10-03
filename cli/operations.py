"""Operational acceptance commands for a running local dashboard."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests
import typer

from tradingagents.operations import (
    CHECKLIST,
    OperationalSoakRunner,
    SoakThresholds,
    XMAcceptanceRecord,
)

app = typer.Typer(help="Run measurable operational acceptance checks.")
xm_app = typer.Typer(help="Record revision-locked XM demo lifecycle evidence.")
app.add_typer(xm_app, name="xm-acceptance")


def _git_release_state() -> tuple[str | None, bool | None]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        return revision or None, not bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        return None, None


@xm_app.command("start")
def xm_acceptance_start(
    tester: str = typer.Option(..., help="Human tester name or stable identifier."),
    output: Path = typer.Option(Path("artifacts/xm-acceptance.json")),  # noqa: B008
) -> None:
    """Start an acceptance record locked to the current Git revision."""
    revision, clean = _git_release_state()
    if revision is None or clean is None:
        raise typer.BadParameter("run this command from a Git working tree")
    record = XMAcceptanceRecord.create(
        release_revision=revision,
        working_tree_clean=clean,
        tester=tester,
    )
    destination = record.write(output)
    typer.echo(f"XM acceptance record: {destination}")
    typer.echo(f"Release revision: {revision}")
    typer.echo(f"Working tree clean: {clean}")
    if not clean:
        typer.echo("Acceptance cannot pass until repeated from a clean release revision.")


@xm_app.command("record")
def xm_acceptance_record(
    evidence: Path = typer.Option(..., exists=True, dir_okay=False),  # noqa: B008
    step: int = typer.Option(..., min=1, max=25),
    status: str = typer.Option("PASS", help="PASS, FAIL, or SKIP where allowed."),
    kind: str = typer.Option(..., help="SYSTEM or HUMAN, as required by the checklist."),
    actor: str = typer.Option(..., help="Tester or verifier recording the evidence."),
    summary: str = typer.Option(..., help="Sanitized evidence summary; never include credentials."),
) -> None:
    """Record one ordered checklist result without weakening its evidence type."""
    revision, _ = _git_release_state()
    if revision is None:
        raise typer.BadParameter("current Git revision is unavailable")
    record = XMAcceptanceRecord.load(evidence)
    try:
        record.record(
            step_number=step,
            status=status.upper(),  # type: ignore[arg-type]
            evidence_kind=kind.upper(),  # type: ignore[arg-type]
            actor=actor,
            summary=summary,
            release_revision=revision,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    record.write(evidence)
    typer.echo(f"Step {step}: {record.steps[str(step)].status}")
    typer.echo(f"XM demo accepted: {record.accepted}")


@xm_app.command("status")
def xm_acceptance_status(
    evidence: Path = typer.Option(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Show the next incomplete item and truthful acceptance state."""
    record = XMAcceptanceRecord.load(evidence)
    state = record.summary()
    typer.echo(f"Release revision: {state['release_revision']}")
    typer.echo(f"Working tree clean: {state['working_tree_clean']}")
    typer.echo(f"XM demo accepted: {state['accepted']}")
    typer.echo(f"Counts: {state['counts']}")
    next_step = state["next_step"]
    if next_step is not None:
        definition = next(step for step in CHECKLIST if step.number == next_step)
        typer.echo(f"Next step {next_step}: {definition.title} [{definition.evidence_kind}]")


class DashboardProbe:
    def __init__(self, base_url: str, api_key: str | None, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        response = self.session.request(
            method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Dashboard returned a non-object response")
        return value

    def workload(self) -> dict[str, Any]:
        ready = self._request("GET", "/api/health/ready")
        status = self._request("GET", "/api/forex/mt5/status")
        self._request("GET", "/api/forex/mt5/account")
        self._request("GET", "/api/forex/mt5/positions")
        self._request("GET", "/api/forex/mt5/orders")
        self._request("GET", "/api/forex/mt5/deals")
        result = {
            "readiness": ready.get("status"),
            "mt5_connected": bool(status.get("is_connected") or status.get("connected")),
            "observation_running": bool(status.get("service_running")),
        }
        if result != {
            "readiness": "ready",
            "mt5_connected": True,
            "observation_running": True,
        }:
            raise RuntimeError("Dashboard, MT5, or observation service is not ready")
        return result

    def resources(self) -> dict[str, Any]:
        return self._request("GET", "/api/health/resources")

    def reconnect(self) -> None:
        disconnected = self._request("POST", "/api/forex/mt5/disconnect")
        if disconnected.get("status") == "STOPPING":
            raise RuntimeError("MT5 observation service did not stop within its bound")
        connected = self._request("POST", "/api/forex/mt5/connect", json={})
        if not connected.get("connected"):
            raise RuntimeError("MT5 reconnect did not establish a connection")

    def analyze(self, payload: dict[str, Any]) -> None:
        started = self._request("POST", "/api/forex/analyze", json=payload)
        if not started.get("run_id"):
            raise RuntimeError("Analysis was not queued")


@app.command("soak-dashboard")
def soak_dashboard(
    base_url: str = typer.Option("http://127.0.0.1:8050", help="Running dashboard URL."),
    duration_hours: float = typer.Option(4.0, min=0.01, help="Planned soak duration."),
    sample_seconds: float = typer.Option(30.0, min=1.0, help="Resource/workload sample interval."),
    reconnect_minutes: float = typer.Option(30.0, min=0.01, help="MT5 reconnect interval."),
    analysis_minutes: float = typer.Option(30.0, min=0.01, help="Analysis launch interval."),
    analysis_payload: Path = typer.Option(..., exists=True, dir_okay=False, help="Forex analysis request JSON."),  # noqa: B008
    output: Path = typer.Option(Path("artifacts/operational-soak.json"), help="Sanitized JSON evidence path."),  # noqa: B008
    api_key_env: str = typer.Option("TRADINGAGENTS_DASHBOARD_API_KEY", help="Environment variable containing the dashboard key."),
    request_timeout_seconds: float = typer.Option(30.0, min=1.0),
) -> None:
    """Exercise observation, analysis and reconnect while measuring the dashboard."""
    try:
        payload = json.loads(analysis_payload.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter("analysis-payload must be readable JSON") from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter("analysis-payload must contain one JSON object")

    probe = DashboardProbe(base_url, os.environ.get(api_key_env), request_timeout_seconds)
    revision, clean = _git_release_state()
    runner = OperationalSoakRunner(
        duration_seconds=duration_hours * 3600,
        sample_interval_seconds=sample_seconds,
        workload_probe=probe.workload,
        resource_probe=probe.resources,
        reconnect_probe=probe.reconnect,
        reconnect_interval_seconds=reconnect_minutes * 60,
        analysis_probe=lambda: probe.analyze(payload),
        analysis_interval_seconds=analysis_minutes * 60,
        thresholds=SoakThresholds(),
        release_revision=revision,
        working_tree_clean=clean,
    )
    report = runner.run()
    destination = report.write_json(output)
    typer.echo(f"Operational soak: {report.status}")
    typer.echo(f"Evidence: {destination}")
    for name, passed in report.checks.items():
        typer.echo(f"{'PASS' if passed else 'FAIL'} {name}")
    if report.status != "PASS":
        raise typer.Exit(code=1)
