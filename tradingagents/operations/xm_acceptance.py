"""Revision-locked evidence state for the manual XM demo acceptance workflow."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

StepStatus = Literal["PENDING", "PASS", "FAIL", "SKIP"]
EvidenceKind = Literal["SYSTEM", "HUMAN"]

_SENSITIVE = re.compile(
    r"(?i)(password|passwd|api[_ -]?key|secret|authorization\s*:|bearer\s+|login\s*=\s*\d{4,})"
)


@dataclass(frozen=True)
class ChecklistDefinition:
    number: int
    title: str
    evidence_kind: EvidenceKind
    depends_on: tuple[int, ...] = ()
    skippable: bool = False


CHECKLIST: tuple[ChecklistDefinition, ...] = (
    ChecklistDefinition(1, "XM MT5 launched and demo account logged in", "HUMAN"),
    ChecklistDefinition(2, "Dashboard started from audited revision", "SYSTEM", (1,)),
    ChecklistDefinition(3, "Read-only MT5 integration connected", "SYSTEM", (2,)),
    ChecklistDefinition(4, "Masked account and server verified", "SYSTEM", (3,)),
    ChecklistDefinition(5, "Positions, pending orders and deals visible", "SYSTEM", (4,)),
    ChecklistDefinition(6, "Forex analysis launched", "SYSTEM", (5,)),
    ChecklistDefinition(7, "Execution and context timeframes reached report", "SYSTEM", (6,)),
    ChecklistDefinition(8, "Decision and proposal output verified", "SYSTEM", (7,)),
    ChecklistDefinition(9, "Deterministic risk and sizing evidence verified", "SYSTEM", (8,)),
    ChecklistDefinition(10, "No broker order was sent by TradingAgents", "HUMAN", (9,)),
    ChecklistDefinition(11, "Tiny demo trade placed manually in XM MT5", "HUMAN", (10,)),
    ChecklistDefinition(12, "Observer detected position without modifying it", "SYSTEM", (11,)),
    ChecklistDefinition(13, "Proposal match or manual classification verified", "SYSTEM", (12,)),
    ChecklistDefinition(14, "Stop loss modified manually in XM MT5", "HUMAN", (13,)),
    ChecklistDefinition(15, "Stop-loss journal event recorded", "SYSTEM", (14,)),
    ChecklistDefinition(16, "Partial close exercised or unsupported mode recorded", "HUMAN", (15,), True),
    ChecklistDefinition(17, "Remaining position closed manually", "HUMAN", (16,)),
    ChecklistDefinition(18, "Exit deals and realized costs recorded", "SYSTEM", (17,)),
    ChecklistDefinition(19, "Holding history and MFE/MAE calculated", "SYSTEM", (18,)),
    ChecklistDefinition(20, "Post-close reflection reached explicit outcome", "SYSTEM", (19,)),
    ChecklistDefinition(21, "Structured lessons persisted", "SYSTEM", (20,)),
    ChecklistDefinition(22, "Lesson navigates to source trade", "SYSTEM", (21,)),
    ChecklistDefinition(23, "Analytics views updated", "SYSTEM", (22,)),
    ChecklistDefinition(24, "Dashboard restarted", "HUMAN", (23,)),
    ChecklistDefinition(25, "Journal and learning evidence persisted after restart", "SYSTEM", (24,)),
)
_BY_NUMBER = {step.number: step for step in CHECKLIST}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    if _SENSITIVE.search(value):
        raise ValueError(f"{field_name} appears to contain sensitive information")
    return value


@dataclass
class ChecklistEvidence:
    status: StepStatus = "PENDING"
    evidence_kind: EvidenceKind | None = None
    recorded_at_utc: str | None = None
    actor: str | None = None
    summary: str | None = None


@dataclass
class XMAcceptanceRecord:
    release_revision: str
    working_tree_clean: bool
    tester: str
    started_at_utc: str = field(default_factory=_now)
    updated_at_utc: str = field(default_factory=_now)
    steps: dict[str, ChecklistEvidence] = field(
        default_factory=lambda: {str(step.number): ChecklistEvidence() for step in CHECKLIST}
    )

    @classmethod
    def create(cls, *, release_revision: str, working_tree_clean: bool, tester: str):
        revision = release_revision.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError("release_revision must be a full 40-character Git commit")
        return cls(
            release_revision=revision.lower(),
            working_tree_clean=bool(working_tree_clean),
            tester=_safe_text(tester, "tester"),
        )

    @classmethod
    def load(cls, source: str | Path) -> XMAcceptanceRecord:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
        data["steps"] = {
            key: ChecklistEvidence(**value) for key, value in data.get("steps", {}).items()
        }
        record = cls(**data)
        missing = {str(step.number) for step in CHECKLIST} - set(record.steps)
        if missing:
            raise ValueError(f"Acceptance record is missing checklist steps: {sorted(missing)}")
        return record

    def record(
        self,
        *,
        step_number: int,
        status: StepStatus,
        evidence_kind: EvidenceKind,
        actor: str,
        summary: str,
        release_revision: str,
    ) -> None:
        if step_number not in _BY_NUMBER:
            raise ValueError("step_number must be between 1 and 25")
        definition = _BY_NUMBER[step_number]
        status = status.upper()  # type: ignore[assignment]
        if status not in {"PASS", "FAIL", "SKIP"}:
            raise ValueError("status must be PASS, FAIL or SKIP")
        if release_revision.lower() != self.release_revision:
            raise ValueError("evidence revision does not match the acceptance session")
        if evidence_kind != definition.evidence_kind:
            raise ValueError(
                f"step {step_number} requires {definition.evidence_kind} evidence"
            )
        if status == "SKIP" and not definition.skippable:
            raise ValueError(f"step {step_number} cannot be skipped")
        if status in {"PASS", "SKIP"}:
            incomplete = [
                dependency
                for dependency in definition.depends_on
                if self.steps[str(dependency)].status not in {"PASS", "SKIP"}
            ]
            if incomplete:
                raise ValueError(f"step {step_number} depends on incomplete steps {incomplete}")
        self.steps[str(step_number)] = ChecklistEvidence(
            status=status,
            evidence_kind=evidence_kind,
            recorded_at_utc=_now(),
            actor=_safe_text(actor, "actor"),
            summary=_safe_text(summary, "summary"),
        )
        self.updated_at_utc = _now()

    @property
    def accepted(self) -> bool:
        return self.working_tree_clean and all(
            self.steps[str(step.number)].status == "PASS"
            or (step.skippable and self.steps[str(step.number)].status == "SKIP")
            for step in CHECKLIST
        )

    def summary(self) -> dict[str, object]:
        counts = dict.fromkeys(("PENDING", "PASS", "FAIL", "SKIP"), 0)
        for evidence in self.steps.values():
            counts[evidence.status] += 1
        return {
            "accepted": self.accepted,
            "release_revision": self.release_revision,
            "working_tree_clean": self.working_tree_clean,
            "counts": counts,
            "next_step": next(
                (step.number for step in CHECKLIST if self.steps[str(step.number)].status == "PENDING"),
                None,
            ),
        }

    def write(self, destination: str | Path) -> Path:
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path
