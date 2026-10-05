"""Runs, the events they emit, the interrupts that pause them, and the schedules that start
them (PLATFORM-DESIGN §4, §7, §10). Types only: an event sink is a port in
:mod:`trellis.contracts.ports`; agent-runs keeps runs and schedules, and its client is
``trellis.runs.RunsClient``.

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
from trellis.contracts.ids import new_id, now, stable_id
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
    person a scheduled run acts for, fixed when the schedule was made. ``priority`` and
    ``concurrency_key`` say how a queued run waits its turn: higher priority is claimed first,
    and runs sharing a key run a few at a time. Notifications are not part of a run:
    agent-runs delivers them to the tenant's webhook subscriptions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(
        default_factory=lambda: new_id("run_"),
        description="Run id: the caller's context agent_run_id, or 'run_' plus 32 hex "
        "characters minted here.",
        examples=["run_4be0643f1d98573b97cdcfd8884e2d4f"],
    )
    tenant_id: str = Field(description="Tenant that owns the run; every read is scoped to it.")
    agent_id: str = Field(
        description="Id of the agent that executes the run.", examples=["refunds"]
    )
    parent_run_id: str | None = Field(
        default=None,
        description="Run that started this one as a nested agent run; None for a top-level run.",
    )
    thread_id: str | None = Field(
        default=None, description="Conversation thread the run belongs to; None outside one."
    )
    user_id: str | None = Field(
        default=None, description="End user the run serves; None when there is none."
    )
    workspace_id: str | None = Field(
        default=None, description="Workspace within the tenant; None when unused."
    )
    on_behalf_of: str | None = Field(
        default=None,
        description="Principal a scheduled run acts for, fixed when the schedule was made; "
        "None for a run somebody started.",
        examples=["user:u1"],
    )
    input: Any = Field(default=None, description="The agent's input, any JSON value, as given.")
    deadline: AwareDatetime | None = Field(
        default=None,
        description="Absolute time the run must end by (ISO 8601, timezone-aware); None for "
        "no deadline.",
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Most working time the run may take, in seconds: time RUNNING, across "
        "attempts, not time queued or waiting for a person. Past it the run ends TIMEOUT. "
        "None: no limit of its own (a service maximum may still apply).",
        examples=[600],
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Key that makes a retried start return the same run instead of a second "
        "one; from_request derives it from the context.",
    )
    agent_version: str | None = Field(
        default=None,
        max_length=128,
        description="Version of the agent's code that started the run (a release or deploy "
        "id), kept for audit and to tell which code a resumed run continues on; None when "
        "not known.",
        examples=["2026.10.05-3f2a1c"],
    )
    priority: int = Field(
        default=0,
        ge=-1000,
        le=1000,
        description="Claim order among the tenant's queued runs: higher first, then the oldest; "
        "-1000 to 1000, default 0.",
        examples=[10],
    )
    concurrency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Runs of the tenant sharing this key run only a few at a time (the run "
        "store's limit, one unless its operator says otherwise); the rest wait QUEUED. None: "
        "no such limit.",
        examples=["thread:chat-42"],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object kept with the run; the contract never reads it.",
    )

    @classmethod
    def from_request(cls, request: AgentRequest) -> Self:
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


#: The control a surface renders for an interrupt.
InterruptUI = Literal["approve", "form", "table", "diff", "choice"]
#: How far a decision reaches: this call only, or calls like it for the rest of the run.
InterruptRemember = Literal["once", "run"]


class Option(BaseModel):
    """One choice an interrupt offers. ``value`` is what an answer that picks it carries;
    ``label`` and ``description`` are what a person sees. A plain string among
    ``Interrupt.options`` is an option whose value is that string, shown as it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(
        description="What an answer that picks this option carries; not blank.",
        examples=["eu"],
    )
    label: str | None = Field(
        default=None,
        description="What a person sees for it; None shows the value.",
        examples=["Europe (Frankfurt)"],
    )
    description: str | None = Field(
        default=None, description="A longer explanation shown with the label; None for none."
    )

    @field_validator("value")
    @classmethod
    def _a_value_is_given(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an option needs a value an answer can carry")
        return value


class Interrupt(BaseModel):
    """The framework-neutral record of a paused run: what is asked, what shape an answer
    takes, what the UI needs, who must answer by when, and the tool call under approval when
    there is one. Surfaces translate it (AG-UI ``RunFinished{outcome: interrupt}``, A2A
    ``input-required``, a webhook) and the run store keeps it as ``awaiting``.

    ``ui`` is the control a surface renders; ``component`` names the asker's own screen, which
    a surface that has it renders instead (with ``props`` as they are), ``ui`` staying the
    fallback; ``ui_schema`` gives widget hints for the form ``expects`` describes.
    ``options`` are plain strings or :class:`Option` objects; with ``multiple`` the answer is a
    list of their values. ``assignee`` is a principal (``user:u1``, ``role:procurement``) an
    inbox is filtered by; past ``deadline`` the run goes to ``escalate_to``, or times out when
    nobody is named. Data too large to travel with the question (a table, a diff) goes by
    reference in ``payload_ref``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interrupt_id: str = Field(
        default_factory=lambda: new_id("int_"),
        description="Interrupt id, 'int_' plus 32 hex characters unless given.",
        examples=["int_9a1c6f2e0b7d4e8f9a1c6f2e0b7d4e8f"],
    )
    tenant_id: str = Field(description="Tenant of the paused run; must equal the run's.")
    run_id: str = Field(description="Id of the paused run.")
    reason: InterruptReason = Field(
        default=InterruptReason.QUESTION,
        description="Why the run stopped to ask. APPROVAL carries tool_call, CHOICE carries "
        "options, REVIEW carries expects.",
    )
    question: str = Field(
        description="What the person is asked, shown as is; must not be blank.",
        examples=["Approve a EUR 240 refund for order 91?"],
    )
    ui: InterruptUI = Field(
        default="approve",
        description="Control a surface renders: approve (yes/no), form (fields shaped by "
        "expects), table, diff, or choice (one of options); the fallback when component is set.",
    )
    expects: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema of an acceptable answer, so a UI renders a control rather "
        "than a text box; required for REVIEW.",
        examples=[{"type": "boolean"}],
    )
    options: list[str | Option] = Field(
        default_factory=list,
        description="Choices offered, in display order: plain strings or Option objects "
        "(value, label, description), with distinct values; required (non-empty) for CHOICE.",
        examples=[["EU", {"value": "us", "label": "United States"}]],
    )
    multiple: bool = Field(
        default=False,
        description="Whether several may be picked: the answer is then a list of distinct "
        "option values (or what expects describes); needs options or expects.",
    )
    ui_schema: dict[str, Any] | None = Field(
        default=None,
        description="Widget hints for the form expects describes, in the react-jsonschema-form "
        "uiSchema convention; None for the surface's defaults.",
        examples=[{"reason": {"ui:widget": "textarea"}}],
    )
    component: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Name of the asker's own screen, rendered instead of ui by a surface that "
        "has it; needs no expects, but an answer still fits expects when given.",
        examples=["refund-review"],
    )
    props: dict[str, Any] | None = Field(
        default=None,
        description="Data for component, passed to it as is and never interpreted (JSON "
        "object, unredacted); needs component.",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="Extra data the UI needs to show the question (JSON object, unredacted).",
    )
    payload_ref: ArtifactRef | None = Field(
        default=None,
        description="Reference to data too large to travel with the question (a table, a "
        "diff), stored as a run artifact.",
    )
    tool_call: ToolCall | None = Field(
        default=None, description="The tool call under approval; required for APPROVAL."
    )
    assignee: str | None = Field(
        default=None,
        description="Principal who must answer, as kind:id; inboxes filter on it. None when "
        "unassigned.",
        examples=["user:u1", "role:procurement"],
    )
    deadline: AwareDatetime | None = Field(
        default=None,
        description="When an answer is due (ISO 8601, timezone-aware); past it the run goes "
        "to escalate_to, or times out. None for no deadline.",
    )
    escalate_to: str | None = Field(
        default=None,
        description="Principal the interrupt passes to once deadline has passed, as kind:id; "
        "requires deadline.",
        examples=["role:finance-lead"],
    )
    created_at: AwareDatetime = Field(
        default_factory=now,
        description="When the run paused (ISO 8601, timezone-aware; UTC unless given).",
    )

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
        values = self.option_values
        if len(set(values)) != len(values):
            raise ValueError("options have distinct values, or an answer cannot tell them apart")
        if self.multiple and not values and self.expects is None:
            raise ValueError("multiple needs options to pick from, or expects to describe them")
        if self.props is not None and self.component is None:
            raise ValueError("props are a component's; name the component")
        return self

    @property
    def option_values(self) -> list[str]:
        """The values an answer may pick, in display order (a plain string is its own)."""
        return [option if isinstance(option, str) else option.value for option in self.options]

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
    """How an interrupt was answered. The paused run continues (``RunsClient.resume`` in
    ``trellis.runs`` moves it back to ``RUNNING`` on the same run id, the next attempt); the
    resolution names the interrupt and the run so agent-runs can refuse an answer to the wrong
    question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interrupt_id: str = Field(description="Id of the interrupt being answered.")
    run_id: str = Field(description="Id of the paused run; must be the interrupt's run_id.")
    decision: InterruptDecision = Field(
        description="What was decided. EDIT carries the edited arguments in payload."
    )
    answer: Any = Field(
        default=None,
        description="The answer to a question, any JSON value shaped by the interrupt's "
        "expects; None for a decision about a tool call.",
    )
    reviewer: str | None = Field(
        default=None,
        description="Principal who decided, as kind:id; carried into the feedback record.",
        examples=["user:u1"],
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="For EDIT, the tool call's edited arguments (required, non-empty); "
        "otherwise optional extra data.",
    )
    comment: str | None = Field(
        default=None,
        max_length=4000,
        description="The reviewer's remark on the decision, whatever it is; carried into the "
        "feedback record. None for none.",
        examples=["Fine this once; refunds over EUR 500 need finance."],
    )
    remember: InterruptRemember = Field(
        default="once",
        description="once: the decision covers this call only. run: approve calls like it for "
        "the rest of the run without asking again (APPROVE of a tool call only).",
    )
    resolved_at: AwareDatetime = Field(
        default_factory=now,
        description="When it was answered (ISO 8601, timezone-aware; UTC unless given).",
    )

    @model_validator(mode="after")
    def _an_edit_says_what_changed(self) -> Self:
        if self.decision == InterruptDecision.EDIT and not self.payload:
            raise ValueError("an EDIT decision carries the edited arguments in payload")
        if self.remember == "run" and self.decision != InterruptDecision.APPROVE:
            raise ValueError("only an approval is remembered for the run")
        return self

    def resolves(self, interrupt: Interrupt) -> bool:
        return self.interrupt_id == interrupt.interrupt_id and self.run_id == interrupt.run_id

    @property
    def feedback_id(self) -> str:
        """The one id the feedback for this decision carries, however often it is sent: an
        interrupt is answered once, so a retried or duplicated send is stored once and
        counted once in the approval patterns learned from it."""
        return stable_id(self.run_id, self.interrupt_id, prefix="fb_")

    def to_feedback(self, interrupt: Interrupt, context: AgentExecutionContext) -> Feedback | None:
        """The feedback record an approve, reject or edit of a tool call is (``metadata`` names
        the tool and the arguments it was asked about, which approval patterns are learned
        from); an answer or a cancellation judges nothing and yields none. The resolution, the
        interrupt and the context must name the same run: feedback is attributed to the run
        that paused."""
        if not self.resolves(interrupt):
            raise ValueError("the resolution answers a different interrupt or run")
        if context.agent_run_id != interrupt.run_id or context.tenant_id != interrupt.tenant_id:
            raise ValueError("the context is not the paused run's")
        verdict = _DECISION_VERDICT.get(self.decision)
        if verdict is None or interrupt.tool_call is None:
            return None
        call = interrupt.tool_call
        # How long the person took: a two-second approval of a large diff is a weak label,
        # and a reader of the patterns can weigh it so. Never negative on a skewed clock.
        took = max(0.0, (self.resolved_at - interrupt.created_at).total_seconds())
        return Feedback.for_context(
            context,
            feedback_id=self.feedback_id,
            target_kind=FeedbackTargetKind.TOOL_CALL,
            target_id=call.idempotency_key or interrupt.interrupt_id,
            verdict=verdict,
            source=FeedbackSource.INTERRUPT,
            reviewer=self.reviewer,
            correction=self.payload if self.decision == InterruptDecision.EDIT else None,
            comment=self.comment,
            metadata={
                "interrupt_id": interrupt.interrupt_id,
                "tool": call.tool,
                "args": call.args,
                "target": "tool_call" if call.idempotency_key else "interrupt",
                "decision_seconds": round(took, 3),
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

    status: RunStatus = Field(
        default=RunStatus.RUNNING,
        description="Where the run is; PAUSED exactly when awaiting is set.",
    )
    output: Any = Field(
        default=None,
        description="What the agent returned, any JSON value; None until it has returned.",
    )
    error: AgentError | None = Field(
        default=None,
        description="Why the run failed; only on ERROR, TIMEOUT and REJECTED, None otherwise.",
    )
    awaiting: Interrupt | None = Field(
        default=None,
        description="The interrupt a PAUSED run waits on, for the same run and tenant; None "
        "for any other status.",
    )
    last_resolution: InterruptResolution | None = Field(
        default=None,
        description="How the most recent interrupt was answered; None until one has been.",
    )
    checkpoint: dict[str, Any] | None = Field(
        default=None,
        description="Opaque executor state (resume journal) written with a pause and "
        "returned on read and claim; never on a final run. Size bounded by the service.",
    )
    attempt: int = Field(
        default=1,
        ge=1,
        description="1-based attempt number; one more each time the run resumes or is "
        "re-queued after a lapsed lease.",
    )
    worked_seconds: float = Field(
        default=0,
        ge=0,
        description="Working time so far, in seconds: time RUNNING across every attempt, "
        "what timeout_seconds limits.",
    )
    created_at: AwareDatetime = Field(
        default_factory=now,
        description="When the run was created (ISO 8601, timezone-aware; UTC unless given).",
    )
    updated_at: AwareDatetime = Field(
        default_factory=now,
        description="When the record last changed (ISO 8601, timezone-aware; UTC unless given).",
    )

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

    event_id: str = Field(
        default_factory=lambda: new_id("evt_"),
        description="Event id, 'evt_' plus 32 hex characters unless given.",
    )
    type: RunEventType = Field(
        description="Event type: AG-UI's spelling, plus the platform's CONTEXT_LOADED and "
        "INTERRUPT."
    )
    tenant_id: str = Field(description="Tenant of the run; what a hub fans events out by.")
    run_id: str = Field(description="Id of the run that emitted the event.")
    sequence: int = Field(
        ge=0,
        description="0-based position within the run's attempt; sinks order and dedupe on "
        "run_id, attempt and sequence.",
    )
    attempt: int = Field(
        default=1, ge=1, description="1-based attempt of the run that emitted the event."
    )
    thread_id: str | None = Field(
        default=None, description="Conversation thread of the run; None outside one."
    )
    timestamp: AwareDatetime = Field(
        default_factory=now,
        description="When the event was emitted (ISO 8601, timezone-aware; UTC unless given).",
    )
    step: str | None = Field(
        default=None,
        description="Name of the step a STEP_STARTED or STEP_FINISHED event marks; None for "
        "other events.",
    )
    message_id: str | None = Field(
        default=None,
        description="Message the text belongs to; required on TEXT_MESSAGE_* events.",
    )
    tool_call_id: str | None = Field(
        default=None,
        description="Tool call the event belongs to; required on TOOL_CALL_* events.",
    )
    outcome: RunOutcome | None = Field(
        default=None,
        description="How the run finished; required on RUN_FINISHED and refused on any other type.",
    )
    error: AgentError | None = Field(
        default=None,
        description="The failure a RUN_ERROR or a failed RUN_FINISHED reports; None otherwise.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="The type's payload as a surface renders it (text delta, tool arguments, "
        "state patch, the interrupt); unredacted JSON object.",
    )

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
    nobody present acts as the person who set the schedule, never wider. ``timeout_seconds``
    and ``agent_version`` are copied into the :class:`RunStart` of every fire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(description="Tenant that owns the schedule.")
    agent_id: str = Field(description="Id of the agent each fire starts a run of.")
    name: str = Field(
        max_length=200,
        description="Human-readable label, 1 to 200 characters, trimmed.",
        examples=["Weekday supplier digest"],
    )
    cadence: str = Field(
        description="When it fires: a cron expression or a named bucket (hourly, daily, "
        "weekly, weekdays, manual), evaluated in timezone.",
        examples=["0 8 * * 1-5", "daily", "manual"],
    )
    timezone: str = Field(
        default="UTC",
        description="IANA time zone the cadence is evaluated in.",
        examples=["UTC", "Europe/Berlin"],
    )
    on_behalf_of: str = Field(
        description="Principal every fired run acts for, never wider; not blank.",
        examples=["user:u1"],
    )
    input: Any = Field(
        default=None, description="Input each fired run starts with, any JSON value."
    )
    workspace_id: str | None = Field(
        default=None, description="Workspace within the tenant; None when unused."
    )
    enabled: bool = Field(
        default=True, description="Whether it fires; a disabled schedule is kept but skipped."
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Most working time each fired run may take, in seconds, copied into its "
        "RunStart.timeout_seconds; None: no limit of its own.",
        examples=[600],
    )
    agent_version: str | None = Field(
        default=None,
        max_length=128,
        description="Version of the agent's code that set the schedule, copied into each fired "
        "run's RunStart.agent_version; None when not known.",
        examples=["2026.10.05-3f2a1c"],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON object kept with the schedule; the contract never reads it.",
    )

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

    schedule_id: str = Field(
        default_factory=lambda: new_id("sch_"),
        description="Schedule id, 'sch_' plus 32 hex characters unless given.",
    )
    created_by: str | None = Field(
        default=None,
        description="Principal whose credential created the schedule, set by the service "
        "from that credential, never by the caller.",
    )
    next_fire_at: AwareDatetime | None = Field(
        default=None,
        description="Next time it is due (ISO 8601, timezone-aware); None when nothing is "
        "due (a manual cadence).",
    )
    last_fired_at: AwareDatetime | None = Field(
        default=None,
        description="Occurrence the last successful fire was for (ISO 8601, timezone-aware); "
        "None before the first.",
    )
    last_run_id: str | None = Field(
        default=None, description="Id of the run the last successful fire queued."
    )
    consecutive_failures: int = Field(
        default=0,
        ge=0,
        description="Fires in a row that could not queue a run (not runs that failed); reset "
        "to 0 by a successful fire.",
    )
    last_error: dict[str, Any] | None = Field(
        default=None,
        description="The last fire failure as a JSON AgentError; None after a successful fire.",
    )
    retry_after: AwareDatetime | None = Field(
        default=None,
        description="A retryable fire failure is not retried before this time (ISO 8601, "
        "timezone-aware); None when not backing off.",
    )
    created_at: AwareDatetime = Field(
        default_factory=now,
        description="When the schedule was created (ISO 8601, timezone-aware; UTC unless given).",
    )
    updated_at: AwareDatetime = Field(
        default_factory=now,
        description="When the record last changed (ISO 8601, timezone-aware; UTC unless given).",
    )

    @classmethod
    def from_spec(cls, spec: ScheduleSpec, **fields: Any) -> Self:
        """The record of a spec; ``fields`` (an id, the next fire time) win over the spec."""
        return cls.model_validate({**spec.model_dump(), **fields})
