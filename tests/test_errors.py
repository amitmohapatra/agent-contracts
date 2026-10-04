"""The pause contract: how an agent asks a person something.

`test_contracts.py` covers the request/response types; this file covers the one exception
that is deliberately not an error, and the error model every real failure is normalised into.
"""

from __future__ import annotations

import asyncio

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
    RETRYABLE_CATEGORIES,
    ErrorCategory,
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
    exc = ToolError("refund failed", details={"order": 91}, source="billing")
    assert str(exc) == exc.message == "refund failed"
    error = AgentError.of(exc, trace_id="tr")
    assert error == AgentError(
        code="TOOL_ERROR",
        category=ErrorCategory.TOOL,
        message="refund failed",
        source="billing",
        details={"order": 91},
        trace_id="tr",
    )
    # a source passed when normalising wins over the exception's own
    assert AgentError.of(exc, source="gateway").source == "gateway"
    assert exc.to_error(source="direct").source == "direct"
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
