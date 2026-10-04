"""Human and judge feedback (PLATFORM-DESIGN §7, §11): stored separately from memory content,
in the Memory Service, so what people said about a run, an answer, a memory, a tool call, a
brief or a procedure has one table, and every learner (revision projector, tool memory,
offline datasets) reads it from there."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trellis.contracts.artifacts import EvidenceRef
from trellis.contracts.context import AgentExecutionContext
from trellis.contracts.ids import new_id, now

#: A score in [0, 1]; strict so ``True`` is not quietly ``1.0``.
Fraction = Annotated[float, Field(ge=0.0, le=1.0, strict=True)]


class FeedbackTargetKind(StrEnum):
    RUN = "run"
    ANSWER = "answer"
    MEMORY = "memory"
    TOOL_CALL = "tool_call"
    BRIEF = "brief"
    PROCEDURE = "procedure"


class FeedbackVerdict(StrEnum):
    #: the target is right (a memory is reinforced)
    CONFIRM = "confirm"
    #: the target is wrong (a memory is invalidated)
    REJECT = "reject"
    #: the target is wrong and ``correction`` says what is right (a memory is superseded)
    CORRECT = "correct"
    #: a tool call or action may proceed
    APPROVE = "approve"
    #: a tool call proceeds with ``correction`` as its arguments
    EDIT = "edit"


#: verdicts that say what is right instead; a record without the correction says nothing
_NEEDS_CORRECTION = frozenset({FeedbackVerdict.CORRECT, FeedbackVerdict.EDIT})


class FeedbackSource(StrEnum):
    HUMAN = "human"
    JUDGE = "judge"
    INTERRUPT = "interrupt"


#: the identity a record takes from the request it was given in; never overridable by a caller
_BOUND = ("tenant_id", "workspace_id", "user_id", "agent_id", "agent_run_id", "trace_id")


class Feedback(BaseModel):
    """One judgement about one target. ``score`` is the judge's number in [0, 1] when there
    is one; ``reviewer`` names the person or the judge (``judge:<model>``). The identity
    fields are what the store verifies against the authenticated caller: a record only says
    who it claims to be from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feedback_id: str = Field(
        default_factory=lambda: new_id("fb_"),
        description="Feedback id, 'fb_' plus 32 hex characters unless given; the store keeps "
        "one record per id.",
    )
    tenant_id: str = Field(description="Tenant the judged target belongs to.")
    workspace_id: str | None = Field(
        default=None, description="Workspace within the tenant; None when unused."
    )
    user_id: str | None = Field(
        default=None, description="User of the run the feedback was given in, if any."
    )
    agent_id: str | None = Field(default=None, description="Agent whose work is judged, if any.")
    agent_run_id: str | None = Field(
        default=None, description="Run the feedback was given in or is about, if any."
    )
    trace_id: str | None = Field(default=None, description="Trace id of that run, if traced.")
    target_kind: FeedbackTargetKind = Field(description="What kind of thing is judged.")
    target_id: str = Field(
        description="Id of the judged thing, of the kind target_kind names; not blank."
    )
    verdict: FeedbackVerdict = Field(
        description="The judgement. CORRECT and EDIT carry correction."
    )
    source: FeedbackSource = Field(
        default=FeedbackSource.HUMAN, description="Who judged: a person, a judge or an interrupt."
    )
    score: Fraction | None = Field(
        default=None, description="A judge's number in [0, 1], strictly a float; None for none."
    )
    correction: Any = Field(
        default=None,
        description="What is right instead (any JSON value); required for CORRECT and EDIT.",
    )
    comment: str | None = Field(default=None, description="Free-text remark for people.")
    reviewer: str | None = Field(
        default=None,
        description="Person (kind:id) or judge ('judge:<model or method>') who judged.",
        examples=["user:u1", "judge:grounded"],
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence the reviewer pointed at."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object; for a tool call, the tool, args and decision time.",
    )
    created_at: AwareDatetime = Field(
        default_factory=now,
        description="When it was given (ISO 8601, timezone-aware; UTC unless given).",
    )

    @field_validator("target_id")
    @classmethod
    def _target_is_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("target_id must name the thing judged")
        return value

    @model_validator(mode="after")
    def _a_correction_says_what_is_right(self) -> Self:
        if self.verdict in _NEEDS_CORRECTION and self.correction is None:
            raise ValueError(f"a {self.verdict.value} verdict needs a correction")
        return self

    @classmethod
    def for_context(
        cls,
        context: AgentExecutionContext,
        *,
        target_kind: FeedbackTargetKind,
        target_id: str,
        verdict: FeedbackVerdict,
        **fields: Any,
    ) -> Self:
        """Feedback bound to the identity of the request it was given in. The bound fields
        cannot be passed in ``fields``: a record must not claim an identity its request
        does not have."""
        claimed = sorted(set(fields) & set(_BOUND))
        if claimed:
            raise ValueError(f"{', '.join(claimed)} come from the context, not from the caller")
        return cls(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            agent_run_id=context.agent_run_id,
            trace_id=context.trace_id,
            target_kind=target_kind,
            target_id=target_id,
            verdict=verdict,
            **fields,
        )
