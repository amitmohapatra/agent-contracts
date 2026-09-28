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
#: answered 1.0 would tell a caller to negotiate the wrong dialect. The A2A surface pins
#: this against the SDK's own ``PROTOCOL_VERSION_CURRENT``.
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

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    extensions: list[dict[str, Any]] = Field(default_factory=list)


class AgentSkill(BaseModel):
    model_config = _A2A

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security: list[dict[str, list[str]]] | None = None

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

    organization: str
    url: str

    _url = field_validator("url")(_http_url)


class AgentInterface(BaseModel):
    """Another URL and transport the same agent answers on."""

    model_config = _A2A

    url: str
    transport: str

    _url = field_validator("url")(_http_url)


#: what a descriptor tells the platform about the agent, kept on the model (not on the
#: wire): the card metadata is ours, the card itself is the protocol's
_PLATFORM_FACTS = ("agent_group_id", "framework", "harness_version")


class AgentCard(BaseModel):
    """What another agent needs to call this one: where, what it can do, how to authenticate.
    ``metadata`` keeps the platform's own facts and is not published."""

    model_config = _A2A

    name: str
    description: str = ""
    url: str
    version: str
    protocol_version: str = A2A_PROTOCOL_VERSION
    provider: AgentProvider | None = None
    documentation_url: str | None = None
    icon_url: str | None = None
    preferred_transport: str = "JSONRPC"
    additional_interfaces: list[AgentInterface] = Field(default_factory=list)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = Field(default_factory=lambda: [_TEXT_PLAIN])
    default_output_modes: list[str] = Field(default_factory=lambda: [_TEXT_PLAIN])
    skills: list[AgentSkill] = Field(default_factory=list)
    security_schemes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    security: list[dict[str, list[str]]] = Field(default_factory=list)
    supports_authenticated_extended_card: bool = False
    signatures: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)

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
