"""Agent and skill identity (§46): what a registry records about an agent, and what
:meth:`trellis.contracts.AgentCard.from_descriptor` maps onto the A2A card."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SkillDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    skill_id: str = Field(description="Id of the skill, unique within its agent.")
    version: str = Field(default="1.0.0", description="Version of the skill (semver).")
    description: str = Field(default="", description="What the skill does, for callers.")
    input_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema of the skill's input; None when free-form."
    )
    output_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema of the skill's output; None when free-form."
    )
    tags: list[str] = Field(default_factory=list, description="Keywords for finding the skill.")


class AgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    agent_id: str = Field(description="Id of the agent; the A2A card's name.")
    version: str = Field(default="0.1.0", description="Version of the agent (semver).")
    description: str = Field(default="", description="What the agent does, for callers.")
    agent_group_id: str | None = Field(default=None, description="Group of agents it belongs to.")
    skills: list[SkillDescriptor] = Field(
        default_factory=list, description="What the agent can be asked to do."
    )
    framework: str | None = Field(
        default=None,
        description="Framework the agent is built on, as the harness's adapter names it.",
        examples=["langgraph", "openai_agents", "function"],
    )
    framework_version: str | None = Field(default=None, description="Version of that framework.")
    harness_version: str | None = Field(
        default=None, description="Version of the harness running the agent."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form JSON object about the agent."
    )

    @property
    def skill_ids(self) -> list[str]:
        return [s.skill_id for s in self.skills]

    @classmethod
    def build(
        cls,
        agent_id: str,
        *,
        skills: list[str | SkillDescriptor] | None = None,
        **fields: Any,
    ) -> AgentDescriptor:
        """Accept plain skill ids (the common case) or full descriptors."""
        resolved = [SkillDescriptor(skill_id=s) if isinstance(s, str) else s for s in skills or []]
        return cls(agent_id=agent_id, skills=resolved, **fields)
