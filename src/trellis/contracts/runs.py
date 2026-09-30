"""Runs, the events they emit, the interrupts that pause them, and the schedules that start
them (PLATFORM-DESIGN §4, §7, §10). Types only: a run store and an event sink are ports in
:mod:`trellis.contracts.ports`; agent-runs keeps runs and schedules.

A chat turn, a week-long cowork run and a 6 a.m. schedule differ in who drives and where
state lives, not in their contract: the same :class:`RunEvent` stream leaves every surface,
the same :class:`Interrupt` pauses every framework, the same :class:`RunRecord` outlives the
process. Everything here that leaves the process (an event's ``data``, an interrupt's
``awaiting()``) is unredacted: a surface passes it through a ``TelemetryRedactor`` before a
UI, a webhook or another tenant's agent sees it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trellis.contracts.artifacts import ArtifactRef
from trellis.contracts.context import AgentExecutionContext
from trellis.contracts.errors import AgentError, AgentPaused
from trellis.contracts.feedback import Feedback, FeedbackSource, FeedbackTargetKind, FeedbackVerdict
from trellis.contracts.ids import new_id, now
from trellis.contracts.messages import AgentRequest, AgentStatus
from trellis.contracts.tool import ToolCall

# --------------------------------------------------------------------------- run records


class RunStatus(StrEnum):
    """Where a run is. ``QUEUED``, ``RUNNING`` and ``PAUSED`` can still move on; the rest are
    how a run ended and spell the same as :class:`AgentStatus`, so a run store and a response
    agree.

    The lifecycle is ``QUEUED -> RUNNING -> (PAUSED <-> RUNNING)* -> final``. A run started
    in process skips the queue; a worker whose lease lapses hands its run back to the queue,
    and a paused durable run is queued again for a worker to resume. :meth:`can_become` is
    the one place a store checks a transition."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def final(self) -> bool:
        return self not in _TRANSITIONS

    def can_become(self, status: RunStatus) -> bool:
        """Whether a run in this status may move to ``status``. A final status moves nowhere."""
        return status in _TRANSITIONS.get(self, frozenset())

    @classmethod
    def from_agent_status(cls, status: AgentStatus | str) -> RunStatus:
        return cls(str(status))


_ENDINGS = frozenset(
    {
        RunStatus.SUCCESS,
        RunStatus.PARTIAL,
        RunStatus.ERROR,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
        RunStatus.REJECTED,
    }
)
#: Every live status and where it may go. A queued run nobody claimed can still be
#: cancelled or run out of time; a paused run is resumed (in process, or queued for a
#: worker), cancelled, or times out waiting, but never ends without running again.
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.TIMEOUT}),
    RunStatus.RUNNING: _ENDINGS | {RunStatus.QUEUED, RunStatus.PAUSED},
    RunStatus.PAUSED: frozenset(
        {RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.TIMEOUT}
    ),
}
#: where a run's record begins
_ENTRIES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})
#: endings that carry an error
_FAILED = frozenset({RunStatus.ERROR, RunStatus.TIMEOUT, RunStatus.REJECTED})


class RunStart(BaseModel):
    """What starting a run records. ``run_id`` comes from the caller's context, or is minted
    here when a service queues a run nobody is waiting on (a schedule firing);
    ``idempotency_key`` makes a retried start return the same run; ``on_behalf_of`` is the
    person a scheduled run acts for, fixed when the schedule was made; ``webhook_url`` is
    captured now because the caller who wants to know is present now and not when the run
    pauses at 3 a.m."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(default_factory=lambda: new_id("run_"))
    tenant_id: str
    agent_id: str
    parent_run_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    on_behalf_of: str | None = None
    input: Any = None
    deadline: AwareDatetime | None = None
    idempotency_key: str | None = None
    webhook_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, request: AgentRequest, *, webhook_url: str | None = None) -> Self:
        context = request.context
        return cls(
            run_id=context.agent_run_id,
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            parent_run_id=context.parent_agent_run_id,
            thread_id=context.thread_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            input=request.input,
            deadline=context.deadline,
            idempotency_key=context.idempotency_key("run", "start"),
            webhook_url=webhook_url,
            metadata=dict(request.metadata),
        )


# --------------------------------------------------------------------------- interrupts


class InterruptReason(StrEnum):
    """Why a run stopped to ask."""

    #: the agent asked a person something (an ``AgentPaused``)
    QUESTION = "QUESTION"
    #: a tool call needs approval (policy said ``require_approval``, or the tool is not
    #: auto-executable)
    APPROVAL = "APPROVAL"
    #: a person reviews and may correct a draft; ``expects`` is the shape of the correction
    REVIEW = "REVIEW"
    #: a person picks one of ``options``
    CHOICE = "CHOICE"
    #: a credential or consent the person must supply
    AUTH = "AUTH"


class Interrupt(BaseModel):
    """The framework-neutral record of a paused run: what is asked, what shape an answer
    takes, what the UI needs, who must answer by when, and the tool call under approval when
    there is one. Surfaces translate it (AG-UI ``RunFinished{outcome: interrupt}``, A2A
    ``input-required``, a webhook) and the run store keeps it as ``awaiting``.

    ``ui`` is the control a surface renders; ``assignee`` is a principal (``user:u1``,
    ``role:procurement``) an inbox is filtered by; past ``deadline`` the run goes to
    ``escalate_to``, or times out when nobody is named. Data too large to travel with the
    question (a table, a diff) goes by reference in ``payload_ref``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interrupt_id: str = Field(default_factory=lambda: new_id("int_"))
    tenant_id: str
    run_id: str
    reason: InterruptReason = InterruptReason.QUESTION
    question: str
    ui: Literal["approve", "form", "table", "diff", "choice"] = "approve"
    expects: dict[str, Any] | None = None
    options: list[str] = Field(default_factory=list)
    payload: dict[str, Any] | None = None
    payload_ref: ArtifactRef | None = None
    tool_call: ToolCall | None = None
    assignee: str | None = None
    deadline: AwareDatetime | None = None
    escalate_to: str | None = None
    created_at: AwareDatetime = Field(default_factory=now)

    @field_validator("question")
    @classmethod
    def _a_question_is_asked(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an interrupt needs a question; a pause nobody can answer is a hang")
        return value

    @model_validator(mode="after")
    def _the_reason_carries_what_it_asks_about(self) -> Self:
        if self.reason is InterruptReason.APPROVAL and self.tool_call is None:
            raise ValueError("an APPROVAL interrupt carries the tool call under approval")
        if self.reason is InterruptReason.CHOICE and not self.options:
            raise ValueError("a CHOICE interrupt carries the options to choose from")
        if self.reason is InterruptReason.REVIEW and self.expects is None:
            raise ValueError("a REVIEW interrupt says what a correction looks like in expects")
        if self.escalate_to is not None and self.deadline is None:
            raise ValueError("escalate_to needs a deadline; without one it never happens")
        return self

    @classmethod
    def from_paused(
        cls,
        paused: AgentPaused,
        *,
        context: AgentExecutionContext,
        reason: InterruptReason = InterruptReason.QUESTION,
        **fields: Any,
    ) -> Self:
        """The interrupt a pause raised in ``context`` becomes; ``fields`` add what the pause
        does not carry (the tool call, the UI, the assignee and deadline...)."""
        return cls(
            tenant_id=context.tenant_id,
            run_id=context.agent_run_id,
            reason=reason,
            question=paused.question,
            expects=paused.expects,
            payload=paused.payload,
            **fields,
        )

    def awaiting(self) -> dict[str, Any]:
        """What the run store keeps and the webhook delivers: the whole interrupt as JSON
        (a copy: mutating it never reaches the record), which ``Interrupt.model_validate``
        reads back. It carries the tool call's arguments unredacted."""
        return self.model_dump(mode="json", exclude_none=True)


class InterruptDecision(StrEnum):
    #: the person answered the question
    ANSWER = "ANSWER"
    #: the tool call may run as proposed
    APPROVE = "APPROVE"
    #: the tool call must not run
    REJECT = "REJECT"
    #: the tool call runs with the person's edited arguments (``payload``)
    EDIT = "EDIT"
    #: the run is abandoned
    CANCEL = "CANCEL"


#: Every decision about a tool call is also a piece of feedback (design §7): the two
#: records meet in one table, and the planner stops proposing what people keep rejecting.
_DECISION_VERDICT: dict[InterruptDecision, FeedbackVerdict] = {
    InterruptDecision.APPROVE: FeedbackVerdict.APPROVE,
    InterruptDecision.REJECT: FeedbackVerdict.REJECT,
    InterruptDecision.EDIT: FeedbackVerdict.EDIT,
}


class InterruptResolution(BaseModel):
    """How an interrupt was answered. The paused run continues (``RunStore.resumed`` moves it
    back to ``RUNNING`` on the same run id, the next attempt); the resolution names the
    interrupt and the run so a store can refuse an answer to the wrong question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interrupt_id: str
    run_id: str
    decision: InterruptDecision
    answer: Any = None
    reviewer: str | None = None
    payload: dict[str, Any] | None = None
    resolved_at: AwareDatetime = Field(default_factory=now)

    @model_validator(mode="after")
    def _an_edit_says_what_changed(self) -> Self:
        if self.decision == InterruptDecision.EDIT and not self.payload:
            raise ValueError("an EDIT decision carries the edited arguments in payload")
        return self

    def resolves(self, interrupt: Interrupt) -> bool:
        return self.interrupt_id == interrupt.interrupt_id and self.run_id == interrupt.run_id

    def to_feedback(self, interrupt: Interrupt, context: AgentExecutionContext) -> Feedback | None:
        """The feedback record an approve, reject or edit of a tool call is; an answer or a
        cancellation judges nothing and yields none. The resolution, the interrupt and the
        context must name the same run: feedback is attributed to the run that paused."""
        if not self.resolves(interrupt):
            raise ValueError("the resolution answers a different interrupt or run")
        if context.agent_run_id != interrupt.run_id or context.tenant_id != interrupt.tenant_id:
            raise ValueError("the context is not the paused run's")
        verdict = _DECISION_VERDICT.get(self.decision)
        if verdict is None or interrupt.tool_call is None:
            return None
        call = interrupt.tool_call
        return Feedback.for_context(
            context,
            target_kind=FeedbackTargetKind.TOOL_CALL,
            target_id=call.idempotency_key or interrupt.interrupt_id,
            verdict=verdict,
            source=FeedbackSource.INTERRUPT,
            reviewer=self.reviewer,
            correction=self.payload if self.decision == InterruptDecision.EDIT else None,
            metadata={
                "interrupt_id": interrupt.interrupt_id,
                "tool": call.tool,
                "target": "tool_call" if call.idempotency_key else "interrupt",
            },
        )


class RunRecord(RunStart):
    """A run as a run store keeps it: the start plus where it is. ``awaiting`` is the
    interrupt a paused run waits on and nothing else; ``error`` belongs to a failed ending.
    ``checkpoint`` is opaque executor state (the resume journal: answered asks, completed tool
    outputs, a framework's own resume state) written when the run pauses, returned on read and
    claim so another worker resumes without repeating side effects, and cleared when the run
    finishes; the service bounds its size. Read back from a store, so unknown columns are
    ignored rather than refused."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    status: RunStatus = RunStatus.RUNNING
    output: Any = None
    error: AgentError | None = None
    awaiting: Interrupt | None = None
    last_resolution: InterruptResolution | None = None
    checkpoint: dict[str, Any] | None = None
    attempt: int = Field(default=1, ge=1)
    created_at: AwareDatetime = Field(default_factory=now)
    updated_at: AwareDatetime = Field(default_factory=now)

    @model_validator(mode="after")
    def _status_and_payloads_agree(self) -> Self:
        paused = self.status == RunStatus.PAUSED
        if paused != (self.awaiting is not None):
            raise ValueError("a PAUSED run waits on an interrupt; no other run does")
        if self.awaiting is not None and (
            self.awaiting.run_id != self.run_id or self.awaiting.tenant_id != self.tenant_id
        ):
            raise ValueError("the interrupt belongs to another run")
        if self.error is not None and self.status not in _FAILED:
            raise ValueError(f"a {self.status.value} run carries no error")
        if self.checkpoint is not None and self.final:
            raise ValueError("a finished run carries no checkpoint")
        return self

    @property
    def final(self) -> bool:
        return self.status.final

    @classmethod
    def from_start(cls, start: RunStart, *, status: RunStatus = RunStatus.RUNNING) -> Self:
        """The record of a run just started (``RUNNING``) or put on the queue (``QUEUED``)."""
        if status not in _ENTRIES:
            raise ValueError(f"a run starts QUEUED or RUNNING, not {status.value}")
        return cls.model_validate({**start.model_dump(), "status": status})


# --------------------------------------------------------------------------- events


class RunEventType(StrEnum):
    """The event vocabulary every surface speaks. The AG-UI names are spelled as AG-UI
    spells them; ``CONTEXT_LOADED`` and ``INTERRUPT`` are the platform's own, which an AG-UI
    transport carries as ``CUSTOM`` events."""

    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
    CONTEXT_LOADED = "CONTEXT_LOADED"
    INTERRUPT = "INTERRUPT"
    RAW = "RAW"
    CUSTOM = "CUSTOM"


class RunOutcome(StrEnum):
    """How a run finished, as ``RUN_FINISHED`` reports it. Lower case because AG-UI's
    ``RunFinished.outcome`` spells ``success`` and ``interrupt`` that way; the rest are the
    platform's endings in the same case, and an AG-UI transport reports ``error``,
    ``rejected``, ``cancelled`` and ``timeout`` as ``RUN_ERROR``."""

    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"
    REJECTED = "rejected"
    INTERRUPT = "interrupt"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @classmethod
    def from_status(cls, status: RunStatus | AgentStatus | str) -> RunOutcome:
        """The outcome of a settled status; a run that has not ended has no outcome."""
        settled = RunStatus.from_agent_status(status)
        if settled not in _OUTCOMES:
            raise ValueError(f"a {settled.value} run has no outcome yet")
        return _OUTCOMES[settled]


_OUTCOMES: dict[RunStatus, RunOutcome] = {
    RunStatus.SUCCESS: RunOutcome.SUCCESS,
    RunStatus.PARTIAL: RunOutcome.PARTIAL,
    RunStatus.ERROR: RunOutcome.ERROR,
    RunStatus.REJECTED: RunOutcome.REJECTED,
    RunStatus.PAUSED: RunOutcome.INTERRUPT,
    RunStatus.CANCELLED: RunOutcome.CANCELLED,
    RunStatus.TIMEOUT: RunOutcome.TIMEOUT,
}

_TEXT_EVENTS = frozenset(
    {
        RunEventType.TEXT_MESSAGE_START,
        RunEventType.TEXT_MESSAGE_CONTENT,
        RunEventType.TEXT_MESSAGE_END,
    }
)
_TOOL_EVENTS = frozenset(
    {
        RunEventType.TOOL_CALL_START,
        RunEventType.TOOL_CALL_ARGS,
        RunEventType.TOOL_CALL_END,
        RunEventType.TOOL_CALL_RESULT,
    }
)


class RunEvent(BaseModel):
    """One event of a run's stream. ``sequence`` orders events within an attempt of a run
    (a sink may receive them out of order and dedupes on run, attempt and sequence);
    ``tenant_id`` is what a hub fans out by, never the caller-chosen ``thread_id`` alone;
    ``data`` carries the type's payload as the surface renders it (text delta, tool
    arguments, state patch, the interrupt) and is unredacted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id("evt_"))
    type: RunEventType
    tenant_id: str
    run_id: str
    sequence: int = Field(ge=0)
    attempt: int = Field(default=1, ge=1)
    thread_id: str | None = None
    timestamp: AwareDatetime = Field(default_factory=now)
    step: str | None = None
    message_id: str | None = None
    tool_call_id: str | None = None
    outcome: RunOutcome | None = None
    error: AgentError | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _the_type_and_its_payload_agree(self) -> Self:
        finished = self.type == RunEventType.RUN_FINISHED
        if finished and self.outcome is None:
            raise ValueError("RUN_FINISHED names its outcome")
        if not finished and self.outcome is not None:
            raise ValueError(f"{self.type.value} carries no outcome")
        if self.outcome == RunOutcome.INTERRUPT and "interrupt" not in self.data:
            raise ValueError("a run finished with an interrupt carries the interrupt")
        if self.type in _TEXT_EVENTS and not self.message_id:
            raise ValueError(f"{self.type.value} names its message")
        if self.type in _TOOL_EVENTS and not self.tool_call_id:
            raise ValueError(f"{self.type.value} names its tool call")
        return self

    # -- constructors: the run's identity comes from its context ----------------------
    @classmethod
    def of(
        cls,
        context: AgentExecutionContext,
        type: RunEventType | str,
        sequence: int,
        *,
        attempt: int = 1,
        **fields: Any,
    ) -> Self:
        return cls(
            type=RunEventType(type),
            tenant_id=context.tenant_id,
            run_id=context.agent_run_id,
            thread_id=context.thread_id,
            sequence=sequence,
            attempt=attempt,
            **fields,
        )

    @classmethod
    def started(cls, context: AgentExecutionContext, sequence: int, **data: Any) -> Self:
        return cls.of(context, RunEventType.RUN_STARTED, sequence, data=data)

    @classmethod
    def finished(
        cls,
        context: AgentExecutionContext,
        outcome: RunOutcome | str,
        sequence: int,
        *,
        error: AgentError | None = None,
        interrupt: Interrupt | None = None,
        **data: Any,
    ) -> Self:
        outcome = RunOutcome(outcome)
        if (outcome == RunOutcome.INTERRUPT) != (interrupt is not None):
            raise ValueError("an interrupt outcome carries the interrupt, and only that one does")
        if interrupt is not None:
            data["interrupt"] = interrupt.awaiting()
        return cls.of(
            context, RunEventType.RUN_FINISHED, sequence, outcome=outcome, error=error, data=data
        )

    @classmethod
    def text(
        cls,
        context: AgentExecutionContext,
        type: RunEventType | str,
        message_id: str,
        sequence: int,
        *,
        delta: str | None = None,
        role: str = "assistant",
    ) -> Self:
        type = RunEventType(type)
        if type not in _TEXT_EVENTS:
            raise ValueError(f"{type.value} is not a text message event")
        data: dict[str, Any] = {"role": role}
        if delta is not None:
            data["delta"] = delta
        return cls.of(context, type, sequence, message_id=message_id, data=data)

    @classmethod
    def tool(
        cls,
        context: AgentExecutionContext,
        type: RunEventType | str,
        tool_call_id: str,
        sequence: int,
        **data: Any,
    ) -> Self:
        type = RunEventType(type)
        if type not in _TOOL_EVENTS:
            raise ValueError(f"{type.value} is not a tool call event")
        return cls.of(context, type, sequence, tool_call_id=tool_call_id, data=data)

    @classmethod
    def interrupt(cls, context: AgentExecutionContext, interrupt: Interrupt, sequence: int) -> Self:
        return cls.of(context, RunEventType.INTERRUPT, sequence, data=interrupt.awaiting())

    @classmethod
    def custom(cls, context: AgentExecutionContext, name: str, sequence: int, **data: Any) -> Self:
        return cls.of(context, RunEventType.CUSTOM, sequence, data={"name": name, **data})


# --------------------------------------------------------------------------- schedules


class ScheduleSpec(BaseModel):
    """A standing intent ("every weekday at 8"): which agent, what input, on whose behalf.
    ``cadence`` is a cron expression or one of agent-runs' named buckets (``hourly``,
    ``daily``, ``weekly``, ``weekdays``, ``manual``) evaluated in ``timezone``; agent-runs
    validates the expression and its floor. ``on_behalf_of`` is required: a run fired with
    nobody present acts as the person who set the schedule, never wider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    name: str = Field(max_length=200)
    cadence: str
    timezone: str = "UTC"
    on_behalf_of: str
    input: Any = None
    workspace_id: str | None = None
    enabled: bool = True
    webhook_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "cadence", "on_behalf_of")
    @classmethod
    def _is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _is_a_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class Schedule(ScheduleSpec):
    """A schedule as agent-runs keeps it. Read back from a store, so unknown columns are
    ignored rather than refused.

    ``created_by`` is the principal whose credential created it, taken from that credential
    and never from the spec (which refuses the field): a self-asserted author is no audit
    trail. ``last_run_id`` is the run the last fire queued. ``consecutive_failures`` and
    ``last_error`` count fires that could not queue a run, not runs that failed; while a
    retryable failure backs off, the schedule stays armed for the same tick but is not
    fired again before ``retry_after``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    schedule_id: str = Field(default_factory=lambda: new_id("sch_"))
    created_by: str | None = None
    next_fire_at: AwareDatetime | None = None
    last_fired_at: AwareDatetime | None = None
    last_run_id: str | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: dict[str, Any] | None = None
    retry_after: AwareDatetime | None = None
    created_at: AwareDatetime = Field(default_factory=now)
    updated_at: AwareDatetime = Field(default_factory=now)

    @classmethod
    def from_spec(cls, spec: ScheduleSpec, **fields: Any) -> Self:
        """The record of a spec; ``fields`` (an id, the next fire time) win over the spec."""
        return cls.model_validate({**spec.model_dump(), **fields})
