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

    agent_id: str
    agent_run_id: str
    tenant_id: str
    trace_id: str | None = None
    skills: list[str] = Field(default_factory=list)
    request_ref: str | None = None
    result_ref: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    model_metadata: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    status: AgentStatus = AgentStatus.SUCCESS
    latency_ms: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
