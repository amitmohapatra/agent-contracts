"""The standardized error model (§37) and the exceptions the harness raises.

Every failure that crosses the harness boundary is normalized into an :class:`AgentError`
so callers can branch on ``category``/``retryable`` instead of on a framework's exception
zoo. Cancellation is *never* normalized away: :class:`asyncio.CancelledError` propagates.
"""

from __future__ import annotations

import asyncio
import builtins
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCategory(StrEnum):
    VALIDATION = "VALIDATION"
    AUTHORIZATION = "AUTHORIZATION"
    MODEL = "MODEL"
    TOOL = "TOOL"
    MEMORY = "MEMORY"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    RATE_LIMIT = "RATE_LIMIT"
    DEPENDENCY = "DEPENDENCY"
    POLICY = "POLICY"
    UNKNOWN = "UNKNOWN"


#: Categories a retry policy may consider (§40). Everything else is never auto-retried.
RETRYABLE_CATEGORIES = frozenset(
    {ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT, ErrorCategory.DEPENDENCY}
)


class AgentError(BaseModel):
    """A normalized, serializable failure."""

    model_config = ConfigDict(frozen=True)

    code: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    message: str = ""
    retryable: bool = False
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None

    @classmethod
    def of(
        cls,
        exc: BaseException,
        *,
        category: ErrorCategory | None = None,
        source: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
    ) -> AgentError:
        """Normalize an exception. A :class:`HarnessError` carries its own classification."""
        if isinstance(exc, HarnessError):
            return exc.to_error(trace_id=trace_id, source=source or exc.source)
        cat = category or classify(exc)
        return cls(
            code=type(exc).__name__,
            category=cat,
            message=str(exc)[:2000],
            retryable=cat in RETRYABLE_CATEGORIES if retryable is None else retryable,
            source=source,
            trace_id=trace_id,
        )


class AgentPaused(Exception):
    """Raised by an agent to suspend the run and ask a person something.

    The framework-agnostic way to pause. LangGraph agents already have one — ``interrupt()``
    raises ``GraphInterrupt`` and the graph runtime persists a checkpoint — but an ordinary
    async function had no way to say "a human has to answer this before I continue". Without
    it, the only options were to fail the run or to name your own exception ``GraphInterrupt``
    and hope, and agents built on this platform rather than on LangGraph could not do
    human-in-the-loop at all.

    Deliberately not a :class:`HarnessError`: a pause is not a failure, and inheriting from
    the error base would classify it, count it in the error rate, and hand it to
    ``on_agent_error``.

    ``question`` is what the person is being asked, and it is required — a pause with no
    question is a run that stops with nothing to show anyone. ``expects`` describes the shape
    of an acceptable answer so a UI can render a control rather than a free-text box;
    ``payload`` carries whatever else that UI needs.

        raise AgentPaused(
            "Approve a EUR 240 refund for order 91?",
            expects={"type": "boolean"},
            payload={"order_id": 91, "amount_eur": 240},
        )

    :meth:`awaiting` is the dict the run store keeps and the webhook delivers, so the
    question reaches a person without the harness knowing anything about the UI.
    """

    def __init__(
        self,
        question: str,
        *,
        expects: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not question or not question.strip():
            raise ValueError("AgentPaused needs a question: a pause nobody can answer is a hang")
        super().__init__(question)
        self.question = question
        self.expects = expects
        self.payload = payload

    def awaiting(self) -> dict[str, Any]:
        """What the run is waiting for, as the run store and the UI see it."""
        out: dict[str, Any] = {"question": self.question}
        if self.expects is not None:
            out["expects"] = self.expects
        if self.payload is not None:
            out["payload"] = self.payload
        return out


#: Exceptions a framework raises to *suspend* a run rather than to report a failure.
#: LangGraph's ``interrupt()`` raises ``GraphInterrupt``; ``Command(goto=...)`` from a
#: subgraph raises ``ParentCommand``. Both derive from ``GraphBubbleUp``, and both are
#: control flow the graph runtime catches and acts on — not errors. :class:`AgentPaused` is
#: this platform's own, for agents that are not graphs.
_PAUSE_SIGNALS = frozenset(
    {"AgentPaused", "GraphBubbleUp", "GraphInterrupt", "NodeInterrupt", "ParentCommand"}
)


def is_pause_signal(exc: BaseException) -> bool:
    """Whether ``exc`` means "this run is suspended", not "this run failed".

    Matched by class name across the exception's own hierarchy rather than by importing
    langgraph, which the core must not depend on. A user-defined ``GraphInterrupt`` of their
    own would match too — which is the behaviour they would want anyway.
    """
    return any(cls.__name__ in _PAUSE_SIGNALS for cls in type(exc).__mro__)


def classify(exc: BaseException) -> ErrorCategory:
    """Best-effort category for an arbitrary exception.

    Known Memory Service SDK errors and stdlib timeouts are mapped explicitly; everything
    else is ``UNKNOWN`` (and therefore *not* retryable) rather than optimistically retried.
    """
    if isinstance(exc, asyncio.CancelledError):
        return ErrorCategory.CANCELLED
    if isinstance(exc, asyncio.TimeoutError | builtins.TimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(exc, ValueError | TypeError | KeyError):
        return ErrorCategory.VALIDATION
    if isinstance(exc, PermissionError):
        return ErrorCategory.AUTHORIZATION
    return _sdk_category(exc)


def _sdk_category(exc: BaseException) -> ErrorCategory:
    """Map ``universal_memory`` SDK errors without importing the SDK eagerly."""
    name = type(exc).__name__
    mapping = {
        "AuthenticationError": ErrorCategory.AUTHORIZATION,
        "AuthorizationError": ErrorCategory.AUTHORIZATION,
        "ValidationError": ErrorCategory.VALIDATION,
        "ConflictError": ErrorCategory.VALIDATION,
        "NotFoundError": ErrorCategory.DEPENDENCY,
        "RateLimitedError": ErrorCategory.RATE_LIMIT,
        "DependencyUnavailableError": ErrorCategory.DEPENDENCY,
        "InsufficientEvidence": ErrorCategory.MEMORY,
        "MemoryError": ErrorCategory.MEMORY,
        "ConnectError": ErrorCategory.DEPENDENCY,
        "ConnectTimeout": ErrorCategory.TIMEOUT,
        "ReadTimeout": ErrorCategory.TIMEOUT,
        "PoolTimeout": ErrorCategory.TIMEOUT,
    }
    module = type(exc).__module__.split(".")[0]
    if module in ("universal_memory", "httpx") and name in mapping:
        return mapping[name]
    return ErrorCategory.UNKNOWN


# --------------------------------------------------------------------------- exceptions


class HarnessError(Exception):
    """Base class for failures the harness itself raises."""

    code = "HARNESS_ERROR"
    category = ErrorCategory.UNKNOWN
    retryable = False
    source: str | None = None

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        source: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details or {}
        if source is not None:
            self.source = source
        if retryable is not None:
            self.retryable = retryable

    def to_error(self, *, trace_id: str | None = None, source: str | None = None) -> AgentError:
        return AgentError(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            source=source or self.source,
            details=self.details,
            trace_id=trace_id,
        )


class ConfigurationError(HarnessError):
    code = "CONFIGURATION_ERROR"
    category = ErrorCategory.VALIDATION


class AgentTimeoutError(HarnessError):
    code = "AGENT_TIMEOUT"
    category = ErrorCategory.TIMEOUT
    retryable = True


class AgentCancelledError(HarnessError):
    code = "AGENT_CANCELLED"
    category = ErrorCategory.CANCELLED


class PolicyDeniedError(HarnessError):
    code = "POLICY_DENIED"
    category = ErrorCategory.POLICY


class MemoryUnavailableError(HarnessError):
    code = "MEMORY_UNAVAILABLE"
    category = ErrorCategory.MEMORY
    retryable = True


class ModelError(HarnessError):
    code = "MODEL_ERROR"
    category = ErrorCategory.MODEL


class ToolError(HarnessError):
    code = "TOOL_ERROR"
    category = ErrorCategory.TOOL


class ToolNotFoundError(ToolError):
    code = "TOOL_NOT_FOUND"
    category = ErrorCategory.VALIDATION


class ResultValidationError(HarnessError):
    code = "RESULT_VALIDATION_ERROR"
    category = ErrorCategory.VALIDATION
