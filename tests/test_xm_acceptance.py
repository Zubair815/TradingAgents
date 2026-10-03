from __future__ import annotations

import json
import re

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app
from tradingagents.operations.xm_acceptance import CHECKLIST, XMAcceptanceRecord

REVISION = "a" * 40


def make_record(*, clean: bool = True) -> XMAcceptanceRecord:
    return XMAcceptanceRecord.create(
        release_revision=REVISION,
        working_tree_clean=clean,
        tester="acceptance-tester",
    )


def record_step(record: XMAcceptanceRecord, number: int, status: str = "PASS") -> None:
    definition = next(step for step in CHECKLIST if step.number == number)
    record.record(
        step_number=number,
        status=status,
        evidence_kind=definition.evidence_kind,
        actor="acceptance-tester",
        summary=f"Sanitized evidence for checklist item {number}",
        release_revision=REVISION,
    )


def test_acceptance_requires_ordered_complete_clean_revision():
    record = make_record()
    for definition in CHECKLIST:
        record_step(record, definition.number, "SKIP" if definition.skippable else "PASS")

    assert record.accepted
    assert record.summary()["counts"] == {
        "PENDING": 0,
        "PASS": 24,
        "FAIL": 0,
        "SKIP": 1,
    }


def test_dirty_revision_can_never_be_accepted():
    record = make_record(clean=False)
    for definition in CHECKLIST:
        record_step(record, definition.number, "SKIP" if definition.skippable else "PASS")
    assert not record.accepted


def test_out_of_order_wrong_evidence_and_revision_mismatch_fail_closed():
    record = make_record()
    with pytest.raises(ValueError, match="incomplete"):
        record_step(record, 2)
    with pytest.raises(ValueError, match="requires HUMAN"):
        record.record(
            step_number=1,
            status="PASS",
            evidence_kind="SYSTEM",
            actor="tester",
            summary="Terminal observed",
            release_revision=REVISION,
        )
    with pytest.raises(ValueError, match="does not match"):
        record.record(
            step_number=1,
            status="PASS",
            evidence_kind="HUMAN",
            actor="tester",
            summary="Terminal observed",
            release_revision="b" * 40,
        )


def test_only_partial_close_step_can_be_skipped():
    record = make_record()
    record_step(record, 1)
    with pytest.raises(ValueError, match="cannot be skipped"):
        record.record(
            step_number=2,
            status="SKIP",
            evidence_kind="SYSTEM",
            actor="tester",
            summary="Not performed",
            release_revision=REVISION,
        )


@pytest.mark.parametrize(
    "unsafe",
    ["password=hunter2", "Authorization: Bearer abc", "API key supplied", "login=12345678"],
)
def test_sensitive_evidence_text_is_rejected(unsafe):
    record = make_record()
    with pytest.raises(ValueError, match="sensitive"):
        record.record(
            step_number=1,
            status="PASS",
            evidence_kind="HUMAN",
            actor="tester",
            summary=unsafe,
            release_revision=REVISION,
        )


def test_atomic_round_trip_and_next_step(tmp_path):
    record = make_record()
    record_step(record, 1)
    path = record.write(tmp_path / "evidence" / "xm.json")
    loaded = XMAcceptanceRecord.load(path)

    assert loaded.release_revision == REVISION
    assert loaded.steps["1"].status == "PASS"
    assert loaded.summary()["next_step"] == 2
    assert not path.with_suffix(".json.tmp").exists()
    assert "hunter2" not in json.dumps(loaded.summary())


def test_cli_exposes_xm_acceptance_workflow():
    result = CliRunner().invoke(
        app,
        ["operations", "xm-acceptance", "--help"],
        env={"NO_COLOR": "1", "COLUMNS": "200"},
    )
    assert result.exit_code == 0
    clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.output)
    assert "start" in clean_output
    assert "record" in clean_output
    assert "status" in clean_output

    # Directly verify registered subcommands on the underlying Typer/Click command
    click_command = typer.main.get_command(app)
    operations_cmd = click_command.get_command(None, "operations")
    assert operations_cmd is not None
    xm_cmd = operations_cmd.get_command(None, "xm-acceptance")
    assert xm_cmd is not None
    assert set(xm_cmd.commands.keys()).issuperset({"start", "record", "status"})
