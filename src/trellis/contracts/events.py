"""Evaluation events (§49): what a judge scores a finished run from."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trellis.contracts.artifacts import EvidenceRef
from trellis.contracts.messages import AgentStatus


class AgentEvalEvent(BaseModel):
    """Emitted after every execution when evaluation events are enabled.

    Carries *references*, not payloads: an evaluator (Langfuse, DeepEval, a custom job)
    resolves them out-of-band, so enabling evaluation never widens what the harness holds
    in memory or sends over the wire.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    agent_id: str = Field(description="Id of the agent that ran.")
    agent_run_id: str = Field(description="Id of the finished run.")
    tenant_id: str = Field(description="Tenant the run belongs to.")
    trace_id: str | None = Field(default=None, description="Trace id of the run, if traced.")
    skills: list[str] = Field(default_factory=list, description="Ids of the skills it used.")
    request_ref: str | None = Field(
        default=None, description="Reference to the stored request (an artifact id or URI)."
    )
    result_ref: str | None = Field(
        default=None,
        description="Reference to the stored result (an artifact id or URI); the judge's "
        "feedback targets it when set, else the run.",
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence the answer was built on."
    )
    model_metadata: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One JSON object per model call (model, provider, usage), no payloads.",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="One JSON object per tool call made, no payloads."
    )
    status: AgentStatus = Field(default=AgentStatus.SUCCESS, description="How the run ended.")
    latency_ms: float = Field(default=0.0, description="Wall-clock duration in milliseconds.")
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Named numeric measurements of the run."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form JSON object for the evaluator."
    )
