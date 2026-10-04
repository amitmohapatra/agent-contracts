"""The standardized error model (§37) and the exceptions the harness raises.

Every failure that crosses the harness boundary is normalized into an :class:`AgentError`
so callers can branch on ``category``/``retryable`` instead of on a framework's exception
zoo. Cancellation is *never* normalized away: :class:`asyncio.CancelledError` propagates.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


#: Who reported a failure: the component that failed, never the exception class (that is
#: ``code``). The harness's framework adapters (``function``, ``langgraph``,
#: ``openai_agents``, ``claude_agent_sdk``, ``react``: the source of every run failure it
#: records), its tool layer (``tools``), an MCP tool behind the gateway (``mcp``), a remote
#: agent called over A2A (``a2a``), the Memory Service (``memory``) and agent-runs itself
#: (``agent-runs``: a lapsed lease, an unanswered interrupt, a schedule that could not fire).
ErrorSource = Literal[
    "agent-runs",
    "tools",
    "mcp",
    "a2a",
    "memory",
    "function",
    "langgraph",
    "openai_agents",
    "claude_agent_sdk",
    "react",
]
#: :data:`ErrorSource` as a set, for checking a value before it reaches a record.
ERROR_SOURCES: Final[frozenset[str]] = frozenset(get_args(ErrorSource))
#: Where a qualified source's qualifier goes (``mcp.refund`` is ``mcp`` plus ``refund``).
SOURCE_DETAIL: Final = "source_detail"
#: The longest exception text :meth:`AgentError.of` keeps, in characters.
MESSAGE_MAX_CHARS: Final = 2000


class AgentError(BaseModel):
    """A normalized, serializable failure."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(
        description="Machine-readable failure code: a HarnessError's own code, else the "
        "exception's class name.",
        examples=["TOOL_ERROR", "TimeoutError", "lease_expired"],
    )
    category: ErrorCategory = Field(
        default=ErrorCategory.UNKNOWN,
        description="What kind of failure it is; callers branch on this, not on exception "
        "classes. TIMEOUT, RATE_LIMIT and DEPENDENCY are retryable by default.",
    )
    message: str = Field(
        default="",
        description="Human-readable explanation (the exception's text, at most "
        f"{MESSAGE_MAX_CHARS} characters when built by AgentError.of); empty when none.",
    )
    retryable: bool = Field(
        default=False,
        description="Whether the same operation may succeed if tried again: the exception's "
        "own retryable attribute when it has one, else true only for a retryable category.",
    )
    source: ErrorSource | None = Field(
        default=None,
        description="Component that reported the failure. A qualified value such as "
        f"'mcp.<tool>' is stored as its root, the rest in details.{SOURCE_DETAIL}. "
        "None when unknown.",
        examples=["langgraph", "agent-runs", "mcp"],
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context for the failure as a JSON object (ids, offending "
        "values); empty when none.",
    )
    trace_id: str | None = Field(
        default=None,
        description="Trace id of the failing execution (its context's trace_id), to find "
        "its spans and logs; None when unknown.",
    )

    @model_validator(mode="before")
    @classmethod
    def _a_qualified_source_keeps_its_qualifier(cls, data: Any) -> Any:
        """``mcp.refund`` (the harness's ``ToolError`` for one MCP tool) becomes source
        ``mcp`` with ``refund`` in ``details``: the vocabulary stays closed and nothing a
        caller said is lost. Anything else is left for the field to accept or refuse."""
        if not isinstance(data, dict):
            return data
        source, details = data.get("source"), data.get("details")
        if not isinstance(source, str) or (details is not None and not isinstance(details, dict)):
            return data
        root, dot, qualifier = source.partition(".")
        if not dot or root not in ERROR_SOURCES:
            return data
        return {**data, "source": root, "details": {SOURCE_DETAIL: qualifier, **(details or {})}}

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
        """Normalize an exception. A :class:`HarnessError` carries its own classification.

        Anything else is sorted by :func:`classify` unless ``category`` is given. Whether it
        is retryable is, in order: ``retryable`` when passed; the exception's own
        ``retryable`` attribute when it has a bool one (the Memory Service SDK's and
        bifrost-sdk's errors do: the service that answered knows better than a class name);
        else whether the category is in :data:`RETRYABLE_CATEGORIES`. ``source`` must be an
        :data:`ErrorSource`, optionally qualified (``mcp.refund``)."""
        if isinstance(exc, HarnessError):
            return exc.to_error(trace_id=trace_id, source=source or exc.source)
        cat = category or classify(exc)
        if retryable is None:
            retryable = _own_retryable(exc)
        return cls.model_validate(
            {
                "code": type(exc).__name__,
                "category": cat,
                "message": str(exc)[:MESSAGE_MAX_CHARS],
                "retryable": cat in RETRYABLE_CATEGORIES if retryable is None else retryable,
                "source": source,
                "trace_id": trace_id,
            }
        )


def _own_retryable(exc: BaseException) -> bool | None:
    """What the exception itself says about retrying, when it says anything."""
    value = getattr(exc, "retryable", None)
    return value if isinstance(value, bool) else None


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

    Known Memory Service SDK, bifrost-sdk and httpx errors and stdlib timeouts are mapped
    explicitly; everything else is ``UNKNOWN`` (and therefore *not* retryable) rather than
    optimistically retried.
    """
    if isinstance(exc, asyncio.CancelledError):
        return ErrorCategory.CANCELLED
    if isinstance(exc, TimeoutError):  # asyncio.TimeoutError is this class since 3.11
        return ErrorCategory.TIMEOUT
    if isinstance(exc, ValueError | TypeError | KeyError):
        return ErrorCategory.VALIDATION
    if isinstance(exc, PermissionError):
        return ErrorCategory.AUTHORIZATION
    return _sdk_category(exc)


#: The packages whose exceptions are mapped by class name, so the contract never imports
#: them: the Memory Service SDK (``trellis.memory``), bifrost-sdk and httpx.
_SDK_MODULES: Final = frozenset({"trellis", "bifrost_sdk", "httpx"})
#: Class name to category. One table for every package above: a name means the same thing
#: whichever SDK raised it (``TimeoutError`` is the Memory Service SDK's, ``RateLimited``
#: and ``Unreachable`` are bifrost-sdk's, ``TimeoutException`` is httpx's timeout base).
_SDK_CATEGORIES: Final[dict[str, ErrorCategory]] = {
    "AuthenticationError": ErrorCategory.AUTHORIZATION,
    "AuthorizationError": ErrorCategory.AUTHORIZATION,
    "PermissionDeniedError": ErrorCategory.AUTHORIZATION,
    "ValidationError": ErrorCategory.VALIDATION,
    "BadRequestError": ErrorCategory.VALIDATION,
    "UnprocessableError": ErrorCategory.VALIDATION,
    "ConflictError": ErrorCategory.VALIDATION,
    "NotFoundError": ErrorCategory.DEPENDENCY,
    "RateLimitedError": ErrorCategory.RATE_LIMIT,
    "RateLimited": ErrorCategory.RATE_LIMIT,
    "DependencyUnavailableError": ErrorCategory.DEPENDENCY,
    "Unreachable": ErrorCategory.DEPENDENCY,
    "CircuitOpen": ErrorCategory.DEPENDENCY,
    "ServerError": ErrorCategory.DEPENDENCY,
    "GatewayError": ErrorCategory.DEPENDENCY,
    "EmptyResponse": ErrorCategory.MODEL,
    "InvalidJSON": ErrorCategory.MODEL,
    "InsufficientEvidence": ErrorCategory.MEMORY,
    "MemoryError": ErrorCategory.MEMORY,
    "TimeoutError": ErrorCategory.TIMEOUT,
    "ConnectError": ErrorCategory.DEPENDENCY,
    "NetworkError": ErrorCategory.DEPENDENCY,
    "RemoteProtocolError": ErrorCategory.DEPENDENCY,
    "ConnectTimeout": ErrorCategory.TIMEOUT,
    "ReadTimeout": ErrorCategory.TIMEOUT,
    "WriteTimeout": ErrorCategory.TIMEOUT,
    "PoolTimeout": ErrorCategory.TIMEOUT,
    "TimeoutException": ErrorCategory.TIMEOUT,
}


def _sdk_category(exc: BaseException) -> ErrorCategory:
    """Map SDK and transport errors by module and class name, nearest class first, so a
    subclass an SDK adds later (httpx's ``WriteTimeout`` under ``TimeoutException``, a new
    ``MemoryError``) is classified like its parent."""
    for cls in type(exc).__mro__:
        if cls.__module__.split(".")[0] in _SDK_MODULES and cls.__name__ in _SDK_CATEGORIES:
            return _SDK_CATEGORIES[cls.__name__]
    return ErrorCategory.UNKNOWN


# --------------------------------------------------------------------------- exceptions


class HarnessError(Exception):
    """Base class for failures the harness itself raises. ``source`` is an
    :data:`ErrorSource`, optionally qualified (``mcp.refund``); :meth:`to_error` refuses any
    other."""

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
        return AgentError.model_validate(
            {
                "code": self.code,
                "category": self.category,
                "message": self.message,
                "retryable": self.retryable,
                "source": source or self.source,
                "details": self.details,
                "trace_id": trace_id,
            }
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
