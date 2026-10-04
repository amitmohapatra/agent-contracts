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

    score: Fraction = Field(
        description="The judge's score in [0, 1]; at or above the threshold confirms the answer."
    )
    method: JudgeMethod = Field(description="How the score was reached.")
    label: str | None = Field(
        default=None,
        description="Short verdict label the judge gave, if any.",
        examples=["grounded", "unsupported"],
    )
    rationale: str | None = Field(
        default=None, description="Concise reason for the score; becomes the feedback comment."
    )
    model: str | None = Field(
        default=None, description="Model that judged, for an LLM verdict; None for GROUNDED."
    )
    cost_usd: float | None = Field(
        default=None, description="What judging cost, in US dollars; None when unknown."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence the judge checked the answer against."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form JSON object copied into the feedback."
    )

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
