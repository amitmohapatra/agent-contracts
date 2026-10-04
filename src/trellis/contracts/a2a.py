"""The A2A Agent Card (protocol 1.0) as a mapping from the platform's own identity
(PLATFORM-DESIGN §9).

Generated from the Registry entity and its :class:`AgentDescriptor`; the card's JSON uses the
A2A spelling (camelCase), so :meth:`AgentCard.to_a2a` is what an A2A server publishes and
what a client reads back through :meth:`AgentCard.from_a2a`. A card read from another
agent is data, not instructions: unknown keys are dropped, and every URL must be http(s).
No ``a2a-sdk`` import here: the server and the client live in ``trellis-a2a``.
"""

from __future__ import annotations

from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from trellis.contracts.descriptors import AgentDescriptor, SkillDescriptor

#: The protocol version a served card declares. It tracks what the transport actually
#: speaks: ``a2a-sdk`` 1.x is protocol 1.0, and a card that claimed 0.3.0 while the wire
#: answered 1.0 would tell a caller to negotiate the wrong dialect. The harness's A2A surface
#: serves the SDK's own ``PROTOCOL_VERSION_CURRENT``, which must equal this.
A2A_PROTOCOL_VERSION = "1.0"
_TEXT_PLAIN = "text/plain"
_A2A = ConfigDict(frozen=True, extra="ignore", alias_generator=to_camel, validate_by_name=True)


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an http(s) URL")
    return value


class AgentCapabilities(BaseModel):
    model_config = _A2A

    streaming: bool = Field(
        default=False, description="Whether the agent streams task updates (SSE)."
    )
    push_notifications: bool = Field(
        default=False, description="Whether the agent can push task updates to a webhook."
    )
    state_transition_history: bool = Field(
        default=False, description="Whether the agent keeps and returns a task's state history."
    )
    extensions: list[dict[str, Any]] = Field(
        default_factory=list, description="A2A protocol extensions the agent supports, as JSON."
    )


class AgentSkill(BaseModel):
    model_config = _A2A

    id: str = Field(description="Id of the skill, unique within the card.")
    name: str = Field(description="Human-readable name of the skill.")
    description: str = Field(default="", description="What the skill does, for callers.")
    tags: list[str] = Field(default_factory=list, description="Keywords for finding the skill.")
    examples: list[str] = Field(
        default_factory=list, description="Example prompts the skill handles."
    )
    input_modes: list[str] | None = Field(
        default=None,
        description="Media types the skill accepts; None means the card's default_input_modes.",
        examples=[["text/plain", "application/json"]],
    )
    output_modes: list[str] | None = Field(
        default=None,
        description="Media types the skill returns; None means the card's default_output_modes.",
    )
    security: list[dict[str, list[str]]] | None = Field(
        default=None,
        description="Security requirements for this skill (scheme name to scopes, any one "
        "entry suffices); None means the card's security.",
    )

    @classmethod
    def from_descriptor(cls, skill: SkillDescriptor) -> Self:
        return cls(
            id=skill.skill_id,
            name=skill.skill_id,
            description=skill.description,
            tags=list(skill.tags),
        )


class AgentProvider(BaseModel):
    model_config = _A2A

    organization: str = Field(description="Name of the organization that runs the agent.")
    url: str = Field(description="The organization's website, an http(s) URL.")

    _url = field_validator("url")(_http_url)


class AgentInterface(BaseModel):
    """Another URL and transport the same agent answers on."""

    model_config = _A2A

    url: str = Field(description="Endpoint the agent answers on there, an http(s) URL.")
    transport: str = Field(
        description="Transport spoken at url (JSONRPC, GRPC or HTTP+JSON in A2A 1.0).",
        examples=["GRPC"],
    )

    _url = field_validator("url")(_http_url)


#: what a descriptor tells the platform about the agent, kept on the model (not on the
#: wire): the card metadata is ours, the card itself is the protocol's
_PLATFORM_FACTS = ("agent_group_id", "framework", "harness_version")


class AgentCard(BaseModel):
    """What another agent needs to call this one: where, what it can do, how to authenticate.
    ``metadata`` keeps the platform's own facts and is not published."""

    model_config = _A2A

    name: str = Field(description="Name of the agent (the descriptor's agent_id).")
    description: str = Field(default="", description="What the agent does, for callers.")
    url: str = Field(
        description="Endpoint for the preferred transport, an http(s) URL.",
        examples=["https://agents.example.com/a2a/refunds"],
    )
    version: str = Field(description="Version of the agent.", examples=["0.1.0"])
    protocol_version: str = Field(
        default=A2A_PROTOCOL_VERSION, description="A2A protocol version the card declares."
    )
    provider: AgentProvider | None = Field(
        default=None, description="Who runs the agent; None when unstated."
    )
    documentation_url: str | None = Field(
        default=None, description="The agent's documentation, an http(s) URL."
    )
    icon_url: str | None = Field(default=None, description="The agent's icon, an http(s) URL.")
    preferred_transport: str = Field(
        default="JSONRPC",
        description="Transport spoken at url (JSONRPC, GRPC or HTTP+JSON in A2A 1.0).",
    )
    additional_interfaces: list[AgentInterface] = Field(
        default_factory=list, description="Other URLs and transports the same agent answers on."
    )
    capabilities: AgentCapabilities = Field(
        default_factory=AgentCapabilities, description="Optional protocol features supported."
    )
    default_input_modes: list[str] = Field(
        default_factory=lambda: [_TEXT_PLAIN],
        description="Media types every skill accepts unless it says otherwise.",
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: [_TEXT_PLAIN],
        description="Media types every skill returns unless it says otherwise.",
    )
    skills: list[AgentSkill] = Field(
        default_factory=list, description="What the agent can be asked to do."
    )
    security_schemes: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Authentication schemes by name, as OpenAPI security scheme objects.",
    )
    security: list[dict[str, list[str]]] = Field(
        default_factory=list,
        description="Security requirements (scheme name to scopes); any one entry suffices.",
    )
    supports_authenticated_extended_card: bool = Field(
        default=False,
        description="Whether an authenticated caller can fetch a fuller card.",
    )
    signatures: list[dict[str, Any]] = Field(
        default_factory=list, description="JWS signatures over the card, as JSON."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        exclude=True,
        description="The platform's own facts about the agent (group, framework, harness "
        "version); never published.",
    )

    _url = field_validator("url")(_http_url)

    @field_validator("documentation_url", "icon_url")
    @classmethod
    def _optional_http_url(cls, value: str | None) -> str | None:
        return None if value is None else _http_url(value)

    @classmethod
    def from_descriptor(
        cls,
        descriptor: AgentDescriptor,
        *,
        url: str,
        capabilities: AgentCapabilities | None = None,
        provider: AgentProvider | None = None,
        security_schemes: dict[str, dict[str, Any]] | None = None,
        security: list[dict[str, list[str]]] | None = None,
    ) -> Self:
        return cls(
            name=descriptor.agent_id,
            description=descriptor.description,
            url=url,
            version=descriptor.version,
            provider=provider,
            capabilities=capabilities or AgentCapabilities(),
            skills=[AgentSkill.from_descriptor(skill) for skill in descriptor.skills],
            security_schemes=dict(security_schemes or {}),
            security=list(security or []),
            metadata={fact: getattr(descriptor, fact) for fact in _PLATFORM_FACTS},
        )

    def to_a2a(self) -> dict[str, Any]:
        """The card as A2A JSON (camelCase, unset optionals omitted, no platform metadata)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def from_a2a(cls, card: dict[str, Any]) -> Self:
        return cls.model_validate(card)

    def skill(self, skill_id: str) -> AgentSkill | None:
        return next((s for s in self.skills if s.id == skill_id), None)
