"""Tool contracts (§14, §89). Shaped so an MCP or gateway-backed client fits without change:
identity, schema, call, result, streaming, idempotency and authorization metadata."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trellis.contracts.artifacts import ArtifactRef

#: Where a tool comes from: a Python function (``local``), an MCP server behind the gateway
#: (``mcp``), the Memory Service's agent tools (``memory``), an OpenAPI operation
#: (``openapi``) or another agent over A2A (``a2a``).
ToolSource = Literal["local", "mcp", "memory", "openapi", "a2a"]


class ToolSpec(BaseModel):
    """What a tool is. ``server``/``source`` allow MCP servers and gateways to be identified."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str = Field(description="Name the model calls the tool by, unique within a run.")
    version: str = Field(default="1", description="Version of the tool.")
    description: str = Field(default="", description="What the tool does, shown to the model.")
    input_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema of the arguments; None when it takes none."
    )
    output_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema of the result; None when free-form."
    )
    tags: list[str] = Field(default_factory=list, description="Keywords for finding the tool.")
    source: ToolSource = Field(default="local", description="Where the tool comes from.")
    server: str | None = Field(
        default=None, description="MCP server (gateway client) providing it, for an MCP tool."
    )
    idempotent: bool = Field(
        default=False, description="Whether calling it twice with the same arguments is safe."
    )
    side_effects: str = Field(
        default="unknown",
        description="Risk tier: read, write or irreversible; unknown when the producer has "
        "not said.",
    )
    authorization: dict[str, Any] = Field(
        default_factory=dict,
        description="What calling it requires (scopes, approval rules), as a JSON object.",
    )

    def descriptor(self) -> dict[str, Any]:
        """The shape the Memory Service tool APIs accept for ``available_tools``."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "tags": list(self.tags),
        }


class ToolCall(BaseModel):
    """One call of a tool, as proposed or made."""

    model_config = ConfigDict(frozen=True, extra="allow")

    tool: str = Field(description="Name of the tool called.", examples=["refund"])
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments as a JSON object, unredacted.",
        examples=[{"order_id": 91, "amount_eur": 240}],
    )
    task: str = Field(default="", description="The task the call serves, in words.")
    step: int | None = Field(
        default=None, description="Position of the call within its run; None when unordered."
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Key that makes a repeated call run once; also the feedback target id.",
    )


class ToolStatus(StrEnum):
    """How a tool call ended. ``StrEnum`` so ``outcome.status == "ok"`` keeps working."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ToolOutcome(BaseModel):
    """The normalized result of a tool call."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    tool: str = Field(description="Name of the tool called.")
    status: ToolStatus = Field(default=ToolStatus.OK, description="How the call ended.")
    output: Any = Field(default=None, description="What the tool returned, any JSON value.")
    output_summary: str | None = Field(
        default=None, description="Short text summary of the output, for logs and memory."
    )
    artifacts: list[ArtifactRef] = Field(
        default_factory=list, description="Large outputs passed by reference."
    )
    cached: bool = Field(
        default=False, description="Whether the output was replayed rather than run again."
    )
    attempts: int = Field(default=1, description="How many times the call was tried.")
    latency_ms: float | None = Field(
        default=None, description="Wall-clock duration in milliseconds."
    )
    error_class: str | None = Field(
        default=None, description="Exception class name of a failed call; None on success."
    )
    invocation_id: str | None = Field(
        default=None, description="Id of the invocation, for correlating with the tool's logs."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form JSON object about the call."
    )

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.OK
