"""The execution context: how one execution is identified, derived and scoped.

Every side effect the harness performs is attributed with this context, so each rule here is
one a memory write, a span or a log line relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from trellis.contracts import AgentExecutionContext


def _ctx(**fields: object) -> AgentExecutionContext:
    return AgentExecutionContext.create(tenant_id="acme", **fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- construction


def test_create_fills_the_ids_an_application_should_not_invent() -> None:
    ctx = _ctx()
    assert ctx.agent_id == "agent"
    assert ctx.agent_run_id.startswith("run_")
    assert ctx.request_id.startswith("req_")
    assert ctx.correlation_id == ctx.request_id  # a root request correlates with itself
    assert ctx.trace_id and ctx.trace_id != ctx.request_id
    assert ctx.deadline is None and ctx.group_ids == () and ctx.metadata == {}


def test_create_keeps_every_id_it_is_given_and_cleans_the_agent_id() -> None:
    ctx = _ctx(
        agent_id="billing agent/v2",
        agent_run_id="run_1",
        request_id="req_1",
        correlation_id="corr_1",
        trace_id="tr_1",
    )
    assert ctx.agent_id == "billing-agent-v2"  # outside the shared id alphabet
    assert (ctx.agent_run_id, ctx.request_id, ctx.correlation_id, ctx.trace_id) == (
        "run_1",
        "req_1",
        "corr_1",
        "tr_1",
    )


def test_a_timeout_becomes_an_absolute_deadline_unless_one_is_given() -> None:
    before = datetime.now(UTC)
    ctx = _ctx(timeout_seconds=30)
    assert ctx.deadline is not None
    assert (
        before + timedelta(seconds=29) <= ctx.deadline <= datetime.now(UTC) + timedelta(seconds=30)
    )
    fixed = datetime(2030, 1, 1, tzinfo=UTC)
    assert _ctx(timeout_seconds=30, deadline=fixed).deadline == fixed


def test_group_ids_are_held_as_a_tuple_so_the_context_stays_immutable() -> None:
    ctx = _ctx(group_ids=["finance", "ops"])
    assert ctx.group_ids == ("finance", "ops")
    with pytest.raises(ValidationError, match="group_ids"):
        _ctx(group_ids=None)  # "no groups" is an empty sequence, not None


def test_the_context_is_frozen_and_closed() -> None:
    ctx = _ctx()
    with pytest.raises(ValidationError):
        ctx.tenant_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="bogus"):
        AgentExecutionContext.model_validate({**ctx.model_dump(), "bogus": 1})


def test_a_turn_without_a_session_gets_one_session_per_thread() -> None:
    """An application that only knows "turn 3 of chat-42" need not invent a session id."""
    ctx = _ctx(thread_id="chat 42", turn_id="t3")
    assert ctx.session_id == "chat-42-session"
    assert _ctx(thread_id="chat-42", turn_id="t4").session_id == ctx.session_id
    # an explicit session wins, and a turn with no thread has nothing to derive from
    assert _ctx(thread_id="chat-42", turn_id="t3", session_id="s1").session_id == "s1"
    assert _ctx(turn_id="t3").session_id is None


def test_input_that_is_not_a_mapping_is_left_to_pydantic_to_refuse() -> None:
    with pytest.raises(ValidationError):
        AgentExecutionContext.model_validate("acme")
    source = SimpleNamespace(
        tenant_id="acme",
        agent_id="a",
        agent_run_id="run_1",
        request_id="req_1",
        correlation_id="req_1",
        trace_id="tr",
        thread_id="chat",
        turn_id="t1",
    )
    read = AgentExecutionContext.model_validate(source, from_attributes=True)
    # attributes are read as they are: the session derivation applies to mappings only
    assert read.tenant_id == "acme" and read.turn_id == "t1" and read.session_id is None


# --------------------------------------------------------------------------- derivation


def test_a_child_inherits_the_trusted_identity_and_replaces_the_run() -> None:
    parent = _ctx(
        agent_id="planner",
        workspace_id="fin",
        user_id="u1",
        group_ids=["g"],
        thread_id="thr",
        session_id="ses",
        turn_id="t1",
        work_id="w1",
        task_id="task-1",
        agent_group_id="grp",
        metadata={"a": 1, "b": 1},
    )
    child = parent.for_agent("worker", metadata={"b": 2})
    for field in (
        "tenant_id",
        "workspace_id",
        "user_id",
        "group_ids",
        "thread_id",
        "session_id",
        "turn_id",
        "work_id",
        "request_id",
        "correlation_id",
        "trace_id",
        "agent_group_id",
        "task_id",
    ):
        assert getattr(child, field) == getattr(parent, field), field
    assert child.agent_id == "worker"
    assert child.agent_run_id.startswith("run_") and child.agent_run_id != parent.agent_run_id
    assert child.parent_agent_run_id == child.causation_id == parent.agent_run_id
    assert child.metadata == {"a": 1, "b": 2} and parent.metadata == {"a": 1, "b": 1}


def test_a_child_can_name_its_run_group_and_task() -> None:
    parent = _ctx(agent_group_id="grp", task_id="t-parent")
    child = parent.for_agent(
        "worker", agent_run_id="run_child", agent_group_id="grp2", task_id="t-child"
    )
    assert (child.agent_run_id, child.agent_group_id, child.task_id) == (
        "run_child",
        "grp2",
        "t-child",
    )
    # an empty task id is a choice the caller made, not a missing one
    assert parent.for_agent("worker", task_id="").task_id == ""


def test_a_child_deadline_never_outlives_its_parent() -> None:
    soon = datetime.now(UTC) + timedelta(seconds=10)
    later = soon + timedelta(hours=1)
    bounded = _ctx(deadline=soon)
    assert bounded.for_agent("w", deadline=later).deadline == soon
    assert bounded.for_agent("w").deadline == soon
    assert _ctx().for_agent("w", deadline=later).deadline == later
    assert _ctx().for_agent("w").deadline is None


def test_with_fields_returns_a_new_context_and_tuples_group_ids() -> None:
    ctx = _ctx()
    changed = ctx.with_fields(user_id="u2", group_ids=["x"])
    assert changed.user_id == "u2" and changed.group_ids == ("x",)
    assert ctx.user_id is None and ctx.group_ids == ()


def test_with_deadline_only_ever_tightens() -> None:
    soon = datetime.now(UTC) + timedelta(seconds=10)
    later = soon + timedelta(hours=1)
    assert _ctx().with_deadline(later).deadline == later
    assert _ctx(deadline=soon).with_deadline(later).deadline == soon
    assert _ctx(deadline=later).with_deadline(soon).deadline == soon
    assert _ctx(deadline=soon).with_deadline(None).deadline == soon


# --------------------------------------------------------------------------- derived values


def test_remaining_seconds_and_expiry_follow_the_deadline() -> None:
    unbounded = _ctx()
    assert unbounded.remaining_seconds is None and not unbounded.expired
    live = _ctx(timeout_seconds=60)
    assert live.remaining_seconds is not None and 0 < live.remaining_seconds <= 60
    assert not live.expired
    past = _ctx(deadline=datetime.now(UTC) - timedelta(seconds=5))
    assert past.remaining_seconds == 0.0  # never negative
    assert past.expired


def test_the_idempotency_key_is_stable_across_retries_and_moves_with_the_lineage() -> None:
    ctx = _ctx(thread_id="thr", turn_id="t1", agent_run_id="run_1")
    key = ctx.idempotency_key("observe", 1)
    assert key == ctx.idempotency_key("observe", 1)
    assert key.startswith("uah-")
    assert key != ctx.idempotency_key("observe", 2)
    assert key != ctx.with_fields(agent_run_id="run_2").idempotency_key("observe", 1)
    assert ctx.idempotency_key("observe", 1, prefix="run").startswith("run-")
    # a fresh context for the same logical execution produces the same key
    again = _ctx(thread_id="thr", turn_id="t1", agent_run_id="run_1")
    assert again.idempotency_key("observe", 1) == key


def test_scope_fields_are_the_memory_service_keywords_without_empty_values() -> None:
    ctx = _ctx(
        agent_id="a",
        agent_run_id="run_1",
        trace_id="tr",
        correlation_id="corr",
        workspace_id="fin",
        user_id="u1",
        group_ids=["g1", "g2"],
        thread_id="thr",
        session_id="ses",
        turn_id="t1",
        work_id="w",
        task_id="task",
        agent_group_id="grp",
    )
    assert ctx.scope_fields() == {
        "tenant_id": "acme",
        "workspace_id": "fin",
        "user_id": "u1",
        "group_ids": ["g1", "g2"],
        "thread_id": "thr",
        "session_id": "ses",
        "turn_id": "t1",
        "work_id": "w",
        "task_id": "task",
        "agent_id": "a",
        "agent_group_id": "grp",
        "agent_run_id": "run_1",
        "trace_id": "tr",
        "correlation_id": "corr",
    }
    child = ctx.for_agent("b")
    assert child.scope_fields()["parent_agent_run_id"] == "run_1"
    minimal = _ctx(agent_id="a", agent_run_id="run_1", trace_id="tr", correlation_id="c")
    assert minimal.scope_fields() == {
        "tenant_id": "acme",
        "agent_id": "a",
        "agent_run_id": "run_1",
        "trace_id": "tr",
        "correlation_id": "c",
    }


def test_scope_fields_drop_conversation_ids_the_service_would_refuse() -> None:
    """``session_id`` needs ``thread_id`` and ``turn_id`` needs ``session_id``."""
    orphan = _ctx(session_id="ses", turn_id="t1")
    scope = orphan.scope_fields()
    assert "session_id" not in scope and "turn_id" not in scope and "thread_id" not in scope
    # a copy bypasses construction, so the boundary derives the session itself
    copied = _ctx(thread_id="thr").with_fields(turn_id="t9")
    assert copied.session_id is None
    assert copied.scope_fields()["session_id"] == "thr-session"
    assert copied.scope_fields()["turn_id"] == "t9"


def test_log_fields_are_strings_and_leave_out_what_is_unset() -> None:
    ctx = _ctx(agent_id="a", agent_run_id="run_1", request_id="req_1", trace_id="tr")
    assert ctx.log_fields() == {
        "trace_id": "tr",
        "request_id": "req_1",
        "correlation_id": "req_1",
        "tenant_id": "acme",
        "agent_id": "a",
        "agent_run_id": "run_1",
    }
    full = ctx.with_fields(task_id="task", thread_id="thr", turn_id="t1")
    assert {"task_id": "task", "thread_id": "thr", "turn_id": "t1"}.items() <= (
        full.log_fields().items()
    )
