"""What a judge says about a run (PLATFORM-DESIGN §11). The grounded method runs first
(citation validation and NLI in the Memory Service); an LLM judge spends tokens only on what
that cannot decide. A verdict becomes a score on the trace and a judge feedback record."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trellis.contracts.artifacts import EvidenceRef
from trellis.contracts.events import AgentEvalEvent
from trellis.contracts.feedback import (
    Feedback,
    FeedbackSource,
    FeedbackTargetKind,
    FeedbackVerdict,
    Fraction,
)


class JudgeMethod(StrEnum):
    #: deterministic: citations resolved, claims checked against evidence (``/v1/verify``)
    GROUNDED = "grounded"
    #: a model read the answer against a rubric
    LLM = "llm"


class JudgeVerdict(BaseModel):
    """A score in [0, 1] with how it was reached and what it cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: Fraction
    method: JudgeMethod
    label: str | None = None
    rationale: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def reviewer(self) -> str:
        return f"judge:{self.model or self.method.value}"

    def as_feedback(self, event: AgentEvalEvent, *, threshold: float = 0.5) -> Feedback:
        """The feedback record of this verdict on the run the event describes: a score at or
        above ``threshold`` confirms the answer, below it rejects it. The target is the
        result artifact when the event names one, else the run; ``metadata.target`` says
        which, so records on the two id spaces can be told apart."""
        verdict = FeedbackVerdict.CONFIRM if self.score >= threshold else FeedbackVerdict.REJECT
        target = "result" if event.result_ref else "run"
        return Feedback(
            tenant_id=event.tenant_id,
            agent_id=event.agent_id,
            agent_run_id=event.agent_run_id,
            trace_id=event.trace_id,
            target_kind=FeedbackTargetKind.ANSWER,
            target_id=event.result_ref or event.agent_run_id,
            verdict=verdict,
            source=FeedbackSource.JUDGE,
            score=self.score,
            comment=self.rationale,
            reviewer=self.reviewer,
            evidence_refs=list(self.evidence_refs),
            metadata={
                **self.metadata,
                "method": self.method.value,
                "label": self.label,
                "target": target,
            },
        )
