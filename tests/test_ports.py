"""The ports the harness depends on, each satisfied by a minimal double with its exact
signatures. ``test_contracts_v2.py`` does the same for the run, event, judge and directory
ports; this covers the rest, so every port in ``trellis.contracts.ports`` is asserted."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest

from trellis.contracts import (
    AgentError,
    AgentExecutionContext,
    AgentInterceptor,
    AgentPolicyProvider,
    AgentRequest,
    AgentResponse,
    ArtifactClient,
    ArtifactRef,
    EvaluationProvider,
    MemoryObservation,
    MemoryPort,
    ModelClient,
    ModelRequest,
    ModelResponse,
    TelemetryProvider,
    TelemetryRedactor,
    ToolCall,
    ToolClient,
    ToolOutcome,
    ToolSpec,
    ports,
)
from trellis.contracts.ports import Runtime


class _Model:
    async def invoke(self, request: ModelRequest | str, /, **kwargs: Any) -> ModelResponse:
        return ModelResponse.coerce(request if isinstance(request, str) else request.prompt)

    async def structured(
        self, request: ModelRequest | str, /, schema: Any, **kwargs: Any
    ) -> ModelResponse:
        return ModelResponse(data={"schema": schema})

    def stream(self, request: ModelRequest | str, /, **kwargs: Any) -> AsyncIterator[Any]:
        async def chunks() -> AsyncIterator[Any]:
            yield "a"
            yield "b"

        return chunks()


class _Tools:
    async def list_tools(self) -> Sequence[ToolSpec]:
        return [ToolSpec(name="echo")]

    async def call(self, tool: str | ToolCall, /, **args: Any) -> ToolOutcome:
        name = tool if isinstance(tool, str) else tool.tool
        return ToolOutcome(tool=name, output=args)


class _Artifacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(
        self,
        content: bytes | str,
        *,
        type: str = "blob",
        mime_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ArtifactRef:
        artifact_id = idempotency_key or f"art_{len(self.blobs)}"
        self.blobs[artifact_id] = content.encode() if isinstance(content, str) else content
        return ArtifactRef(artifact_id=artifact_id, type=type, mime_type=mime_type)

    async def get(self, artifact_id: str) -> bytes | None:
        return self.blobs.get(artifact_id)


class _Memory:
    enabled: bool = True

    async def retrieve(self, query: str, /, **options: Any) -> Any | None:
        return {"query": query}

    async def observe(self, observation: MemoryObservation, /) -> Any | None:
        return None

    async def record_input(self, text: str, /, **metadata: Any) -> Any | None:
        return None

    async def record_output(self, text: str, /, **metadata: Any) -> Any | None:
        return None

    def describe(self, bundle: Any, /) -> dict[str, Any]:
        return {"items": 0}


class _Telemetry:
    def start_span(
        self, name: str, *, kind: str = "internal", attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]:
        return nullcontext(name)

    def record_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        return None

    def record_metric(
        self,
        name: str,
        value: float,
        *,
        unit: str = "",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        return None

    def flush(self, timeout_seconds: float = 5.0) -> None:
        return None


class _Redactor:
    def redact_attributes(self, attributes: Mapping[str, Any]) -> dict[str, Any]:
        return {k: "***" if k == "secret" else v for k, v in attributes.items()}

    def redact_input(self, value: Any) -> Any:
        return value

    def redact_output(self, value: Any) -> Any:
        return value


class _Evaluation:
    async def score(
        self,
        name: str,
        value: float | str,
        /,
        *,
        context: AgentExecutionContext | None = None,
        comment: str | None = None,
        **metadata: Any,
    ) -> None:
        return None

    async def submit_dataset_item(self, dataset: str, item: Mapping[str, Any]) -> None:
        return None


class _Policy:
    async def authorize_execution(self, request: AgentRequest) -> bool | str:
        return True

    async def authorize_tool(self, context: AgentExecutionContext, call: ToolCall) -> bool | str:
        return "tool not allowed" if call.tool == "rm" else True

    async def authorize_model(
        self, context: AgentExecutionContext, request: ModelRequest
    ) -> bool | str:
        return True


class _Interceptor:
    name = "noop"
    order = 10

    async def before(self, request: AgentRequest, runtime: Runtime) -> AgentRequest:
        return request

    async def after(self, result: AgentResponse, runtime: Runtime) -> AgentResponse:
        return result

    async def on_error(self, error: AgentError, runtime: Runtime) -> AgentResponse | None:
        return None


_IMPLEMENTATIONS: list[tuple[type, type]] = [
    (_Model, ModelClient),
    (_Tools, ToolClient),
    (_Artifacts, ArtifactClient),
    (_Memory, MemoryPort),
    (_Telemetry, TelemetryProvider),
    (_Redactor, TelemetryRedactor),
    (_Evaluation, EvaluationProvider),
    (_Policy, AgentPolicyProvider),
    (_Interceptor, AgentInterceptor),
]


@pytest.mark.parametrize(("implementation", "port"), _IMPLEMENTATIONS)
def test_each_port_is_satisfied_by_shape_alone(implementation: type, port: type) -> None:
    """Conforming needs no base class from here, and the double's signatures are the port's,
    not merely its method names (all ``runtime_checkable`` itself looks at)."""
    assert isinstance(implementation(), port)
    assert not isinstance(object(), port)
    assert port not in implementation.__mro__
    for name, member in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert inspect.signature(getattr(implementation, name)) == inspect.signature(member), (
            f"{port.__name__}.{name}"
        )


def test_a_double_missing_one_method_does_not_conform() -> None:
    class _NoFlush:
        def start_span(self, name: str, **_: Any) -> AbstractContextManager[Any]:
            return nullcontext()

        def record_event(self, name: str, attributes: Any = None) -> None: ...

        def record_metric(self, name: str, value: float, **_: Any) -> None: ...

    assert not isinstance(_NoFlush(), TelemetryProvider)


def test_the_interceptor_and_memory_ports_declare_their_attributes() -> None:
    """Data members are part of the protocol: a pipeline orders by ``order`` and a memory
    port can be switched off by ``enabled``."""
    assert {"name", "order"} <= AgentInterceptor.__protocol_attrs__  # type: ignore[attr-defined]
    assert "enabled" in MemoryPort.__protocol_attrs__  # type: ignore[attr-defined]


async def test_the_doubles_behave_as_the_ports_describe() -> None:
    ctx = AgentExecutionContext.create(tenant_id="acme")
    model = _Model()
    assert (await model.invoke("hi")).text == "hi"
    assert [chunk async for chunk in model.stream("hi")] == ["a", "b"]
    tools = _Tools()
    assert [t.name for t in await tools.list_tools()] == ["echo"]
    assert (await tools.call(ToolCall(tool="echo"), x=1)).output == {"x": 1}
    artifacts = _Artifacts()
    ref = await artifacts.put("payload", idempotency_key="art_k", mime_type="text/plain")
    assert ref.artifact_id == "art_k" and await artifacts.get("art_k") == b"payload"
    assert await artifacts.get("missing") is None
    with _Telemetry().start_span("run") as span:
        assert span == "run"
    assert _Redactor().redact_attributes({"secret": "s", "ok": 1}) == {"secret": "***", "ok": 1}
    assert await _Policy().authorize_tool(ctx, ToolCall(tool="rm")) == "tool not allowed"
    assert await _Policy().authorize_execution(AgentRequest.create(ctx)) is True


def test_runtime_is_deliberately_untyped() -> None:
    """The concrete runtime belongs to whatever executes the agent; naming it here would make
    the contract depend on the harness."""
    assert Runtime is ports.Runtime is Any
