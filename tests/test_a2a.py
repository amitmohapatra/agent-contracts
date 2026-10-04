"""The A2A card's smaller parts, and the evaluation event a card's agent is judged from.
``test_contracts_v2.py`` covers the card as a whole."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trellis.contracts import (
    A2A_PROTOCOL_VERSION,
    AgentEvalEvent,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    AgentStatus,
    SkillDescriptor,
)


def test_a_skill_keeps_the_descriptor_text_and_tags() -> None:
    skill = AgentSkill.from_descriptor(
        SkillDescriptor(skill_id="billing.refund", description="refund an order", tags=["money"])
    )
    assert (skill.id, skill.name, skill.description, skill.tags) == (
        "billing.refund",
        "billing.refund",
        "refund an order",
        ["money"],
    )
    # modes and security are the protocol's optionals: absent rather than empty on the wire
    assert "inputModes" not in skill.model_dump(by_alias=True, exclude_none=True)


@pytest.mark.parametrize("url", ["ftp://acme.example", "https://", "acme.example", ""])
def test_every_url_on_a_card_part_is_http(url: str) -> None:
    with pytest.raises(ValidationError, match="http"):
        AgentProvider(organization="Acme", url=url)
    with pytest.raises(ValidationError, match="http"):
        AgentInterface(url=url, transport="GRPC")


def test_card_parts_read_either_spelling_and_write_camel_case() -> None:
    interface = AgentInterface.model_validate({"url": "http://a.example", "transport": "GRPC"})
    assert interface.model_dump(by_alias=True) == {"url": "http://a.example", "transport": "GRPC"}
    skill = AgentSkill.model_validate({"id": "s", "name": "s", "inputModes": ["text/plain"]})
    assert skill.input_modes == ["text/plain"]
    assert AgentSkill(id="s", name="s", output_modes=["application/json"]).model_dump(
        by_alias=True
    )["outputModes"] == ["application/json"]
    assert A2A_PROTOCOL_VERSION == "1.0"


def test_an_eval_event_carries_references_and_defaults_to_a_success() -> None:
    event = AgentEvalEvent(agent_id="a", agent_run_id="run_1", tenant_id="acme")
    assert event.status is AgentStatus.SUCCESS and event.latency_ms == 0.0
    assert event.result_ref is None and event.evidence_refs == [] and event.tool_calls == []
    with pytest.raises(ValidationError):
        event.status = AgentStatus.ERROR  # type: ignore[misc]
    assert AgentEvalEvent.model_validate_json(event.model_dump_json()) == event
