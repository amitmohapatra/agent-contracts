"""``AgentRequest`` / ``AgentResponse`` (§6, §7): the harness's stable, serializable boundary.

Both are plain Pydantic models with no framework types in them, so they can be persisted,
queued, or (later) mapped onto A2A without touching agent code (§90).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from trellis.contracts.artifacts import (
    AgentWarning,
    ArtifactRef,
    Claim,
    EvidenceRef,
    MemoryObservation,
    RecommendedAction,
)
from trellis.contracts.context import AgentExecutionContext
from trellis.contracts.errors import AgentError


class AgentStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    #: suspended waiting for something outside the run — a human, typically — and expected
    #: to be resumed. Not a failure, and not a finished turn either.
    PAUSED = "PAUSED"

    @property
    def ok(self) -> bool:
        return self in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)


class AgentRequest(BaseModel):
    """What an agent was asked to do."""

    model_config = ConfigDict(frozen=True, extra="allow")

    request_id: str = Field(description="Id of the request; create() takes the context's.")
    objective: str | None = Field(
        default=None,
        description="What the agent is asked to achieve, in words; also the memory query.",
    )
    input: Any = Field(default=None, description="The agent's input, any JSON value, as given.")

    context: AgentExecutionContext = Field(
        description="Identity and lineage of the execution the request starts."
    )

    skills_requested: list[str] = Field(
        default_factory=list, description="Ids of the skills the caller wants used."
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Limits the caller sets (budget, allowed tools...), as a JSON object.",
    )

    artifact_refs: list[ArtifactRef] = Field(
        default_factory=list, description="Large inputs passed by reference."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence the caller supplies for the agent to use."
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object; RunStart.from_request copies it onto the run.",
    )

    @classmethod
    def create(cls, context: AgentExecutionContext, input: Any = None, **fields: Any) -> Self:
        return cls(request_id=context.request_id, context=context, input=input, **fields)

    def with_fields(self, **changes: Any) -> Self:
        """Interceptors return modified requests through this (the model is frozen)."""
        return self.model_copy(update=changes)

    @property
    def query(self) -> str | None:
        """The text a memory retrieval should use: the objective, else a string input."""
        if self.objective:
            return self.objective
        if isinstance(self.input, str):
            return self.input
        if isinstance(self.input, dict):
            for key in ("query", "question", "objective", "prompt", "input", "text"):
                value = self.input.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None


class AgentResponse(BaseModel):
    """What an agent produced. Adapters map this onto framework state — never the reverse."""

    model_config = ConfigDict(extra="allow")

    status: AgentStatus = Field(default=AgentStatus.SUCCESS, description="How the execution ended.")

    data: Any = Field(default=None, description="The agent's answer, any JSON value.")

    claims: list[Claim] = Field(
        default_factory=list, description="Assertions made, each with its evidence ids."
    )
    evidence: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence the claims cite."
    )
    artifacts: list[ArtifactRef] = Field(
        default_factory=list, description="Large outputs passed by reference."
    )
    memory_observations: list[MemoryObservation] = Field(
        default_factory=list, description="What to write to memory after the response."
    )
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list, description="Next steps the agent proposes."
    )

    confidence: float | None = Field(
        default=None,
        description="The agent's confidence in the answer, from 0 to 1 by convention.",
    )
    warnings: list[AgentWarning] = Field(
        default_factory=list, description="Non-fatal problems the caller should see."
    )
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Named numeric measurements of the execution."
    )

    error: AgentError | None = Field(
        default=None, description="The failure, for a status that is not ok; None otherwise."
    )

    @classmethod
    def ok(cls, data: Any = None, **fields: Any) -> Self:
        return cls(status=AgentStatus.SUCCESS, data=data, **fields)

    @classmethod
    def failed(
        cls, error: AgentError, *, status: AgentStatus = AgentStatus.ERROR, **fields: Any
    ) -> Self:
        return cls(status=status, error=error, **fields)

    @classmethod
    def coerce(cls, value: Any) -> Self:
        """Normalize whatever a developer's agent returned into an ``AgentResponse`` (§26).

        An ``AgentResponse`` passes through; anything else becomes ``data``. This is what lets
        an existing agent be wrapped without changing its return type.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, AgentResponse):  # subclass/superclass mix
            return cls.model_validate(value.model_dump())
        return cls(status=AgentStatus.SUCCESS, data=value)

    @property
    def succeeded(self) -> bool:
        return self.status.ok and self.error is None

    def with_fields(self, **changes: Any) -> Self:
        return self.model_copy(update=changes)

    def add_warning(self, code: str, message: str, **details: Any) -> Self:
        return self.model_copy(
            update={
                "warnings": [
                    *self.warnings,
                    AgentWarning(code=code, message=message, details=details),
                ]
            }
        )
