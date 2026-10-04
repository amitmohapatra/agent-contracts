"""The request and response of one execution, and what a response carries on the way out:
artifact and evidence references, claims, actions, memory observations, warnings, and the
descriptors that say what an agent can do."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from trellis.contracts import (
    OBSERVATION_KINDS,
    AgentDescriptor,
    AgentError,
    AgentExecutionContext,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    AgentWarning,
    ArtifactRef,
    Claim,
    ErrorCategory,
    EvidenceRef,
    MemoryObservation,
    RecommendedAction,
    SkillDescriptor,
)


@pytest.fixture
def ctx() -> AgentExecutionContext:
    return AgentExecutionContext.create(tenant_id="acme", agent_id="triage")


# --------------------------------------------------------------------------- AgentStatus


def test_only_success_and_partial_are_ok() -> None:
    assert {s for s in AgentStatus if s.ok} == {AgentStatus.SUCCESS, AgentStatus.PARTIAL}
    assert not AgentStatus.PAUSED.ok  # a pause is not a finished turn
    assert AgentStatus("SUCCESS") is AgentStatus.SUCCESS and AgentStatus.ERROR == "ERROR"


# --------------------------------------------------------------------------- AgentRequest


def test_create_takes_the_request_id_from_the_context(ctx: AgentExecutionContext) -> None:
    request = AgentRequest.create(ctx, {"q": 1}, objective="find it", skills_requested=["sql"])
    assert request.request_id == ctx.request_id and request.context is ctx
    assert request.input == {"q": 1} and request.skills_requested == ["sql"]
    assert request.objective == "find it" and request.constraints == {} and request.metadata == {}
    assert AgentRequest.create(ctx).input is None


def test_a_request_is_frozen_and_with_fields_returns_a_new_one(
    ctx: AgentExecutionContext,
) -> None:
    request = AgentRequest.create(ctx, "hi")
    with pytest.raises(ValidationError):
        request.input = "bye"  # type: ignore[misc]
    changed = request.with_fields(objective="rewritten", constraints={"max_tokens": 10})
    assert changed.objective == "rewritten" and changed.constraints == {"max_tokens": 10}
    assert request.objective is None and request.constraints == {}


def test_a_request_keeps_fields_it_does_not_know(ctx: AgentExecutionContext) -> None:
    """Open, so an interceptor can carry its own annotations through the pipeline."""
    request = AgentRequest.create(ctx, "hi", channel="slack")
    assert request.model_dump()["channel"] == "slack"


@pytest.mark.parametrize(
    ("objective", "input", "query"),
    [
        ("summarise", "ignored", "summarise"),  # the objective wins
        (None, "plain text", "plain text"),
        ("", "plain text", "plain text"),  # an empty objective is no objective
        (None, {"query": "q"}, "q"),
        (None, {"question": "what?"}, "what?"),
        (None, {"objective": "o"}, "o"),
        (None, {"prompt": "p"}, "p"),
        (None, {"input": "i"}, "i"),
        (None, {"text": "t"}, "t"),
        (None, {"query": "first", "text": "second"}, "first"),  # the keys are tried in order
        (None, {"query": "   ", "text": "t"}, "t"),  # blank strings are skipped
        (None, {"query": 42, "text": "t"}, "t"),  # so are non-strings
        (None, {"other": "x"}, None),
        (None, ["a list"], None),
        (None, None, None),
    ],
)
def test_query_is_the_text_a_memory_retrieval_should_use(
    ctx: AgentExecutionContext, objective: str | None, input: Any, query: str | None
) -> None:
    assert AgentRequest.create(ctx, input, objective=objective).query == query


def test_a_request_round_trips_through_json(ctx: AgentExecutionContext) -> None:
    request = AgentRequest.create(
        ctx,
        {"q": 1},
        artifact_refs=[ArtifactRef(artifact_id="art_1", uri="s3://b/k")],
        evidence_refs=[EvidenceRef(source_id="chunk_1")],
    )
    assert AgentRequest.model_validate_json(request.model_dump_json()) == request


# --------------------------------------------------------------------------- AgentResponse


def test_ok_and_failed_build_the_two_common_responses() -> None:
    ok = AgentResponse.ok({"answer": 42}, confidence=0.8)
    assert ok.status is AgentStatus.SUCCESS and ok.data == {"answer": 42} and ok.confidence == 0.8
    assert ok.succeeded
    error = AgentError(code="BOOM", category=ErrorCategory.MODEL)
    failed = AgentResponse.failed(error)
    assert failed.status is AgentStatus.ERROR and failed.error is error and not failed.succeeded
    timed_out = AgentResponse.failed(error, status=AgentStatus.TIMEOUT, data="partial")
    assert timed_out.status is AgentStatus.TIMEOUT and timed_out.data == "partial"


def test_succeeded_needs_an_ok_status_and_no_error() -> None:
    error = AgentError(code="X")
    assert AgentResponse(status=AgentStatus.PARTIAL).succeeded
    assert not AgentResponse(status=AgentStatus.SUCCESS, error=error).succeeded
    assert not AgentResponse(status=AgentStatus.REJECTED).succeeded
    assert not AgentResponse(status=AgentStatus.PAUSED).succeeded


class _Narrow(AgentResponse):
    """A caller's own response type, as a framework adapter might declare it."""


def test_coerce_wraps_whatever_an_agent_returned() -> None:
    response = AgentResponse.ok("x")
    assert AgentResponse.coerce(response) is response  # passes through untouched
    wrapped = AgentResponse.coerce({"answer": 42})
    assert wrapped.status is AgentStatus.SUCCESS and wrapped.data == {"answer": 42}
    assert AgentResponse.coerce(None).data is None
    # a response of the base type is re-read as the narrower one, not wrapped as data
    narrowed = _Narrow.coerce(AgentResponse.ok("y", metrics={"ms": 1.0}))
    assert type(narrowed) is _Narrow and narrowed.data == "y" and narrowed.metrics == {"ms": 1.0}
    subclass = _Narrow.ok("z")
    assert AgentResponse.coerce(subclass) is subclass


def test_with_fields_and_add_warning_leave_the_original_alone() -> None:
    response = AgentResponse.ok("x")
    changed = response.with_fields(status=AgentStatus.PARTIAL)
    assert changed.status is AgentStatus.PARTIAL and response.status is AgentStatus.SUCCESS
    warned = response.add_warning("MEMORY_DEGRADED", "memory was slow", latency_ms=900)
    again = warned.add_warning("TOOL_PARTIAL", "one tool failed")
    assert response.warnings == []
    assert warned.warnings == [
        AgentWarning(code="MEMORY_DEGRADED", message="memory was slow", details={"latency_ms": 900})
    ]
    assert [w.code for w in again.warnings] == ["MEMORY_DEGRADED", "TOOL_PARTIAL"]
    assert again.warnings[1].details == {}


def test_a_response_round_trips_through_json_with_everything_it_carries() -> None:
    response = AgentResponse(
        status=AgentStatus.PARTIAL,
        data={"rows": 3},
        claims=[Claim(claim_id="c1", text="t", evidence_ids=["e1"], confidence=0.5)],
        evidence=[
            EvidenceRef(source_id="e1", page=3, observed_at=datetime(2026, 1, 1, tzinfo=UTC))
        ],
        artifacts=[ArtifactRef(artifact_id="a1", size_bytes=10)],
        memory_observations=[MemoryObservation(content="remember", kind="DECISION")],
        recommended_actions=[RecommendedAction(action_type="ask", description="ask ops")],
        warnings=[AgentWarning(code="W", message="m")],
        metrics={"ms": 12.5},
        error=AgentError(code="E", category=ErrorCategory.TOOL),
    )
    assert AgentResponse.model_validate_json(response.model_dump_json()) == response


# --------------------------------------------------------------------------- artifacts


def test_an_artifact_ref_points_at_content_and_defaults_to_a_blob() -> None:
    ref = ArtifactRef(artifact_id="art_1")
    assert ref.type == "blob" and ref.uri is None and ref.metadata == {}
    with pytest.raises(ValidationError):
        ref.uri = "s3://x"  # type: ignore[misc]


def test_evidence_is_identified_by_its_source() -> None:
    evidence = EvidenceRef(source_id="chunk_7", chunk_id="c7", citation="p. 4")
    assert evidence.evidence_id == "chunk_7"
    assert evidence.source_type == "memory"  # the Memory Service's shape by default


@pytest.mark.parametrize("kind", sorted(OBSERVATION_KINDS))
def test_every_kind_the_memory_service_accepts_is_accepted(kind: str) -> None:
    assert MemoryObservation(content="x", kind=kind).kind == kind


@pytest.mark.parametrize("kind", ["agent_result", "NOTE", ""])
def test_an_unknown_observation_kind_is_refused_before_the_wire(kind: str) -> None:
    with pytest.raises(ValidationError, match="unknown observation kind") as caught:
        MemoryObservation(content="x", kind=kind)
    # the message lists what is accepted, so the fix is in the error
    assert "AGENT_RESULT" in str(caught.value) and "TOOL_RESULT" in str(caught.value)


def test_a_memory_observation_defaults_to_an_agent_result() -> None:
    observation = MemoryObservation(content="Priya owns the rollback plan")
    assert observation.kind == "AGENT_RESULT" and observation.idempotency_key is None
    assert (
        frozenset(
            {"MESSAGE", "FILE", "AGENT_RESULT", "TOOL_RESULT"}
            | {"DECISION", "FEEDBACK", "EVENT", "IMPORT"}
        )
        == OBSERVATION_KINDS
    )


# --------------------------------------------------------------------------- descriptors


def test_build_accepts_skill_ids_or_descriptors() -> None:
    sql = SkillDescriptor(skill_id="sql", description="queries", tags=["data"])
    descriptor = AgentDescriptor.build(
        "billing", skills=["refunds", sql], version="1.2.0", framework="langgraph"
    )
    assert descriptor.skill_ids == ["refunds", "sql"]
    assert descriptor.skills[0] == SkillDescriptor(skill_id="refunds")
    assert descriptor.skills[1] is sql
    assert descriptor.version == "1.2.0" and descriptor.framework == "langgraph"
    empty = AgentDescriptor.build("idle")
    assert empty.skills == [] and empty.skill_ids == [] and empty.version == "0.1.0"


def test_descriptors_default_their_versions_and_are_frozen() -> None:
    skill = SkillDescriptor(skill_id="s")
    assert skill.version == "1.0.0" and skill.input_schema is None and skill.tags == []
    with pytest.raises(ValidationError):
        skill.version = "2"  # type: ignore[misc]
