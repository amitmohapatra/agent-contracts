"""Model and tool calls, provider-neutral: what a model client is asked and returns, the usage
it reports, and what a tool is, is called with and returns."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from trellis.contracts import (
    ArtifactRef,
    Endpoint,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolOutcome,
    ToolSource,
    ToolSpec,
    ToolStatus,
)

# --------------------------------------------------------------------------- usage


@pytest.mark.parametrize(
    ("usage", "tokens"),
    [
        (ModelUsage(total_tokens=10, input_tokens=1, output_tokens=2), 10),  # reported total wins
        (ModelUsage(input_tokens=3, output_tokens=4), 7),
        (ModelUsage(input_tokens=3), 3),
        (ModelUsage(output_tokens=4), 4),
        (ModelUsage(), None),  # nothing reported is not zero
    ],
)
def test_tokens_is_the_total_or_the_sum_of_what_was_reported(
    usage: ModelUsage, tokens: int | None
) -> None:
    assert usage.tokens == tokens


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # OpenAI spelling, nested under ``usage``
        (
            {"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
            ModelUsage(input_tokens=3, output_tokens=4, total_tokens=7),
        ),
        # Anthropic spelling, with cache reads
        (
            {"usage": {"input_tokens": 5, "output_tokens": 6, "cache_read_input_tokens": 2}},
            ModelUsage(input_tokens=5, output_tokens=6, cached_input_tokens=2),
        ),
        # the usage object itself, with reasoning tokens and a cost under any of its names
        (
            {"input_tokens": 1, "reasoning_tokens": 9, "cost_usd": 0.25},
            ModelUsage(input_tokens=1, reasoning_tokens=9, cost_usd=0.25),
        ),
        ({"total_tokens": 8, "cost": 1}, ModelUsage(total_tokens=8, cost_usd=1.0)),
        ({"prompt_tokens": 2, "total_cost": 0.5}, ModelUsage(input_tokens=2, cost_usd=0.5)),
        # an explicit field wins over its alternative spelling
        (
            {"input_tokens": 1, "prompt_tokens": 99, "cached_input_tokens": 4},
            ModelUsage(input_tokens=1, cached_input_tokens=4),
        ),
        # attributes work as well as keys
        (
            SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=3)),
            ModelUsage(input_tokens=2, output_tokens=3),
        ),
        # floats are counted as whole tokens
        ({"usage": {"input_tokens": 2.0}}, ModelUsage(input_tokens=2)),
    ],
)
def test_usage_is_read_from_the_common_provider_spellings(
    payload: Any, expected: ModelUsage
) -> None:
    usage = ModelUsage.extract(payload)
    assert usage == expected
    assert usage is not None
    assert all(isinstance(v, int) for k, v in usage.model_dump().items() if k != "cost_usd" and v)
    assert usage.cost_usd is None or isinstance(usage.cost_usd, float)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "just text",
        {},
        {"usage": None},
        {"usage": {}},
        {"usage": {"input_tokens": "12"}},  # a string is not a count
        {"input_tokens": True, "output_tokens": False},  # neither is a bool
        SimpleNamespace(text="no usage here"),
    ],
)
def test_usage_is_never_invented(payload: Any) -> None:
    assert ModelUsage.extract(payload) is None


def test_usage_is_frozen_and_keeps_extra_provider_fields() -> None:
    usage = ModelUsage(input_tokens=1, provider_detail="x")  # type: ignore[call-arg]
    assert usage.model_dump()["provider_detail"] == "x"
    with pytest.raises(ValidationError):
        usage.input_tokens = 2  # type: ignore[misc]


# --------------------------------------------------------------------------- request / response


def test_a_model_request_defaults_to_nothing_and_keeps_extras() -> None:
    request = ModelRequest(prompt="hi", seed=7)  # type: ignore[call-arg]
    assert request.model is None and request.messages is None
    assert request.params == {} and request.tools == [] and request.metadata == {}
    assert request.model_dump()["seed"] == 7


def test_coerce_passes_a_response_through() -> None:
    response = ModelResponse(text="x")
    assert ModelResponse.coerce(response) is response


def test_coerce_wraps_a_string_as_text_and_takes_the_model_from_the_request() -> None:
    request = ModelRequest(model="model-a", provider="provider-a")
    response = ModelResponse.coerce("hello", request=request)
    assert response.text == "hello" and response.raw == "hello" and response.data is None
    assert response.model == "model-a" and response.provider == "provider-a"
    assert response.usage is None and response.finish_reason is None
    bare = ModelResponse.coerce("hello")
    assert bare.model is None and bare.provider is None


def test_coerce_reads_a_provider_dict_without_losing_it() -> None:
    payload = {
        "model": "model-b",
        "content": "the answer",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }
    response = ModelResponse.coerce(payload, request=ModelRequest(model="ignored", provider="p"))
    assert response.text == "the answer"
    assert response.data is payload and response.raw is payload
    assert response.model == "model-b"  # what the provider says it ran wins over the request
    assert response.provider == "p"
    assert response.finish_reason == "end_turn"
    assert response.usage == ModelUsage(input_tokens=4, output_tokens=5)


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        ({"text": "t", "content": "c"}, "t"),  # tried in order: text, content, output_text
        ({"content": "c", "output_text": "o"}, "c"),
        ({"output_text": "o"}, "o"),
        ({"content": [{"type": "text", "text": "blocks"}]}, None),  # only a string is text
        (SimpleNamespace(output_text="from an object"), "from an object"),
        ({"data": 1}, None),
    ],
)
def test_coerce_finds_the_text_under_its_usual_names(payload: Any, text: str | None) -> None:
    assert ModelResponse.coerce(payload).text == text


def test_coerce_prefers_finish_reason_over_stop_reason() -> None:
    payload = {"finish_reason": "stop", "stop_reason": "end_turn"}
    assert ModelResponse.coerce(payload).finish_reason == "stop"


def test_a_model_response_keeps_any_raw_object_and_round_trips_its_own_fields() -> None:
    raw = object()
    response = ModelResponse(
        text="x",
        raw=raw,
        usage=ModelUsage(total_tokens=3),
        artifacts=[ArtifactRef(artifact_id="a")],
        tool_calls=[{"name": "t"}],
        fallback_used=True,
        latency_ms=12.0,
    )
    assert response.raw is raw
    dumped = response.model_dump(exclude={"raw"})
    assert ModelResponse.model_validate(dumped) == response.model_copy(update={"raw": None})


# --------------------------------------------------------------------------- endpoints

#: A LangChain chat model's ``profile`` as LangChain gives it: many keys, four of them ours.
LANGCHAIN_PROFILE = {
    "name": "a reasoning model",
    "max_input_tokens": 400_000,
    "max_output_tokens": 128_000,
    "reasoning_output": True,
    "reasoning_effort_levels": ["low", "medium", "high"],
    "tool_calling": True,
    "structured_output": True,
    "temperature": False,
    "image_inputs": True,
}


def test_a_profile_defaults_to_a_plain_chat_model() -> None:
    assert ModelProfile().model_dump() == {
        "reasoning_output": False,
        "temperature": True,
        "max_output_tokens": None,
        "tool_calling": True,
    }


def test_a_langchain_profile_validates_as_one_and_its_other_keys_are_dropped() -> None:
    profile = ModelProfile.model_validate(LANGCHAIN_PROFILE)
    assert profile == ModelProfile(
        reasoning_output=True, temperature=False, max_output_tokens=128_000, tool_calling=True
    )
    assert "max_input_tokens" not in profile.model_dump()


def test_a_profile_is_frozen_and_refuses_a_budget_below_one() -> None:
    with pytest.raises(ValidationError):
        ModelProfile().temperature = False  # type: ignore[misc]
    with pytest.raises(ValidationError, match="max_output_tokens"):
        ModelProfile(max_output_tokens=0)


def test_an_endpoint_defaults_to_a_bearer_openai_compatible_route_and_a_plain_model() -> None:
    endpoint = Endpoint(base_url="https://api.openai.com/v1")
    assert (endpoint.gateway, endpoint.auth, endpoint.model, endpoint.api_key) == (
        "openai-compatible",
        "bearer",
        None,
        None,
    )
    assert endpoint.profile == ModelProfile()


def test_an_endpoint_masks_its_key_and_a_sender_reveals_it_for_the_wire() -> None:
    endpoint = Endpoint(base_url="http://bifrost:8080/v1", gateway="bifrost", api_key="sk-1")
    assert "sk-1" not in repr(endpoint)
    assert "sk-1" not in endpoint.model_dump_json()
    assert endpoint.api_key is not None
    wire = endpoint.model_dump(mode="json") | {"api_key": endpoint.api_key.get_secret_value()}
    assert Endpoint.model_validate(wire) == endpoint


def test_an_endpoint_carries_a_profile_and_round_trips() -> None:
    endpoint = Endpoint(
        base_url="https://aiplatform.googleapis.com/v1/projects/p/locations/l/endpoints/openapi",
        auth="google",
        model="google/gemini-2.5-pro",
        profile=LANGCHAIN_PROFILE,  # type: ignore[arg-type]
    )
    assert endpoint.profile.reasoning_output
    assert Endpoint.model_validate(endpoint.model_dump(mode="json")) == endpoint


@pytest.mark.parametrize(
    "fields",
    [
        {"base_url": "localhost:8080/v1"},  # no scheme
        {"base_url": "ftp://models/v1"},
        {"base_url": "https://x/v1", "gateway": ""},
        {"base_url": "https://x/v1", "auth": ""},
        {"base_url": "https://x/v1", "region": "us-east-1"},  # an unknown field
    ],
)
def test_an_endpoint_refuses_what_no_consumer_could_call(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate(fields)


def test_an_endpoint_is_frozen() -> None:
    with pytest.raises(ValidationError):
        Endpoint(base_url="https://x/v1").model = "m"  # type: ignore[misc]


# --------------------------------------------------------------------------- tools


def test_a_tool_spec_describes_itself_for_the_memory_service() -> None:
    spec = ToolSpec(
        name="billing.refund",
        description="refund an order",
        input_schema={"type": "object"},
        tags=["billing"],
        server="mcp://billing",
        idempotent=True,
    )
    descriptor = spec.descriptor()
    assert descriptor == {
        "name": "billing.refund",
        "description": "refund an order",
        "input_schema": {"type": "object"},
        "tags": ["billing"],
    }
    descriptor["tags"].append("mutated")
    assert spec.tags == ["billing"]  # the descriptor is a copy


def test_a_tool_spec_defaults_to_a_local_tool_with_unknown_side_effects() -> None:
    spec = ToolSpec(name="t")
    assert (spec.version, spec.source, spec.side_effects, spec.idempotent) == (
        "1",
        "local",
        "unknown",
        False,
    )
    assert spec.descriptor()["input_schema"] is None


@pytest.mark.parametrize("source", get_args(ToolSource))
def test_every_tool_source_the_harness_builds_is_accepted(source: str) -> None:
    assert ToolSpec(name="t", source=source).source == source  # type: ignore[arg-type]


def test_a_tool_source_outside_the_vocabulary_is_refused() -> None:
    """A typo (``MCP``, ``gateway``) would silently miss every ``source == "mcp"`` branch."""
    with pytest.raises(ValidationError, match="local"):
        ToolSpec(name="t", source="gateway")  # type: ignore[arg-type]


def test_a_tool_call_names_the_tool_and_is_frozen() -> None:
    call = ToolCall(tool="search", args={"q": "x"}, step=2, idempotency_key="k")
    assert call.task == "" and call.step == 2
    with pytest.raises(ValidationError):
        call.tool = "other"  # type: ignore[misc]
    assert ToolCall.model_validate_json(call.model_dump_json()) == call


def test_a_tool_outcome_defaults_to_one_ok_attempt() -> None:
    outcome = ToolOutcome(tool="search", output=[1, 2])
    assert outcome.status is ToolStatus.OK and outcome.ok
    assert outcome.attempts == 1 and not outcome.cached and outcome.artifacts == []
    failed = ToolOutcome(tool="search", status=ToolStatus.TIMEOUT, error_class="ReadTimeout")
    assert not failed.ok and failed.error_class == "ReadTimeout"
