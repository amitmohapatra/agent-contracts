"""Every outbound dependency of the harness, as a Protocol (§27, §61).

The core imports nothing but these protocols and the contracts. Concrete adapters (Memory
Service SDK, OpenTelemetry, Langfuse, local tools, LangGraph...) implement them and are
injected. That is what keeps the core framework- and vendor-neutral.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from trellis.contracts.a2a import AgentCard
from trellis.contracts.artifacts import ArtifactRef, MemoryObservation
from trellis.contracts.context import AgentExecutionContext
from trellis.contracts.descriptors import AgentDescriptor
from trellis.contracts.errors import AgentError
from trellis.contracts.evaluation import JudgeVerdict
from trellis.contracts.events import AgentEvalEvent
from trellis.contracts.feedback import Feedback, FeedbackTargetKind
from trellis.contracts.messages import AgentRequest, AgentResponse
from trellis.contracts.model import ModelRequest, ModelResponse
from trellis.contracts.runs import (
    Interrupt,
    InterruptResolution,
    RunEvent,
    RunRecord,
    RunStart,
    RunStatus,
    Schedule,
    ScheduleSpec,
)
from trellis.contracts.tool import ToolCall, ToolOutcome, ToolSpec

# --------------------------------------------------------------------------- model / tool


@runtime_checkable
class ModelClient(Protocol):
    """A model provider. Implementations are wrapped by the harness for instrumentation."""

    async def invoke(self, request: ModelRequest | str, /, **kwargs: Any) -> ModelResponse: ...

    async def structured(
        self, request: ModelRequest | str, /, schema: Any, **kwargs: Any
    ) -> ModelResponse: ...

    def stream(self, request: ModelRequest | str, /, **kwargs: Any) -> AsyncIterator[Any]: ...


@runtime_checkable
class ToolClient(Protocol):
    """A tool runtime: local callables, an MCP server, or a gateway."""

    async def list_tools(self) -> Sequence[ToolSpec]: ...

    async def call(self, tool: str | ToolCall, /, **args: Any) -> ToolOutcome: ...


@runtime_checkable
class ArtifactClient(Protocol):
    """Where large payloads go so they never sit inside a result or a graph state (§43)."""

    async def put(
        self,
        content: bytes | str,
        *,
        type: str = "blob",
        mime_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ArtifactRef: ...

    async def get(self, artifact_id: str) -> bytes | None: ...


# --------------------------------------------------------------------------- memory


@runtime_checkable
class MemoryPort(Protocol):
    """The harness's view of the Memory Service. Implemented by the SDK adapter and by a
    no-op. Retrieval returns whatever bundle type the backing service produces; the harness
    treats it as opaque except for the small facts it reads through :meth:`describe`."""

    enabled: bool

    async def retrieve(self, query: str, /, **options: Any) -> Any | None: ...

    async def observe(self, observation: MemoryObservation, /) -> Any | None: ...

    async def record_input(self, text: str, /, **metadata: Any) -> Any | None: ...

    async def record_output(self, text: str, /, **metadata: Any) -> Any | None: ...

    def describe(self, bundle: Any, /) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- telemetry


@runtime_checkable
class TelemetryProvider(Protocol):
    """Span/event/metric emission (§23). ``start_span`` is a context manager yielding a
    :class:`HarnessSpan`-shaped object."""

    def start_span(
        self, name: str, *, kind: str = "internal", attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]: ...

    def record_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None: ...

    def record_metric(
        self,
        name: str,
        value: float,
        *,
        unit: str = "",
        attributes: Mapping[str, Any] | None = None,
    ) -> None: ...

    def flush(self, timeout_seconds: float = 5.0) -> None: ...


@runtime_checkable
class TelemetryRedactor(Protocol):
    """What may leave the process (§27). Applied before any attribute reaches a backend."""

    def redact_attributes(self, attributes: Mapping[str, Any]) -> dict[str, Any]: ...

    def redact_input(self, value: Any) -> Any: ...

    def redact_output(self, value: Any) -> Any: ...


@runtime_checkable
class EvaluationProvider(Protocol):
    async def score(
        self,
        name: str,
        value: float | str,
        /,
        *,
        context: AgentExecutionContext | None = None,
        comment: str | None = None,
        **metadata: Any,
    ) -> None: ...

    async def submit_dataset_item(self, dataset: str, item: Mapping[str, Any]) -> None: ...

    async def submit_feedback(self, feedback: Mapping[str, Any]) -> None: ...


@runtime_checkable
class EvaluationSink(Protocol):
    """Where :class:`AgentEvalEvent` values go. Async, off the critical path (§29)."""

    async def emit(self, event: AgentEvalEvent) -> None: ...


@runtime_checkable
class PromptProvider(Protocol):
    """Prompt management (§31). Optional; agents work without one."""

    # ``name`` is positional-only: a prompt's own variables may legitimately be called
    # "name", and they arrive in ``**vars``.
    async def get_prompt(self, name: str, /, *, version: str | None = None, **vars: Any) -> Any: ...


# --------------------------------------------------------------------------- policy / registry


@runtime_checkable
class AgentPolicyProvider(Protocol):
    """Authorization decisions (§48). A denial raises ``PolicyDeniedError`` in the harness."""

    async def authorize_execution(self, request: AgentRequest) -> bool | str: ...

    async def authorize_tool(
        self, context: AgentExecutionContext, call: ToolCall
    ) -> bool | str: ...

    async def authorize_model(
        self, context: AgentExecutionContext, request: ModelRequest
    ) -> bool | str: ...


@runtime_checkable
class AgentRegistryClient(Protocol):
    """Future agent registry (§47). Default implementation is a no-op."""

    async def register(self, descriptor: AgentDescriptor) -> None: ...

    async def heartbeat(self, descriptor: AgentDescriptor, *, status: str = "healthy") -> None: ...


# --------------------------------------------------------------------------- runs / surfaces


@runtime_checkable
class EventSink(Protocol):
    """Where a run's :class:`RunEvent` stream goes: an SSE response, a webhook outbox, a
    trace exporter, a test collector. Must not raise into the run; a sink that fails is
    degraded, never the run. Events are unredacted: a sink that leaves the process passes
    them through a :class:`TelemetryRedactor` first."""

    async def publish(self, event: RunEvent) -> None: ...


@runtime_checkable
class RunStore(Protocol):
    """Where a run outlives the process: agent-runs, Temporal, or an in-memory store in
    tests. A run is started idempotently by its id, paused with the interrupt it waits on,
    resumed with the resolution it got, and finished once."""

    async def started(self, start: RunStart) -> RunRecord: ...

    async def paused(self, interrupt: Interrupt) -> RunRecord: ...

    async def resumed(self, resolution: InterruptResolution) -> RunRecord: ...

    async def finished(
        self,
        run_id: str,
        status: RunStatus,
        *,
        output: Any = None,
        error: AgentError | None = None,
    ) -> RunRecord: ...

    async def get(self, run_id: str) -> RunRecord | None: ...

    async def list_paused(self, tenant_id: str, *, limit: int = 100) -> Sequence[RunRecord]: ...


@runtime_checkable
class Scheduler(Protocol):
    """Standing intents: agent-schedules, Temporal Schedules, or an in-memory scheduler.
    Firing creates a run through the same request contract as any other entry point."""

    async def create(self, spec: ScheduleSpec) -> Schedule: ...

    async def get(self, schedule_id: str) -> Schedule | None: ...

    async def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> Sequence[Schedule]: ...

    async def set_enabled(self, schedule_id: str, enabled: bool) -> Schedule: ...

    async def delete(self, schedule_id: str) -> None: ...


@runtime_checkable
class FeedbackStore(Protocol):
    """Where :class:`Feedback` is kept: the Memory Service (``POST /v1/feedback``) or an
    in-memory store in tests. The system of record; ``EvaluationProvider.submit_feedback``
    is the tracing backend's copy. A store verifies a record's identity fields against the
    authenticated caller: the record only claims who it is from."""

    async def submit(self, feedback: Feedback) -> Feedback: ...

    async def list_for(
        self, target_kind: FeedbackTargetKind, target_id: str, *, limit: int = 100
    ) -> Sequence[Feedback]: ...


@runtime_checkable
class Judge(Protocol):
    """Scores a finished run, off the critical path. ``None`` means the judge abstained
    (unsampled, over budget, nothing to check)."""

    async def judge(
        self, event: AgentEvalEvent, /, *, response: AgentResponse | None = None
    ) -> JudgeVerdict | None: ...


@runtime_checkable
class AgentDirectory(Protocol):
    """Agents another agent may call, as Agent Cards: the AI Registry, or a static list."""

    async def get(self, agent_id: str) -> AgentCard | None: ...

    async def find(
        self, query: str | None = None, *, skill: str | None = None, limit: int = 20
    ) -> Sequence[AgentCard]: ...

    async def publish(self, card: AgentCard) -> None: ...


# --------------------------------------------------------------------------- pipeline


#: The per-run object an interceptor is handed. Deliberately untyped here.
#:
#: The concrete runtime belongs to whatever executes the agent — this package must stay
#: installable without it, or it stops being a standard a team can adopt on its own. Naming
#: the harness's class here was the one line that made the extracted package depend on the
#: thing it was extracted from.
Runtime = Any


@runtime_checkable
class AgentInterceptor(Protocol):
    """One stage of the execution pipeline (§20). Deterministically ordered by ``order``."""

    name: str
    order: int

    async def before(self, request: AgentRequest, runtime: Runtime) -> AgentRequest: ...

    async def after(self, result: AgentResponse, runtime: Runtime) -> AgentResponse: ...

    async def on_error(self, error: AgentError, runtime: Runtime) -> AgentResponse | None: ...


@runtime_checkable
class LifecycleListener(Protocol):
    """Observes lifecycle events. Must not raise; the harness swallows and logs if it does."""

    def on_event(self, event: str, payload: Mapping[str, Any]) -> Awaitable[None] | None: ...


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Framework-specific wrapping (§50). The only place a framework may be imported."""

    name: str

    def supports(self, target: object) -> bool: ...

    def wrap(self, target: object, config: Any) -> object: ...

    def extract_context(self, *args: Any, **kwargs: Any) -> AgentExecutionContext | None: ...

    def map_result(self, result: AgentResponse, *args: Any, **kwargs: Any) -> object: ...
