"""Model-call contracts (§15/§16). Provider-neutral: no OpenAI/Anthropic/Bifrost types here."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trellis.contracts.artifacts import ArtifactRef


class ModelUsage(BaseModel):
    """Token and cost accounting, as far as the provider reports it."""

    model_config = ConfigDict(frozen=True, extra="allow")

    input_tokens: int | None = Field(
        default=None, description="Prompt tokens billed; None when not reported."
    )
    output_tokens: int | None = Field(
        default=None, description="Completion tokens billed; None when not reported."
    )
    total_tokens: int | None = Field(
        default=None, description="All tokens billed, as the provider reports it."
    )
    cached_input_tokens: int | None = Field(
        default=None, description="Prompt tokens served from the provider's cache."
    )
    reasoning_tokens: int | None = Field(
        default=None, description="Output tokens spent on reasoning before the answer."
    )
    cost_usd: float | None = Field(
        default=None, description="Cost of the call in US dollars; None when not reported."
    )

    @property
    def tokens(self) -> int | None:
        if self.total_tokens is not None:
            return self.total_tokens
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @classmethod
    def extract(cls, payload: Any) -> ModelUsage | None:
        """Best-effort usage from a provider response (dict or object) (§15 "where available").

        Recognises the common spellings (``prompt_tokens``/``input_tokens``,
        ``completion_tokens``/``output_tokens``). Returns ``None`` when nothing is reported —
        the harness never invents token counts.
        """
        usage = _get(payload, "usage") if not _is_usage_like(payload) else payload
        if usage is None:
            return None
        fields = {
            "input_tokens": _first(usage, "input_tokens", "prompt_tokens"),
            "output_tokens": _first(usage, "output_tokens", "completion_tokens"),
            "total_tokens": _first(usage, "total_tokens"),
            "cached_input_tokens": _first(usage, "cached_input_tokens", "cache_read_input_tokens"),
            "reasoning_tokens": _first(usage, "reasoning_tokens"),
            "cost_usd": _first(usage, "cost_usd", "cost", "total_cost"),
        }
        known: dict[str, Any] = {}
        for name, value in fields.items():
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            known[name] = float(value) if name == "cost_usd" else int(value)
        return cls(**known) if known else None


class ModelRequest(BaseModel):
    """One model invocation, as the harness sees it."""

    model_config = ConfigDict(extra="allow")

    model: str | None = Field(
        default=None, description="Model to call; None lets the client or profile choose."
    )
    provider: str | None = Field(
        default=None, description="Provider serving the model; None lets the gateway route."
    )
    profile: str | None = Field(
        default=None, description="Named model profile (model plus parameters) to use."
    )
    prompt_id: str | None = Field(
        default=None, description="Id of a managed prompt to render, instead of messages."
    )
    prompt_version: str | None = Field(
        default=None, description="Version of that managed prompt; None for the latest."
    )
    messages: list[dict[str, Any]] | None = Field(
        default=None, description="Chat messages ({role, content} objects) to send."
    )
    prompt: str | None = Field(
        default=None, description="A single text prompt, for a call without messages."
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Sampling and provider parameters (temperature, max_tokens...).",
    )
    tools: list[dict[str, Any]] = Field(
        default_factory=list, description="Tool definitions the model may call, as JSON."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form JSON object for tracing and routing."
    )


class ModelResponse(BaseModel):
    """The normalized result of a model call. ``raw`` keeps the provider object for callers."""

    model_config = ConfigDict(extra="allow")

    text: str | None = Field(default=None, description="The text the model returned.")
    data: Any = Field(
        default=None, description="Structured output, or the provider response when not text."
    )
    raw: Any = Field(default=None, description="The provider's response object, unchanged.")
    model: str | None = Field(default=None, description="Model that answered.")
    provider: str | None = Field(default=None, description="Provider that served it.")
    finish_reason: str | None = Field(
        default=None,
        description="Why generation stopped, in the provider's words.",
        examples=["stop", "length", "tool_calls"],
    )
    usage: ModelUsage | None = Field(
        default=None, description="Token and cost accounting; None when not reported."
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="Tool calls the model requested, as JSON."
    )
    fallback_used: bool = Field(
        default=False, description="Whether a fallback model answered instead of the first."
    )
    artifacts: list[ArtifactRef] = Field(
        default_factory=list, description="Large outputs passed by reference."
    )
    latency_ms: float | None = Field(
        default=None, description="Wall-clock duration of the call in milliseconds."
    )

    @classmethod
    def coerce(cls, value: Any, *, request: ModelRequest | None = None) -> ModelResponse:
        """Wrap a provider response without losing it."""
        if isinstance(value, cls):
            return value
        usage = ModelUsage.extract(value)
        text = value if isinstance(value, str) else _text_of(value)
        return cls(
            text=text,
            data=None if isinstance(value, str) else value,
            raw=value,
            model=_get(value, "model") or (request.model if request else None),
            provider=request.provider if request else None,
            finish_reason=_first(value, "finish_reason", "stop_reason"),
            usage=usage,
        )


def _is_usage_like(payload: Any) -> bool:
    return any(
        _get(payload, k) is not None for k in ("input_tokens", "prompt_tokens", "total_tokens")
    )


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _first(obj: Any, *keys: str) -> Any:
    for key in keys:
        value = _get(obj, key)
        if value is not None:
            return value
    return None


def _text_of(value: Any) -> str | None:
    for key in ("text", "content", "output_text"):
        found = _get(value, key)
        if isinstance(found, str):
            return found
    return None
