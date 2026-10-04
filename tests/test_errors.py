"""The pause contract: how an agent asks a person something.

`test_contracts.py` covers the request/response types; this file covers the one exception
that is deliberately not an error, and the error model every real failure is normalised into.
"""

from __future__ import annotations

import asyncio
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from trellis.contracts import (
    AgentCancelledError,
    AgentError,
    AgentPaused,
    AgentTimeoutError,
    ConfigurationError,
    MemoryUnavailableError,
    ModelError,
    PolicyDeniedError,
    ResultValidationError,
    ToolError,
    ToolNotFoundError,
)
from trellis.contracts.errors import (
    ERROR_SOURCES,
    RETRYABLE_CATEGORIES,
    ErrorCategory,
    ErrorSource,
    HarnessError,
    classify,
    is_pause_signal,
)

# --------------------------------------------------------------- pausing for a human


def test_an_agent_can_pause_without_being_a_langgraph_graph() -> None:
    """The framework-agnostic pause.

    Pause detection used to match four LangGraph class names and nothing else, so an
    ordinary async agent could only stop a run by failing it — or by naming its own
    exception ``GraphInterrupt`` and hoping. Agents built on this platform rather than on
    LangGraph could not do human-in-the-loop at all.
    """
    paused = AgentPaused("Approve a EUR 240 refund for order 91?")
    assert is_pause_signal(paused)
    assert str(paused) == "Approve a EUR 240 refund for order 91?"


def test_a_pause_is_not_an_error() -> None:
    """Inheriting from HarnessError would classify it, count it in the error rate and hand
    it to on_agent_error — which is exactly what the pause handling exists to avoid."""
    assert not issubclass(AgentPaused, HarnessError)


def test_a_pause_carries_the_question_to_whoever_has_to_answer_it() -> None:
    """awaiting() is what the run store keeps and the webhook delivers, so a UI can render
    the question without the harness knowing anything about the UI."""
    paused = AgentPaused(
        "Approve a EUR 240 refund for order 91?",
        expects={"type": "boolean"},
        payload={"order_id": 91, "amount_eur": 240},
    )
    assert paused.awaiting() == {
        "question": "Approve a EUR 240 refund for order 91?",
        "expects": {"type": "boolean"},
        "payload": {"order_id": 91, "amount_eur": 240},
    }
    # Absent extras are left out rather than sent as nulls a UI has to special-case.
    assert AgentPaused("Continue?").awaiting() == {"question": "Continue?"}


def test_a_pause_with_no_question_is_refused() -> None:
    """A run that stops with nothing to show anyone is a hang, not a pause."""
    for empty in ("", "   "):
        with pytest.raises(ValueError, match="needs a question"):
            AgentPaused(empty)


def test_the_langgraph_signals_are_still_recognised() -> None:
    """Matched by class name across the hierarchy, so langgraph stays un-imported."""

    class GraphBubbleUp(Exception):
        pass

    class GraphInterrupt(GraphBubbleUp):
        pass

    class ParentCommand(GraphBubbleUp):
        pass

    for cls in (GraphBubbleUp, GraphInterrupt, ParentCommand):
        assert is_pause_signal(cls("suspended")), cls.__name__
    assert not is_pause_signal(RuntimeError("a real failure"))


def test_memory_sdk_errors_are_classified_by_module_and_name():
    """``trellis.memory`` exceptions are recognised without importing the SDK: a class of
    the right name from the ``trellis`` namespace maps, the same name elsewhere does not."""
    sdk_error = type("RateLimitedError", (Exception,), {"__module__": "trellis.memory.errors"})
    assert classify(sdk_error("slow down")) is ErrorCategory.RATE_LIMIT
    elsewhere = type("RateLimitedError", (Exception,), {"__module__": "somewhere.else"})
    assert classify(elsewhere("slow down")) is ErrorCategory.UNKNOWN


# --------------------------------------------------------------- normalising failures


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (asyncio.CancelledError(), ErrorCategory.CANCELLED),
        (TimeoutError("slow"), ErrorCategory.TIMEOUT),
        (TimeoutError("slow"), ErrorCategory.TIMEOUT),
        (ValueError("bad"), ErrorCategory.VALIDATION),
        (TypeError("bad"), ErrorCategory.VALIDATION),
        (KeyError("missing"), ErrorCategory.VALIDATION),
        (PermissionError("no"), ErrorCategory.AUTHORIZATION),
        (RuntimeError("?"), ErrorCategory.UNKNOWN),
        (OSError("disk"), ErrorCategory.UNKNOWN),
    ],
)
def test_stdlib_failures_are_classified(exc: BaseException, category: ErrorCategory) -> None:
    assert classify(exc) is category


@pytest.mark.parametrize(
    ("module", "name", "category"),
    [
        ("trellis.memory.errors", "AuthenticationError", ErrorCategory.AUTHORIZATION),
        ("trellis.memory.errors", "AuthorizationError", ErrorCategory.AUTHORIZATION),
        ("trellis.memory.errors", "ValidationError", ErrorCategory.VALIDATION),
        ("trellis.memory.errors", "ConflictError", ErrorCategory.VALIDATION),
        ("trellis.memory.errors", "NotFoundError", ErrorCategory.DEPENDENCY),
        ("trellis.memory.errors", "DependencyUnavailableError", ErrorCategory.DEPENDENCY),
        ("trellis.memory.errors", "InsufficientEvidence", ErrorCategory.MEMORY),
        ("trellis.memory.errors", "MemoryError", ErrorCategory.MEMORY),
        ("httpx", "ConnectError", ErrorCategory.DEPENDENCY),
        ("httpx._exceptions", "ConnectTimeout", ErrorCategory.TIMEOUT),
        ("httpx._exceptions", "ReadTimeout", ErrorCategory.TIMEOUT),
        ("httpx._exceptions", "PoolTimeout", ErrorCategory.TIMEOUT),
        ("httpx", "SomethingNew", ErrorCategory.UNKNOWN),
        ("requests", "ConnectError", ErrorCategory.UNKNOWN),
    ],
)
def test_sdk_and_transport_errors_are_classified_by_module_and_name(
    module: str, name: str, category: ErrorCategory
) -> None:
    exc_type = type(name, (Exception,), {"__module__": module})
    assert classify(exc_type("x")) is category


def test_only_transient_categories_are_retryable() -> None:
    assert (
        frozenset({ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT, ErrorCategory.DEPENDENCY})
        == RETRYABLE_CATEGORIES
    )


def test_an_exception_becomes_a_serialisable_error() -> None:
    error = AgentError.of(TimeoutError("took too long"), source="memory", trace_id="tr")
    assert error == AgentError(
        code="TimeoutError",
        category=ErrorCategory.TIMEOUT,
        message="took too long",
        retryable=True,
        source="memory",
        trace_id="tr",
    )
    assert AgentError.model_validate_json(error.model_dump_json()) == error
    unknown = AgentError.of(RuntimeError("?"))
    assert unknown.category is ErrorCategory.UNKNOWN and not unknown.retryable


def test_the_caller_can_override_the_category_and_retryability() -> None:
    error = AgentError.of(RuntimeError("429"), category=ErrorCategory.RATE_LIMIT)
    assert error.category is ErrorCategory.RATE_LIMIT and error.retryable
    pinned = AgentError.of(TimeoutError(), retryable=False)
    assert pinned.category is ErrorCategory.TIMEOUT and not pinned.retryable


def test_a_long_message_is_truncated() -> None:
    assert len(AgentError.of(ValueError("x" * 5000)).message) == 2000


def test_an_agent_error_is_frozen_with_safe_defaults() -> None:
    error = AgentError(code="X")
    assert error.category is ErrorCategory.UNKNOWN and not error.retryable
    assert error.message == "" and error.details == {} and error.source is None
    with pytest.raises(ValidationError):
        error.code = "Y"  # type: ignore[misc]


# --------------------------------------------------------------- the harness's own failures


@pytest.mark.parametrize(
    ("exc_type", "code", "category", "retryable"),
    [
        (HarnessError, "HARNESS_ERROR", ErrorCategory.UNKNOWN, False),
        (ConfigurationError, "CONFIGURATION_ERROR", ErrorCategory.VALIDATION, False),
        (AgentTimeoutError, "AGENT_TIMEOUT", ErrorCategory.TIMEOUT, True),
        (AgentCancelledError, "AGENT_CANCELLED", ErrorCategory.CANCELLED, False),
        (PolicyDeniedError, "POLICY_DENIED", ErrorCategory.POLICY, False),
        (MemoryUnavailableError, "MEMORY_UNAVAILABLE", ErrorCategory.MEMORY, True),
        (ModelError, "MODEL_ERROR", ErrorCategory.MODEL, False),
        (ToolError, "TOOL_ERROR", ErrorCategory.TOOL, False),
        (ToolNotFoundError, "TOOL_NOT_FOUND", ErrorCategory.VALIDATION, False),
        (ResultValidationError, "RESULT_VALIDATION_ERROR", ErrorCategory.VALIDATION, False),
    ],
)
def test_each_harness_error_carries_its_own_classification(
    exc_type: type[HarnessError], code: str, category: ErrorCategory, retryable: bool
) -> None:
    exc = exc_type()
    assert isinstance(exc, HarnessError)
    assert (exc.code, exc.category, exc.retryable) == (code, category, retryable)
    assert exc.message == str(exc) == code  # no message means the code is the message
    assert exc.details == {} and exc.source is None
    error = AgentError.of(exc)
    assert (error.code, error.category, error.retryable, error.message) == (
        code,
        category,
        retryable,
        code,
    )


def test_a_harness_error_keeps_its_message_details_and_source() -> None:
    exc = ToolError("refund failed", details={"order": 91}, source="tools")
    assert str(exc) == exc.message == "refund failed"
    error = AgentError.of(exc, trace_id="tr")
    assert error == AgentError(
        code="TOOL_ERROR",
        category=ErrorCategory.TOOL,
        message="refund failed",
        source="tools",
        details={"order": 91},
        trace_id="tr",
    )
    # a source passed when normalising wins over the exception's own
    assert AgentError.of(exc, source="langgraph").source == "langgraph"
    assert exc.to_error(source="a2a").source == "a2a"
    # ``category`` is ignored: the harness error has already classified itself
    assert AgentError.of(exc, category=ErrorCategory.MEMORY).category is ErrorCategory.TOOL


def test_retryability_can_be_set_per_instance_without_touching_the_class() -> None:
    assert ModelError("overloaded", retryable=True).to_error().retryable
    assert not ModelError("bad prompt").retryable
    assert not AgentTimeoutError(retryable=False).to_error().retryable
    assert AgentTimeoutError.retryable


def test_tool_not_found_is_a_tool_error_classified_as_validation() -> None:
    """Callers catching ``ToolError`` still catch it; retry policy sees a caller's mistake."""
    with pytest.raises(ToolError):
        raise ToolNotFoundError("no such tool: frobnicate")


def test_node_interrupt_is_a_pause_by_name_too() -> None:
    node_interrupt = type("NodeInterrupt", (Exception,), {})
    assert is_pause_signal(node_interrupt())
    assert not is_pause_signal(ToolError())


# --------------------------------------------------------------- what an SDK error says itself


def _sdk_error(module: str, name: str, base: type[Exception] = Exception) -> type[Exception]:
    """A class shaped like the SDKs' errors (``retryable`` set per instance, as the Memory
    Service SDK's ``MemoryError`` and bifrost-sdk's ``GatewayError`` do), without importing
    them: the contract classifies by module and class name."""

    def __init__(self: Any, message: str = "", *, retryable: Any = None) -> None:
        Exception.__init__(self, message)
        if retryable is not None:
            self.retryable = retryable

    return type(name, (base,), {"__module__": module, "__init__": __init__})


MEMORY = "trellis.memory.errors"
BIFROST = "bifrost_sdk._errors"
HTTPX = "httpx._exceptions"


@pytest.mark.parametrize(
    ("module", "name", "retryable", "category"),
    [
        # the service said so: a 409 it calls transient, a 503 it calls permanent
        (MEMORY, "ConflictError", True, ErrorCategory.VALIDATION),
        (MEMORY, "DependencyUnavailableError", False, ErrorCategory.DEPENDENCY),
        # the SDK's base class keeps its own answer either way
        (MEMORY, "MemoryError", True, ErrorCategory.MEMORY),
        (MEMORY, "MemoryError", False, ErrorCategory.MEMORY),
        # bifrost-sdk: a 4xx GatewayError is not retried, a 5xx ServerError is
        (BIFROST, "GatewayError", False, ErrorCategory.DEPENDENCY),
        (BIFROST, "ServerError", True, ErrorCategory.DEPENDENCY),
        (BIFROST, "BadRequestError", False, ErrorCategory.VALIDATION),
        (BIFROST, "ConflictError", True, ErrorCategory.VALIDATION),
        # an httpx error that carries the attribute is read the same way
        (HTTPX, "ConnectError", False, ErrorCategory.DEPENDENCY),
    ],
)
def test_an_exception_s_own_retryable_wins_over_its_category(
    module: str, name: str, retryable: bool, category: ErrorCategory
) -> None:
    error = AgentError.of(_sdk_error(module, name)("boom", retryable=retryable))
    assert (error.category, error.retryable) == (category, retryable)


def test_without_its_own_answer_an_exception_is_retried_by_category() -> None:
    assert AgentError.of(_sdk_error(MEMORY, "DependencyUnavailableError")()).retryable
    assert not AgentError.of(_sdk_error(MEMORY, "ConflictError")()).retryable


@pytest.mark.parametrize("value", ["yes", 1, None, object()])
def test_a_retryable_attribute_that_is_not_a_bool_is_ignored(value: Any) -> None:
    exc = _sdk_error(MEMORY, "ConflictError")("boom")
    exc.retryable = value  # type: ignore[attr-defined]
    assert not AgentError.of(exc).retryable  # VALIDATION, so not retried


def test_the_caller_s_retryable_wins_over_the_exception_s() -> None:
    exc = _sdk_error(MEMORY, "DependencyUnavailableError")("down", retryable=True)
    assert not AgentError.of(exc, retryable=False).retryable


# --------------------------------------------------------------- timeouts, whoever raised them


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("builtin, which asyncio.TimeoutError is"),
        _sdk_error(MEMORY, "TimeoutError", base=_sdk_error(MEMORY, "MemoryError"))("sdk"),
        _sdk_error(BIFROST, "TimeoutError")("bifrost"),
        _sdk_error(HTTPX, "TimeoutException")("httpx base"),
        _sdk_error(HTTPX, "ReadTimeout", base=_sdk_error(HTTPX, "TimeoutException"))("read"),
    ],
    ids=["builtin", "memory-sdk", "bifrost-sdk", "httpx-base", "httpx-read"],
)
def test_every_timeout_is_a_retryable_timeout(exc: BaseException) -> None:
    error = AgentError.of(exc)
    assert (error.category, error.retryable) == (ErrorCategory.TIMEOUT, True)


def test_the_memory_sdk_timeout_is_not_its_base_class_s_memory_error() -> None:
    """The nearest class decides: the SDK's ``TimeoutError`` subclasses its ``MemoryError``."""
    base = _sdk_error(MEMORY, "MemoryError")
    assert classify(_sdk_error(MEMORY, "TimeoutError", base=base)()) is ErrorCategory.TIMEOUT
    assert classify(base()) is ErrorCategory.MEMORY


def test_a_subclass_an_sdk_adds_later_is_classified_like_its_parent() -> None:
    write_timeout = _sdk_error(HTTPX, "WriteTimeout2", base=_sdk_error(HTTPX, "TimeoutException"))
    assert classify(write_timeout()) is ErrorCategory.TIMEOUT
    quota = _sdk_error(MEMORY, "QuotaError", base=_sdk_error(MEMORY, "RateLimitedError"))
    assert classify(quota()) is ErrorCategory.RATE_LIMIT
    # an unknown name in an unknown package stays unknown, whatever its parents are called
    elsewhere = _sdk_error("vendor.errors", "TimeoutException")
    assert classify(_sdk_error("vendor.errors", "Slow", base=elsewhere)()) is (
        ErrorCategory.UNKNOWN
    )


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Unreachable", ErrorCategory.DEPENDENCY),
        ("CircuitOpen", ErrorCategory.DEPENDENCY),
        ("RateLimited", ErrorCategory.RATE_LIMIT),
        ("RateLimitedError", ErrorCategory.RATE_LIMIT),
        ("AuthenticationError", ErrorCategory.AUTHORIZATION),
        ("PermissionDeniedError", ErrorCategory.AUTHORIZATION),
        ("NotFoundError", ErrorCategory.DEPENDENCY),
        ("UnprocessableError", ErrorCategory.VALIDATION),
        ("EmptyResponse", ErrorCategory.MODEL),
        ("InvalidJSON", ErrorCategory.MODEL),
        ("BifrostError", ErrorCategory.UNKNOWN),
    ],
)
def test_bifrost_sdk_errors_are_classified_by_name(name: str, category: ErrorCategory) -> None:
    assert classify(_sdk_error(BIFROST, name)()) is category


# --------------------------------------------------------------- who reported it


@pytest.mark.parametrize("source", get_args(ErrorSource))
def test_every_source_in_use_is_accepted(source: str) -> None:
    assert AgentError(code="X", source=source).source == source  # type: ignore[arg-type]
    assert AgentError.of(RuntimeError(), source=source).source == source


def test_the_sources_are_the_components_that_report_failures() -> None:
    """agent-runs, the harness's tool bridge, MCP, A2A, memory, and each framework adapter
    (whose name the harness records as the source of a failed run)."""
    adapters = {"function", "langgraph", "openai_agents", "claude_agent_sdk", "react"}
    assert frozenset({"agent-runs", "tools", "mcp", "a2a", "memory"} | adapters) == ERROR_SOURCES


@pytest.mark.parametrize("source", ["billing", "MCP", "", "agent_runs", "billing.refund"])
def test_a_source_outside_the_vocabulary_is_refused(source: str) -> None:
    with pytest.raises(ValidationError, match="agent-runs"):
        AgentError(code="X", source=source)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AgentError.of(RuntimeError(), source=source)
    with pytest.raises(ValidationError):
        ToolError("boom", source=source).to_error()


def test_a_qualified_source_keeps_its_qualifier_in_details() -> None:
    """The harness raises ``ToolError(source=f"mcp.{tool}")`` for a failed MCP tool."""
    error = ToolError("no such order", details={"order": 91}, source="mcp.refund").to_error()
    assert error.source == "mcp"
    assert error.details == {"order": 91, "source_detail": "refund"}
    assert AgentError.model_validate_json(error.model_dump_json()) == error
    # only the first dot splits, and a qualifier the caller already set is kept
    dotted = AgentError(code="X", source="mcp.erp.get", details={"source_detail": "erp"})  # type: ignore[arg-type]
    assert (dotted.source, dotted.details) == ("mcp", {"source_detail": "erp"})
    assert AgentError(code="X", source="mcp.erp.get").details == {"source_detail": "erp.get"}  # type: ignore[arg-type]


def test_a_qualified_source_with_unusable_details_is_left_for_validation_to_refuse() -> None:
    with pytest.raises(ValidationError, match="details"):
        AgentError(code="X", source="mcp.refund", details="not a dict")  # type: ignore[arg-type]


def test_the_schema_lists_the_sources() -> None:
    schema = AgentError.model_json_schema()["properties"]["source"]
    listed = {value for option in schema["anyOf"] for value in option.get("enum", [])}
    assert listed == ERROR_SOURCES
