"""References, not payloads (§43). Large data leaves the result and becomes an ``ArtifactRef``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactRef(BaseModel):
    """A pointer to content stored outside the result/state."""

    model_config = ConfigDict(frozen=True, extra="allow")

    artifact_id: str = Field(
        description="Id of the stored content in the store that holds it.",
        examples=["art_5d41402abc4b2a76b9719d91"],
    )
    type: str = Field(
        default="blob",
        description="What kind of content it is; 'blob' (opaque bytes, described by "
        "mime_type) unless the producer says otherwise.",
    )
    uri: str | None = Field(
        default=None,
        description="Where to fetch the content (for agent-runs, its download route); None "
        "when only the id is known.",
        examples=["/v1/artifacts/art_5d41402abc4b2a76b9719d91"],
    )
    mime_type: str | None = Field(
        default=None, description="Media type of the content.", examples=["application/json"]
    )
    checksum: str | None = Field(
        default=None,
        description="Digest of the content as algorithm:hex, to verify a download.",
        examples=["sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"],
    )
    size_bytes: int | None = Field(default=None, description="Size of the content in bytes.")
    created_at: datetime | None = Field(
        default=None, description="When the content was stored (ISO 8601, timezone-aware)."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object about the content (e.g. the run it belongs to).",
    )


class EvidenceRef(BaseModel):
    """Where a claim came from. Mirrors the Memory Service evidence shape."""

    model_config = ConfigDict(frozen=True, extra="allow")

    source_type: str = Field(
        default="memory",
        description="Kind of source, as the Memory Service names it (memory, message, file, "
        "document_chunk, agent_result, tool_result, import).",
    )
    source_id: str = Field(description="Id of the source object; also the evidence id.")
    message_id: str | None = Field(
        default=None, description="Message the evidence was taken from, if any."
    )
    document_id: str | None = Field(
        default=None, description="Document the evidence was taken from, if any."
    )
    chunk_id: str | None = Field(
        default=None, description="Document chunk the evidence was taken from, if any."
    )
    page: int | None = Field(
        default=None, description="Page of the document the evidence is on, when it has pages."
    )
    citation: str | None = Field(
        default=None, description="Human-readable citation or quoted text to show a reader."
    )
    observed_at: datetime | None = Field(
        default=None,
        description="When the source was observed (ISO 8601, timezone-aware), if known.",
    )

    @property
    def evidence_id(self) -> str:
        return self.source_id


class Claim(BaseModel):
    """One assertion an agent made, with the evidence that supports it (§44)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    claim_id: str = Field(description="Id of the claim, unique within the response.")
    text: str = Field(description="The assertion, as stated to the reader.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the evidence supporting it (EvidenceRef.source_id); empty when "
        "unsupported.",
    )
    confidence: float | None = Field(
        default=None, description="The agent's confidence in it, from 0 to 1 by convention."
    )


class RecommendedAction(BaseModel):
    """A next step the agent proposes. Concise rationale only — never chain-of-thought (§45)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    action_type: str = Field(
        description="Short machine-readable name of the proposed action.",
        examples=["send_email", "open_ticket"],
    )
    description: str = Field(description="What the action does, for a person to read.")
    reason_summary: str | None = Field(
        default=None, description="One concise sentence of why; never chain-of-thought."
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the evidence behind it (EvidenceRef.source_id).",
    )
    confidence: float | None = Field(
        default=None,
        description="The agent's confidence the action is right, from 0 to 1 by convention.",
    )


#: The observation kinds the Memory Service accepts (its ``ObservationKind`` enum). Sending
#: anything else is rejected with a 422, so the harness validates before the wire rather
#: than letting a typo become a runtime failure in the writeback path.
ObservationKind = Literal[
    "MESSAGE", "FILE", "AGENT_RESULT", "TOOL_RESULT", "DECISION", "FEEDBACK", "EVENT", "IMPORT"
]
#: :data:`ObservationKind` as a set.
OBSERVATION_KINDS: frozenset[str] = frozenset(get_args(ObservationKind))


class MemoryObservation(BaseModel):
    """Something the agent wants remembered. Written after the result is returned."""

    model_config = ConfigDict(frozen=True, extra="allow")

    content: str = Field(description="The text to remember.")
    kind: ObservationKind = Field(
        default="AGENT_RESULT",
        description="Memory Service observation kind; anything else is refused before the wire.",
    )
    hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Extraction hints passed to the Memory Service as is (JSON object).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object stored with the observation.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Key that makes a retried write store the observation once; None for none.",
    )

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, value: Any) -> Any:
        # before the Literal check, so the refusal lists what the Memory Service accepts
        if value not in OBSERVATION_KINDS:
            raise ValueError(
                f"unknown observation kind {value!r}; the Memory Service accepts "
                f"{', '.join(sorted(OBSERVATION_KINDS))}"
            )
        return value


class AgentWarning(BaseModel):
    """A non-fatal problem the caller should see (degraded memory, partial tool failure...)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    code: str = Field(description="Machine-readable warning code.", examples=["memory_degraded"])
    message: str = Field(description="What went wrong, for a person to read.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Structured context as a JSON object."
    )
