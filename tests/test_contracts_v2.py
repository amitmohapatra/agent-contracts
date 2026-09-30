"""Contracts v2 and v3 (ADRs 0001, 0002): runs, events, interrupts, feedback, cards, ports."""

from __future__ import annotations

import importlib.metadata as md
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from trellis import contracts
from trellis.contracts import (
    AgentCapabilities,
    AgentCard,
    AgentDescriptor,
    AgentDirectory,
    AgentError,
    AgentEvalEvent,
    AgentExecutionContext,
    AgentPaused,
    AgentProvider,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    ArtifactRef,
    ErrorCategory,
    EventSink,
    EvidenceRef,
    Feedback,
    FeedbackSource,
    FeedbackTargetKind,
    FeedbackVerdict,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    Judge,
    JudgeMethod,
    JudgeVerdict,
    RunEvent,
    RunEventType,
    RunOutcome,
    RunRecord,
    RunStart,
    RunStatus,
    RunStore,
    Schedule,
    ScheduleSpec,
    ToolCall,
    ToolOutcome,
    ToolStatus,
    events,
    ports,
)

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.fixture
def ctx() -> AgentExecutionContext:
    return AgentExecutionContext.create(
        tenant_id="acme",
        user_id="u1",
        agent_id="refund-agent",
        thread_id="thr_1",
        workspace_id="fin",
    )


def _interrupt(ctx: AgentExecutionContext, **fields: Any) -> Interrupt:
    fields.setdefault("question", "ok?")
    return Interrupt(tenant_id=ctx.tenant_id, run_id=ctx.agent_run_id, **fields)


# --------------------------------------------------------------------------- runs


def test_run_status_spells_like_agent_status_and_knows_what_is_final() -> None:
    assert {s.value for s in AgentStatus} == {s.value for s in RunStatus} - {"QUEUED", "RUNNING"}
    assert RunStatus.from_agent_status(AgentStatus.SUCCESS) is RunStatus.SUCCESS
    assert RunStatus.SUCCESS.final and RunStatus.CANCELLED.final
    assert not any(s.final for s in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED))
    with pytest.raises(ValueError):
        RunStatus("STARTED")


_LIVE = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED)
_ENDINGS = tuple(s for s in RunStatus if s not in _LIVE)
#: the whole state machine, spelled out once more by hand so a change to it is deliberate
_MOVES = {
    (RunStatus.QUEUED, RunStatus.RUNNING),
    (RunStatus.QUEUED, RunStatus.CANCELLED),
    (RunStatus.QUEUED, RunStatus.TIMEOUT),
    (RunStatus.RUNNING, RunStatus.QUEUED),  # a worker's lease lapsed
    (RunStatus.RUNNING, RunStatus.PAUSED),
    *((RunStatus.RUNNING, ending) for ending in _ENDINGS),
    (RunStatus.PAUSED, RunStatus.RUNNING),
    (RunStatus.PAUSED, RunStatus.QUEUED),  # resumed by a worker
    (RunStatus.PAUSED, RunStatus.CANCELLED),
    (RunStatus.PAUSED, RunStatus.TIMEOUT),
}


@pytest.mark.parametrize("source", list(RunStatus))
@pytest.mark.parametrize("target", list(RunStatus))
def test_the_run_state_machine(source: RunStatus, target: RunStatus) -> None:
    assert source.can_become(target) is ((source, target) in _MOVES)


def test_queued_runs_reach_an_ending_only_by_running_or_being_abandoned() -> None:
    assert not RunStatus.QUEUED.can_become(RunStatus.SUCCESS)
    assert not RunStatus.QUEUED.can_become(RunStatus.PAUSED)
    assert not RunStatus.PAUSED.can_become(RunStatus.SUCCESS)
    assert all(not ending.can_become(status) for ending in _ENDINGS for status in RunStatus)


def test_a_run_starts_from_the_request_and_is_recorded_as_running(
    ctx: AgentExecutionContext,
) -> None:
    child = ctx.for_agent("worker", deadline=datetime.now(UTC) + timedelta(seconds=60))
    request = AgentRequest.create(child, input={"order": 91}, metadata={"channel": "chat"})
    start = RunStart.from_request(request)
    assert start.run_id == child.agent_run_id and start.parent_run_id == ctx.agent_run_id
    assert start.tenant_id == "acme" and start.user_id == "u1" and start.thread_id == "thr_1"
    assert start.deadline == child.deadline and start.input == {"order": 91}
    assert start.idempotency_key == child.idempotency_key("run", "start")
    assert start.workspace_id == "fin" and start.metadata == {"channel": "chat"}
    record = RunRecord.from_start(start)
    assert record.status is RunStatus.RUNNING and not record.final and record.awaiting is None
    queued = RunRecord.from_start(start, status=RunStatus.QUEUED)
    assert queued.status is RunStatus.QUEUED and not queued.final
    with pytest.raises(ValueError, match="starts QUEUED or RUNNING"):
        RunRecord.from_start(start, status=RunStatus.SUCCESS)
    assert record.model_dump(include=set(RunStart.model_fields)) == start.model_dump()
    with pytest.raises(ValidationError):
        RunRecord(run_id="r", tenant_id="t", agent_id="a", status="STARTED")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="timezone"):
        RunStart(run_id="r", tenant_id="t", agent_id="a", deadline=datetime(2026, 1, 1))


def test_a_service_queuing_a_run_nobody_waits_on_mints_its_id() -> None:
    first, second = (RunStart(tenant_id="t", agent_id="a") for _ in range(2))
    assert first.run_id.startswith("run_") and first.run_id != second.run_id
    assert RunStart.model_validate_json(first.model_dump_json()) == first


def test_a_record_holds_its_own_invariants(ctx: AgentExecutionContext) -> None:
    base = {"run_id": ctx.agent_run_id, "tenant_id": "acme", "agent_id": "a"}
    with pytest.raises(ValidationError, match="PAUSED run waits"):
        RunRecord(**base, status=RunStatus.PAUSED)
    with pytest.raises(ValidationError, match="PAUSED run waits"):
        RunRecord(**base, status=RunStatus.SUCCESS, awaiting=_interrupt(ctx))
    with pytest.raises(ValidationError, match="another run"):
        RunRecord(
            **base,
            status=RunStatus.PAUSED,
            awaiting=_interrupt(ctx).model_copy(update={"run_id": "other"}),
        )
    error = AgentError(code="X", message="boom", category=ErrorCategory.MODEL)
    with pytest.raises(ValidationError, match="carries no error"):
        RunRecord(**base, status=RunStatus.SUCCESS, error=error)
    failed = RunRecord(**base, status=RunStatus.ERROR, error=error)
    assert failed.final and failed.error is error
    journal = {"asks": {"q1": "yes"}, "tools": {"sha256:ab": {"ok": True}}}
    paused = RunRecord(
        **base, status=RunStatus.PAUSED, awaiting=_interrupt(ctx), checkpoint=journal
    )
    assert RunRecord.model_validate_json(paused.model_dump_json()).checkpoint == journal
    assert RunRecord(**base, status=RunStatus.QUEUED, checkpoint=journal).checkpoint == journal
    assert RunRecord(**base).checkpoint is None
    with pytest.raises(ValidationError, match="finished run carries no checkpoint"):
        RunRecord(**base, status=RunStatus.SUCCESS, checkpoint=journal)
    # read back from a store: an unknown column is ignored, not refused
    assert RunRecord.model_validate({**base, "legacy_column": 1}).status is RunStatus.RUNNING


# --------------------------------------------------------------------------- interrupts


def test_an_interrupt_is_the_pause_plus_its_identity(ctx: AgentExecutionContext) -> None:
    paused = AgentPaused(
        "Approve a EUR 240 refund for order 91?",
        expects={"type": "boolean"},
        payload={"order_id": 91},
    )
    call = ToolCall(tool="billing.refund", args={"order_id": 91}, idempotency_key="call-1")
    interrupt = Interrupt.from_paused(
        paused, context=ctx, reason=InterruptReason.APPROVAL, tool_call=call
    )
    awaiting = interrupt.awaiting()
    assert awaiting["question"] == paused.question and awaiting["expects"] == {"type": "boolean"}
    assert awaiting["tool_call"]["args"] == {"order_id": 91} and awaiting["reason"] == "APPROVAL"
    assert awaiting["tenant_id"] == "acme" and awaiting["run_id"] == ctx.agent_run_id
    assert Interrupt.model_validate(awaiting) == interrupt  # what the store keeps reads back
    assert interrupt.interrupt_id.startswith("int_")
    awaiting["payload"]["order_id"] = 999  # a copy: the record is untouched
    assert interrupt.payload == {"order_id": 91}
    plain = _interrupt(ctx, question="which region?")
    assert set(plain.awaiting()) == {
        "interrupt_id",
        "tenant_id",
        "run_id",
        "reason",
        "question",
        "ui",
        "options",
        "created_at",
    }


@pytest.mark.parametrize("question", ["", "   "])
def test_an_interrupt_needs_a_question(ctx: AgentExecutionContext, question: str) -> None:
    with pytest.raises(ValidationError, match="question"):
        _interrupt(ctx, question=question)


def test_an_approval_names_its_tool_call_and_reasons_are_closed(ctx: AgentExecutionContext) -> None:
    with pytest.raises(ValidationError, match="tool call under approval"):
        _interrupt(ctx, reason=InterruptReason.APPROVAL)
    with pytest.raises(ValidationError):
        _interrupt(ctx, reason="MAYBE")  # type: ignore[arg-type]


def test_choice_and_review_carry_what_they_ask_about(ctx: AgentExecutionContext) -> None:
    with pytest.raises(ValidationError, match="options to choose from"):
        _interrupt(ctx, reason=InterruptReason.CHOICE, ui="choice")
    choice = _interrupt(ctx, reason=InterruptReason.CHOICE, ui="choice", options=["EU", "US"])
    assert choice.options == ["EU", "US"] and choice.awaiting()["options"] == ["EU", "US"]
    with pytest.raises(ValidationError, match="correction looks like"):
        _interrupt(ctx, reason=InterruptReason.REVIEW, ui="diff")
    review = _interrupt(ctx, reason=InterruptReason.REVIEW, ui="diff", expects={"type": "string"})
    assert review.reason is InterruptReason.REVIEW and review.ui == "diff"
    assert _interrupt(ctx).ui == "approve" and _interrupt(ctx).options == []
    with pytest.raises(ValidationError):
        _interrupt(ctx, ui="slider")


def test_an_interrupt_names_who_answers_by_when_and_who_is_next(
    ctx: AgentExecutionContext,
) -> None:
    deadline = datetime(2026, 10, 1, 9, tzinfo=UTC)
    interrupt = _interrupt(
        ctx,
        ui="table",
        assignee="role:procurement",
        deadline=deadline,
        escalate_to="user:cfo",
        payload_ref=ArtifactRef(artifact_id="art_1", mime_type="text/csv"),
    )
    awaiting = interrupt.awaiting()
    assert awaiting["assignee"] == "role:procurement" and awaiting["escalate_to"] == "user:cfo"
    assert awaiting["payload_ref"]["artifact_id"] == "art_1"
    assert Interrupt.model_validate(awaiting) == interrupt
    with pytest.raises(ValidationError, match="escalate_to needs a deadline"):
        _interrupt(ctx, escalate_to="user:cfo")
    with pytest.raises(ValidationError, match="timezone"):
        _interrupt(ctx, deadline=datetime(2026, 10, 1, 9))
    # the pause carries the question; everything else is added where it becomes an interrupt
    asked = Interrupt.from_paused(
        AgentPaused("Which supplier?"),
        context=ctx,
        reason=InterruptReason.CHOICE,
        ui="choice",
        options=["Acme", "Globex"],
        assignee="user:u1",
    )
    assert asked.options == ["Acme", "Globex"] and asked.assignee == "user:u1"
    assert asked.run_id == ctx.agent_run_id


def test_approve_reject_and_edit_of_a_tool_call_are_feedback(ctx: AgentExecutionContext) -> None:
    call = ToolCall(tool="billing.refund", args={"amount": 240}, idempotency_key="call-1")
    interrupt = _interrupt(ctx, reason=InterruptReason.APPROVAL, tool_call=call)

    def resolve(decision: InterruptDecision, **fields: Any) -> Feedback | None:
        resolution = InterruptResolution(
            interrupt_id=interrupt.interrupt_id,
            run_id=interrupt.run_id,
            decision=decision,
            reviewer="u1",
            **fields,
        )
        assert resolution.resolves(interrupt)
        return resolution.to_feedback(interrupt, ctx)

    approved = resolve(InterruptDecision.APPROVE)
    assert approved is not None and approved.verdict is FeedbackVerdict.APPROVE
    assert approved.target_kind is FeedbackTargetKind.TOOL_CALL and approved.target_id == "call-1"
    assert approved.source is FeedbackSource.INTERRUPT and approved.reviewer == "u1"
    assert approved.tenant_id == "acme" and approved.agent_run_id == ctx.agent_run_id
    assert approved.metadata == {
        "interrupt_id": interrupt.interrupt_id,
        "tool": "billing.refund",
        "args": {"amount": 240},
        "target": "tool_call",
    }
    edited = resolve(InterruptDecision.EDIT, payload={"amount": 200})
    assert (
        edited is not None
        and edited.verdict is FeedbackVerdict.EDIT
        and edited.correction == {"amount": 200}
    )
    rejected = resolve(InterruptDecision.REJECT)
    assert rejected is not None and rejected.verdict is FeedbackVerdict.REJECT
    assert resolve(InterruptDecision.ANSWER, answer=True) is None
    assert resolve(InterruptDecision.CANCEL) is None
    with pytest.raises(ValidationError, match="edited arguments"):
        resolve(InterruptDecision.EDIT)
    # a call without an idempotency key is targeted through the interrupt, and says so
    bare = _interrupt(ctx, reason=InterruptReason.APPROVAL, tool_call=ToolCall(tool="t"))
    answer = InterruptResolution(
        interrupt_id=bare.interrupt_id, run_id=bare.run_id, decision=InterruptDecision.APPROVE
    )
    fb = answer.to_feedback(bare, ctx)
    assert (
        fb is not None
        and fb.target_id == bare.interrupt_id
        and fb.metadata["target"] == "interrupt"
    )
    # a question with no tool call judges nothing, whatever the decision
    plain = _interrupt(ctx)
    ok = InterruptResolution(
        interrupt_id=plain.interrupt_id, run_id=plain.run_id, decision=InterruptDecision.APPROVE
    )
    assert ok.to_feedback(plain, ctx) is None


def test_a_resolution_answers_only_its_own_interrupt(ctx: AgentExecutionContext) -> None:
    interrupt = _interrupt(
        ctx, reason=InterruptReason.APPROVAL, tool_call=ToolCall(tool="t", idempotency_key="k")
    )
    other = InterruptResolution(
        interrupt_id="int_other", run_id=interrupt.run_id, decision=InterruptDecision.APPROVE
    )
    assert not other.resolves(interrupt)
    with pytest.raises(ValueError, match="different interrupt"):
        other.to_feedback(interrupt, ctx)
    mine = InterruptResolution(
        interrupt_id=interrupt.interrupt_id,
        run_id=interrupt.run_id,
        decision=InterruptDecision.APPROVE,
    )
    stranger = AgentExecutionContext.create(
        tenant_id="globex", user_id="u9", agent_id="refund-agent"
    )
    with pytest.raises(ValueError, match="not the paused run"):
        mine.to_feedback(interrupt, stranger)


# --------------------------------------------------------------------------- events


def test_run_events_carry_the_run_identity_and_the_agui_spelling(
    ctx: AgentExecutionContext,
) -> None:
    started = RunEvent.started(ctx, 0, channel="chat")
    assert started.type is RunEventType.RUN_STARTED and started.run_id == ctx.agent_run_id
    assert started.tenant_id == "acme" and started.thread_id == "thr_1" and started.attempt == 1
    assert started.data == {"channel": "chat"} and started.event_id.startswith("evt_")
    assert started.model_dump(mode="json")["type"] == "RUN_STARTED"
    text = RunEvent.text(ctx, "TEXT_MESSAGE_CONTENT", "msg_1", 3, delta="Hel")
    assert (
        text.sequence == 3
        and text.message_id == "msg_1"
        and text.data == {"role": "assistant", "delta": "Hel"}
    )
    assert RunEvent.text(ctx, RunEventType.TEXT_MESSAGE_START, "m", 0, role="user").data == {
        "role": "user"
    }
    tool = RunEvent.tool(ctx, RunEventType.TOOL_CALL_START, "tc_1", 4, tool="billing.refund")
    assert tool.tool_call_id == "tc_1" and tool.data == {"tool": "billing.refund"}
    custom = RunEvent.custom(ctx, "recommendation", 5, actions=["a"])
    assert custom.data == {"name": "recommendation", "actions": ["a"]}
    interrupt = _interrupt(ctx)
    assert RunEvent.interrupt(ctx, interrupt, 6).data == interrupt.awaiting()
    retry = RunEvent.of(ctx, RunEventType.STEP_STARTED, 0, attempt=2, step="plan")
    assert retry.attempt == 2 and retry.step == "plan"
    copy = started.model_copy(update={"data": {}})
    assert copy.data == {} and started.data == {"channel": "chat"}
    with pytest.raises(ValidationError):
        started.data = {}  # type: ignore[misc]


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (
            lambda ctx: RunEvent.text(ctx, RunEventType.TOOL_CALL_END, "msg_1", 0),
            "not a text message event",
        ),
        (
            lambda ctx: RunEvent.tool(ctx, RunEventType.TEXT_MESSAGE_END, "tc_1", 0),
            "not a tool call event",
        ),
        (lambda ctx: RunEvent.text(ctx, "BOGUS", "msg_1", 0), "BOGUS"),
        (lambda ctx: RunEvent.of(ctx, RunEventType.RUN_STARTED, -1), "greater than or equal to 0"),
        (
            lambda ctx: RunEvent.of(ctx, RunEventType.RUN_STARTED, 0, outcome=RunOutcome.SUCCESS),
            "carries no outcome",
        ),
        (lambda ctx: RunEvent.of(ctx, RunEventType.RUN_FINISHED, 0), "names its outcome"),
        (
            lambda ctx: RunEvent.of(
                ctx, RunEventType.RUN_FINISHED, 0, outcome=RunOutcome.INTERRUPT
            ),
            "carries the interrupt",
        ),
        (lambda ctx: RunEvent.of(ctx, RunEventType.TEXT_MESSAGE_END, 0), "names its message"),
        (lambda ctx: RunEvent.of(ctx, RunEventType.TOOL_CALL_RESULT, 0), "names its tool call"),
        (
            lambda ctx: RunEvent.of(ctx, RunEventType.RUN_STARTED, 0, attempt=0),
            "greater than or equal to 1",
        ),
    ],
)
def test_an_event_whose_shape_disagrees_with_its_type_is_refused(
    ctx: AgentExecutionContext, build: Any, message: str
) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        build(ctx)


def test_a_finished_event_names_its_outcome_and_an_interrupt_carries_the_question(
    ctx: AgentExecutionContext,
) -> None:
    interrupt = _interrupt(ctx, question="which region?")
    finished = RunEvent.finished(ctx, RunOutcome.INTERRUPT, 9, interrupt=interrupt, note="x")
    assert finished.outcome is RunOutcome.INTERRUPT and finished.data["note"] == "x"
    assert finished.data["interrupt"]["question"] == "which region?"
    with pytest.raises(ValueError, match="carries the interrupt"):
        RunEvent.finished(ctx, "interrupt", 1)
    with pytest.raises(ValueError, match="only that one does"):
        RunEvent.finished(ctx, RunOutcome.SUCCESS, 1, interrupt=interrupt)
    error = AgentError(code="X", message="boom", category=ErrorCategory.MODEL)
    failed = RunEvent.finished(ctx, RunOutcome.ERROR, 2, error=error)
    assert failed.error is error and failed.model_dump(mode="json")["outcome"] == "error"


@pytest.mark.parametrize(
    "status",
    [*(s for s in RunStatus if s not in {RunStatus.QUEUED, RunStatus.RUNNING}), *AgentStatus],
)
def test_every_settled_status_has_an_outcome(status: RunStatus | AgentStatus) -> None:
    assert isinstance(RunOutcome.from_status(status), RunOutcome)


def test_outcomes_keep_a_policy_refusal_apart_and_refuse_the_unsettled() -> None:
    assert RunOutcome.from_status(RunStatus.PAUSED) is RunOutcome.INTERRUPT
    assert RunOutcome.from_status(AgentStatus.REJECTED) is RunOutcome.REJECTED
    assert RunOutcome.from_status("TIMEOUT") is RunOutcome.TIMEOUT
    for unsettled in ("QUEUED", "RUNNING"):
        with pytest.raises(ValueError, match=f"a {unsettled} run has no outcome yet"):
            RunOutcome.from_status(unsettled)
    with pytest.raises(ValueError):
        RunOutcome.from_status("success")


# --------------------------------------------------------------------------- feedback / judge


def test_feedback_is_bounded_and_bound_to_the_request(ctx: AgentExecutionContext) -> None:
    feedback = Feedback.for_context(
        ctx,
        target_kind=FeedbackTargetKind.MEMORY,
        target_id="mem_1",
        verdict=FeedbackVerdict.CORRECT,
        correction="The renewal is in March, not May.",
        score=0.9,
    )
    assert (feedback.tenant_id, feedback.workspace_id, feedback.user_id) == ("acme", "fin", "u1")
    assert (feedback.agent_id, feedback.agent_run_id, feedback.trace_id) == (
        "refund-agent",
        ctx.agent_run_id,
        ctx.trace_id,
    )
    assert feedback.feedback_id.startswith("fb_") and feedback.source is FeedbackSource.HUMAN
    confirm: dict[str, Any] = {
        "target_kind": FeedbackTargetKind.RUN,
        "target_id": "r",
        "verdict": FeedbackVerdict.CONFIRM,
    }
    for score in (0, 1, 0.0, 1.0, None):
        Feedback.for_context(ctx, **confirm, score=score)
    for score in (-0.1, 1.5, True):
        with pytest.raises(ValidationError):
            Feedback.for_context(ctx, **confirm, score=score)
    with pytest.raises(ValidationError, match="target_id"):
        Feedback.for_context(
            ctx, target_kind=FeedbackTargetKind.RUN, target_id="  ", verdict=FeedbackVerdict.CONFIRM
        )
    with pytest.raises(ValidationError, match="needs a correction"):
        Feedback.for_context(
            ctx,
            target_kind=FeedbackTargetKind.MEMORY,
            target_id="m",
            verdict=FeedbackVerdict.CORRECT,
        )
    with pytest.raises(ValueError, match="come from the context"):
        Feedback.for_context(ctx, **confirm, tenant_id="other")
    with pytest.raises(ValidationError):
        Feedback.for_context(
            ctx, target_kind="vibe", target_id="r", verdict=FeedbackVerdict.CONFIRM
        )  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Feedback.for_context(ctx, target_kind=FeedbackTargetKind.RUN, target_id="r", verdict="meh")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Feedback.for_context(ctx, **confirm, source="bot")  # type: ignore[arg-type]


def test_a_judge_verdict_becomes_judge_feedback_on_the_answer() -> None:
    event = AgentEvalEvent(
        agent_id="a", agent_run_id="run_1", tenant_id="acme", trace_id=TRACE, result_ref="art_9"
    )
    good = JudgeVerdict(
        score=0.8,
        method=JudgeMethod.GROUNDED,
        rationale="all claims cited",
        cost_usd=0.0,
        metadata={"method": "mine"},
    )
    feedback = good.as_feedback(event)
    assert feedback.verdict is FeedbackVerdict.CONFIRM and feedback.source is FeedbackSource.JUDGE
    assert feedback.target_kind is FeedbackTargetKind.ANSWER and feedback.target_id == "art_9"
    assert feedback.reviewer == "judge:grounded" and feedback.score == 0.8
    assert feedback.metadata["method"] == "grounded" and feedback.metadata["target"] == "result"
    assert feedback.trace_id == TRACE
    bad = JudgeVerdict(score=0.2, method=JudgeMethod.LLM, model="gpt-4.1-nano")
    assert (
        bad.as_feedback(event).verdict is FeedbackVerdict.REJECT
        and bad.reviewer == "judge:gpt-4.1-nano"
    )
    on_run = bad.as_feedback(event.model_copy(update={"result_ref": None}))
    assert on_run.target_id == "run_1" and on_run.metadata["target"] == "run"
    edge = JudgeVerdict(score=0.5, method=JudgeMethod.LLM)
    assert edge.as_feedback(event).verdict is FeedbackVerdict.CONFIRM
    assert edge.as_feedback(event, threshold=0.6).verdict is FeedbackVerdict.REJECT
    for score in (0.0, 1.0):
        JudgeVerdict(score=score, method=JudgeMethod.GROUNDED)
    for score in (-0.1, 2.0):
        with pytest.raises(ValidationError):
            JudgeVerdict(score=score, method=JudgeMethod.LLM)
    with pytest.raises(ValidationError):
        JudgeVerdict(score=0.5, method="vibes")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AgentEvalEvent(agent_id="a", agent_run_id="r", tenant_id="t", status="MEH")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- agent card


def test_an_agent_card_is_the_descriptor_in_a2a_spelling() -> None:
    descriptor = AgentDescriptor.build(
        "refund-agent",
        skills=["billing.refund"],
        description="Refunds within policy",
        version="1.2.0",
        framework="langgraph",
        metadata={"registry_entity": "ent_1", "internal_owner": "team-x"},
    )
    card = AgentCard.from_descriptor(
        descriptor,
        url="https://agents.example/refund",
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        provider=AgentProvider(organization="Acme", url="https://acme.example"),
        security_schemes={"teamKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        security=[{"teamKey": []}],
    )
    a2a = card.to_a2a()
    assert (
        a2a["name"] == "refund-agent"
        and a2a["version"] == "1.2.0"
        and a2a["protocolVersion"] == "1.0"  # what a2a-sdk 1.x actually speaks
    )
    assert a2a["capabilities"] == {
        "streaming": True,
        "pushNotifications": True,
        "stateTransitionHistory": False,
        "extensions": [],
    }
    assert a2a["defaultInputModes"] == ["text/plain"] and "default_input_modes" not in a2a
    assert a2a["skills"] == [
        {
            "id": "billing.refund",
            "name": "billing.refund",
            "description": "",
            "tags": [],
            "examples": [],
        }
    ]
    assert a2a["securitySchemes"]["teamKey"]["name"] == "X-API-Key" and a2a["security"] == [
        {"teamKey": []}
    ]
    assert a2a["provider"] == {"organization": "Acme", "url": "https://acme.example"}
    assert (
        a2a["supportsAuthenticatedExtendedCard"] is False and a2a["preferredTransport"] == "JSONRPC"
    )
    assert "metadata" not in a2a  # the platform's facts stay on the model
    assert card.metadata == {
        "agent_group_id": None,
        "framework": "langgraph",
        "harness_version": None,
    }
    assert AgentCard.from_a2a(a2a).to_a2a() == a2a and card.skill("billing.refund") is not None
    assert card.skill("nope") is None


def test_a_foreign_card_is_data(ctx: AgentExecutionContext) -> None:
    foreign = {
        "name": "other",
        "url": "https://other.example/a2a",
        "version": "2.0.0",
        "iconUrl": "https://other.example/icon.png",
        "additionalInterfaces": [{"url": "https://other.example/grpc", "transport": "GRPC"}],
        "signatures": [{"protected": "e30", "signature": "abc"}],
        "unknownField": {"drop": "me"},
        "metadata": {"not": "ours"},
    }
    card = AgentCard.from_a2a(foreign)
    assert card.icon_url == "https://other.example/icon.png"
    assert (
        card.additional_interfaces[0].transport == "GRPC"
        and card.signatures[0]["signature"] == "abc"
    )
    assert not hasattr(card, "unknownField") and "unknownField" not in card.to_a2a()
    assert card.metadata == {"not": "ours"} and "metadata" not in card.to_a2a()
    assert AgentCard.from_a2a(
        {"name": "a", "url": "https://a.example", "version": "1", "default_input_modes": ["x"]}
    ).to_a2a()["defaultInputModes"] == ["x"]
    for url in ("javascript:alert(1)", "ftp://x", "not a url", ""):
        with pytest.raises(ValidationError, match="http"):
            AgentCard(name="a", url=url, version="1")
    with pytest.raises(ValidationError, match="http"):
        AgentCard(
            name="a", url="https://a.example", version="1", documentation_url="file:///etc/passwd"
        )
    with pytest.raises(ValidationError):
        AgentProvider(organization="x")  # the protocol requires the provider's url


# --------------------------------------------------------------------------- schedules / tools


def test_schedules_are_specs_with_identity() -> None:
    spec = ScheduleSpec(
        tenant_id="acme",
        agent_id="digest",
        name="Morning digest",
        cadence=" 0 8 * * 1-5 ",
        on_behalf_of="u1",
        timezone="Europe/Berlin",
    )
    assert spec.cadence == "0 8 * * 1-5" and spec.enabled
    schedule = Schedule.from_spec(spec, schedule_id="sch_fixed", enabled=False)
    assert (
        schedule.schedule_id == "sch_fixed"
        and not schedule.enabled
        and schedule.on_behalf_of == "u1"
    )
    assert Schedule.from_spec(spec).schedule_id.startswith("sch_")
    # read back from a store: an unknown column is ignored, not refused
    assert Schedule.model_validate({**spec.model_dump(), "legacy": 1}).name == "Morning digest"
    with pytest.raises(ValidationError, match="200"):
        ScheduleSpec(**{**spec.model_dump(), "name": "x" * 201})
    for field in ("name", "cadence", "on_behalf_of"):
        with pytest.raises(ValidationError, match="blank"):
            ScheduleSpec(**{**spec.model_dump(), field: "  "})
    with pytest.raises(ValidationError, match="unknown timezone"):
        ScheduleSpec(**{**spec.model_dump(), "timezone": "Mars/Olympus"})


def test_runs_and_schedules_carry_no_webhook_url() -> None:
    """Notifications go to the tenant's webhook subscriptions, never a per-run URL."""
    assert "webhook_url" not in RunStart.model_fields
    assert "webhook_url" not in ScheduleSpec.model_fields
    assert "webhook_url" not in inspect.signature(RunStart.from_request).parameters
    with pytest.raises(ValidationError, match="webhook_url"):
        RunStart(tenant_id="t", agent_id="a", webhook_url="https://hooks.example/run")  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="webhook_url"):
        ScheduleSpec(
            tenant_id="t",
            agent_id="a",
            name="n",
            cadence="daily",
            on_behalf_of="u1",
            webhook_url="https://hooks.example/s",  # type: ignore[call-arg]
        )


def test_a_schedule_records_its_author_and_how_its_fires_went() -> None:
    spec = ScheduleSpec(tenant_id="t", agent_id="a", name="n", cadence="daily", on_behalf_of="u1")
    fresh = Schedule.from_spec(spec)
    assert fresh.created_by is None and fresh.last_run_id is None and fresh.last_error is None
    assert fresh.consecutive_failures == 0 and fresh.retry_after is None
    # the author comes from the credential, so a spec cannot claim one
    with pytest.raises(ValidationError, match="created_by"):
        ScheduleSpec(**spec.model_dump(), created_by="admin")
    retry = datetime(2026, 10, 1, 8, 5, tzinfo=UTC)
    failing = Schedule.from_spec(
        spec,
        created_by="admin",
        last_run_id="run_1",
        consecutive_failures=2,
        last_error={"status": 503, "message": "agent-runs unavailable"},
        retry_after=retry,
    )
    assert failing.created_by == "admin" and failing.retry_after == retry
    assert Schedule.model_validate_json(failing.model_dump_json()) == failing
    with pytest.raises(ValidationError):
        Schedule.from_spec(spec, consecutive_failures=-1)
    with pytest.raises(ValidationError, match="timezone"):
        Schedule.from_spec(spec, retry_after=datetime(2026, 10, 1, 8, 5))


def test_tool_status_is_an_enum_that_still_compares_to_its_strings() -> None:
    outcome = ToolOutcome(tool="x", status="ok")  # type: ignore[arg-type]
    assert outcome.ok and outcome.status == "ok" and outcome.status is ToolStatus.OK
    assert not ToolOutcome(tool="x", status=ToolStatus.CANCELLED).ok
    outcome.status = "error"  # type: ignore[assignment]
    assert outcome.status is ToolStatus.ERROR and not outcome.ok  # assignments are validated too
    with pytest.raises(ValidationError):
        ToolOutcome(tool="x", status="meh")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        outcome.status = "OK"  # type: ignore[assignment]


# --------------------------------------------------------------------------- wire shape


def _samples(ctx: AgentExecutionContext) -> list[BaseModel]:
    call = ToolCall(tool="t", idempotency_key="k")
    interrupt = _interrupt(
        ctx,
        reason=InterruptReason.APPROVAL,
        tool_call=call,
        expects={"type": "boolean"},
        payload={"a": 1},
    )
    error = AgentError(code="X", message="boom", category=ErrorCategory.MODEL)
    return [
        Feedback.for_context(
            ctx,
            target_kind=FeedbackTargetKind.RUN,
            target_id="r",
            verdict=FeedbackVerdict.CONFIRM,
            score=0.5,
            evidence_refs=[EvidenceRef(source_id="s")],
        ),
        interrupt,
        _interrupt(
            ctx,
            reason=InterruptReason.CHOICE,
            ui="choice",
            options=["a", "b"],
            assignee="role:ops",
            deadline=datetime(2026, 10, 1, tzinfo=UTC),
            escalate_to="user:boss",
            payload_ref=ArtifactRef(artifact_id="art_1"),
        ),
        InterruptResolution(
            interrupt_id=interrupt.interrupt_id,
            run_id=interrupt.run_id,
            decision=InterruptDecision.EDIT,
            payload={"a": 2},
        ),
        JudgeVerdict(score=1.0, method=JudgeMethod.LLM, model="m"),
        RunStart.from_request(AgentRequest.create(ctx, input={"q": 1})),
        RunRecord(
            run_id=ctx.agent_run_id,
            tenant_id="acme",
            agent_id="a",
            status=RunStatus.PAUSED,
            awaiting=interrupt,
        ),
        RunRecord(
            run_id=ctx.agent_run_id,
            tenant_id="acme",
            agent_id="a",
            status=RunStatus.ERROR,
            error=error,
        ),
        RunEvent.finished(ctx, RunOutcome.INTERRUPT, 3, interrupt=interrupt),
        RunEvent.finished(ctx, RunOutcome.ERROR, 4, error=error),
        Schedule.from_spec(
            ScheduleSpec(
                tenant_id="t", agent_id="a", name="n", cadence="0 8 * * *", on_behalf_of="u"
            )
        ),
        AgentCard(name="a", url="https://a.example", version="1"),
    ]


def test_v2_models_survive_json(ctx: AgentExecutionContext) -> None:
    for model in _samples(ctx):
        assert type(model).model_validate_json(model.model_dump_json(by_alias=True)) == model, type(
            model
        )


def test_written_records_are_frozen_and_closed(ctx: AgentExecutionContext) -> None:
    for model in _samples(ctx):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
    for model in (
        RunStart(run_id="r", tenant_id="t", agent_id="a"),
        _interrupt(ctx),
        JudgeVerdict(score=0.5, method=JudgeMethod.LLM),
    ):
        with pytest.raises(ValidationError, match="bogus"):
            type(model).model_validate({**model.model_dump(), "bogus": 1})


@pytest.mark.parametrize(
    "enum",
    [
        RunEventType,
        RunOutcome,
        InterruptDecision,
        InterruptReason,
        FeedbackSource,
        JudgeMethod,
        ToolStatus,
    ],
)
def test_closed_vocabularies_refuse_strangers(enum: type) -> None:
    with pytest.raises(ValueError):
        enum("nope")
    with pytest.raises(ValidationError):
        InterruptResolution(interrupt_id="i", run_id="r", decision="MAYBE")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- ports


class _Runs:
    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}

    async def queued(self, start: RunStart) -> RunRecord:
        return self.records.setdefault(
            start.run_id, RunRecord.from_start(start, status=RunStatus.QUEUED)
        )

    async def started(self, start: RunStart) -> RunRecord:
        if (queued := self.records.get(start.run_id)) and queued.status is RunStatus.QUEUED:
            return self._move(queued, RunStatus.RUNNING)
        return self.records.setdefault(start.run_id, RunRecord.from_start(start))

    def _move(self, record: RunRecord, status: RunStatus, **fields: Any) -> RunRecord:
        assert record.status.can_become(status), (record.status, status)
        moved = record.model_copy(update={"status": status, **fields})
        self.records[record.run_id] = moved
        return moved

    async def paused(
        self, interrupt: Interrupt, *, checkpoint: dict[str, Any] | None = None
    ) -> RunRecord:
        return self._move(
            self.records[interrupt.run_id],
            RunStatus.PAUSED,
            awaiting=interrupt,
            checkpoint=checkpoint,
        )

    async def resumed(self, resolution: InterruptResolution) -> RunRecord:
        current = self.records[resolution.run_id]
        return self._move(
            current,
            RunStatus.RUNNING,
            awaiting=None,
            last_resolution=resolution,
            attempt=current.attempt + 1,
        )

    async def finished(
        self, run_id: str, status: RunStatus, *, output: Any = None, error: AgentError | None = None
    ) -> RunRecord:
        return self._move(self.records[run_id], status, output=output, error=error, checkpoint=None)

    async def get(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

    async def list_paused(self, tenant_id: str, *, limit: int = 100) -> Sequence[RunRecord]:
        return [
            r
            for r in self.records.values()
            if r.tenant_id == tenant_id and r.status == RunStatus.PAUSED
        ][:limit]


class _Sink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def publish(self, event: RunEvent) -> None:
        self.events.append(event)


class _Judge:
    async def judge(
        self, event: AgentEvalEvent, /, *, response: AgentResponse | None = None
    ) -> JudgeVerdict | None:
        return None


class _Directory:
    async def get(self, agent_id: str) -> AgentCard | None:
        return None

    async def find(
        self, query: str | None = None, *, skill: str | None = None, limit: int = 20
    ) -> Sequence[AgentCard]:
        return []

    async def publish(self, card: AgentCard) -> None:
        return None


_IMPLEMENTATIONS: list[tuple[type, type]] = [
    (_Runs, RunStore),
    (_Sink, EventSink),
    (_Judge, Judge),
    (_Directory, AgentDirectory),
]


@pytest.mark.parametrize(("implementation", "port"), _IMPLEMENTATIONS)
def test_the_ports_are_satisfied_method_for_method(implementation: type, port: type) -> None:
    """``runtime_checkable`` only checks names; the signatures are compared here so a double
    (and any adapter copied from it) has the port's shape, not just its vocabulary."""
    assert isinstance(implementation(), port)
    assert not isinstance(object(), port)
    for name, member in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert inspect.signature(getattr(implementation, name)) == inspect.signature(member), (
            f"{port.__name__}.{name}"
        )


async def test_a_run_moves_through_a_store(ctx: AgentExecutionContext) -> None:
    runs, sink = _Runs(), _Sink()
    start = RunStart.from_request(AgentRequest.create(ctx, input="hi"))
    assert (await runs.queued(start)).status is RunStatus.QUEUED
    assert (await runs.queued(start)).status is RunStatus.QUEUED  # idempotent
    assert (await runs.started(start)).status is RunStatus.RUNNING
    interrupt = _interrupt(ctx)
    paused = await runs.paused(interrupt, checkpoint={"asks": {}})
    assert paused.awaiting is interrupt and paused.checkpoint == {"asks": {}}
    assert [r.run_id for r in await runs.list_paused("acme")] == [start.run_id]
    await sink.publish(RunEvent.interrupt(ctx, interrupt, 1))
    assert sink.events[0].type is RunEventType.INTERRUPT
    resolution = InterruptResolution(
        interrupt_id=interrupt.interrupt_id,
        run_id=start.run_id,
        decision=InterruptDecision.ANSWER,
        answer=True,
    )
    resumed = await runs.resumed(resolution)
    assert (
        resumed.status is RunStatus.RUNNING
        and resumed.attempt == 2
        and resumed.last_resolution is resolution
        and resumed.checkpoint == {"asks": {}}
    )
    done = await runs.finished(start.run_id, RunStatus.SUCCESS, output={"ok": True})
    assert done.final and done.checkpoint is None and (await runs.list_paused("acme")) == []


def test_everything_new_is_exported_and_the_version_is_the_installed_one() -> None:
    for name in (
        "RunEvent",
        "RunEventType",
        "RunOutcome",
        "RunRecord",
        "RunStart",
        "RunStatus",
        "Interrupt",
        "InterruptDecision",
        "InterruptReason",
        "InterruptResolution",
        "Feedback",
        "FeedbackTargetKind",
        "FeedbackVerdict",
        "FeedbackSource",
        "JudgeVerdict",
        "JudgeMethod",
        "AgentCard",
        "AgentSkill",
        "AgentCapabilities",
        "AgentProvider",
        "AgentInterface",
        "A2A_PROTOCOL_VERSION",
        "Schedule",
        "ScheduleSpec",
        "ToolStatus",
        "EventSink",
        "RunStore",
        "Judge",
        "AgentDirectory",
        "now",
    ):
        assert name in contracts.__all__ and hasattr(contracts, name), name
    assert contracts.__version__ == md.version("trellis-contracts") == "0.4.0"


@pytest.mark.parametrize(
    "name",
    [
        "FrameworkAdapter",
        "PromptProvider",
        "FeedbackStore",
        "Scheduler",
        "LifecycleListener",
        "AgentRegistryClient",
        "EvaluationSink",
        "LifecycleEvent",
    ],
)
def test_retired_names_are_gone(name: str) -> None:
    assert name not in contracts.__all__ and not hasattr(contracts, name)
    assert not hasattr(ports, name) and not hasattr(events, name)


def test_feedback_is_not_an_evaluation_provider_concern() -> None:
    assert not hasattr(contracts.EvaluationProvider, "submit_feedback")


def test_an_awaiting_stored_by_contracts_v2_still_reads_back(ctx: AgentExecutionContext) -> None:
    stored = {
        "interrupt_id": "int_1",
        "tenant_id": "acme",
        "run_id": ctx.agent_run_id,
        "reason": "QUESTION",
        "question": "which region?",
        "created_at": "2026-09-28T08:00:00Z",
    }
    interrupt = Interrupt.model_validate(stored)
    assert interrupt.ui == "approve" and interrupt.options == [] and interrupt.assignee is None
