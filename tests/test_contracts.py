"""The contract, exercised as a standalone package.

This exists as its own distribution for one reason: a team must be able to conform to the
standard without installing a runtime. So the first test is the important one — if anything
here grows a dependency on the harness, telemetry or a memory client, the package has stopped
being adoptable and this fails.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from universal_agent_contracts import (
    AgentDescriptor,
    AgentExecutionContext,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    Claim,
    EvidenceRef,
    MemoryObservation,
    RecommendedAction,
    SkillDescriptor,
)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "universal_agent_contracts"

#: Everything this package may import. Deliberately tiny.
ALLOWED = {
    "universal_agent_contracts",
    "pydantic",
    "typing",
    "collections",
    "datetime",
    "enum",
    "uuid",
    "json",
    "re",
    "os",
    "sys",
    "abc",
    "dataclasses",
    "functools",
    "types",
    "contextlib",
    "__future__",
    "hashlib",
    "time",
    "math",
    "asyncio",
    "builtins",
}


def test_the_package_depends_on_nothing_but_pydantic() -> None:
    """A standard that drags a runtime behind it is one team's library, not a standard."""
    offenders: dict[str, set[str]] = {}
    for f in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = {node.module.split(".")[0]}
            else:
                continue
            if bad := mods - ALLOWED:
                offenders.setdefault(f.name, set()).update(bad)
    assert not offenders, f"contracts must stay dependency-free: {offenders}"


def test_a_request_carries_its_scope() -> None:
    """A request without a context has no tenant, so it cannot be authorised or attributed."""
    context = AgentExecutionContext.create(tenant_id="acme", agent_id="triage")
    request = AgentRequest(
        request_id=context.request_id, context=context, objective="summarise the incident"
    )
    assert request.context.tenant_id == "acme"
    assert request.objective == "summarise the incident"


def test_a_response_carries_its_evidence() -> None:
    """`claims` + `evidence` are why an aggregator can adjudicate rather than concatenate."""
    response = AgentResponse(
        status=AgentStatus.SUCCESS,
        data="revenue fell 4%",
        claims=[Claim(claim_id="c1", text="revenue fell 4%", confidence=0.9)],
        evidence=[EvidenceRef(source_id="chunk_1", kind="chunk")],
    )
    assert response.status.ok
    assert response.claims[0].text == "revenue fell 4%"
    assert response.evidence[0].source_id == "chunk_1"


def test_a_child_context_inherits_the_tree_and_narrows_the_deadline() -> None:
    """Sub-agent calls must not restart the clock, or a deadline means nothing at depth."""
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    parent = AgentExecutionContext.create(tenant_id="acme", agent_id="planner", deadline=deadline)
    child = parent.for_agent("worker")

    assert child.parent_agent_run_id == parent.agent_run_id
    assert child.causation_id == parent.agent_run_id
    assert child.tenant_id == parent.tenant_id
    assert child.agent_run_id != parent.agent_run_id
    assert child.deadline is not None and child.deadline <= deadline


def test_a_descriptor_says_what_an_agent_can_do() -> None:
    descriptor = AgentDescriptor(
        agent_id="billing",
        version="1.2.0",
        skills=[SkillDescriptor(skill_id="billing"), SkillDescriptor(skill_id="sql")],
    )
    assert {s.skill_id for s in descriptor.skills} == {"billing", "sql"}


def test_a_recommended_action_is_how_an_agent_asks_for_help() -> None:
    """The alternative is agents calling each other directly, which has no audit point."""
    action = RecommendedAction(action_type="query_sql", description="fetch FY26 revenue")
    response = AgentResponse(status=AgentStatus.SUCCESS, recommended_actions=[action])
    assert response.recommended_actions[0].action_type == "query_sql"


def test_a_memory_observation_is_a_suggestion_not_an_instruction() -> None:
    """The service's admission gate decides what is kept; the agent only proposes."""
    observation = MemoryObservation(content="Priya owns the rollback plan", kind="AGENT_RESULT")
    assert observation.kind == "AGENT_RESULT"


@pytest.mark.parametrize("status", list(AgentStatus))
def test_every_status_answers_whether_it_succeeded(status: AgentStatus) -> None:
    """Callers branch on this; a status that cannot answer is a bug waiting to happen."""
    assert isinstance(status.ok, bool)
