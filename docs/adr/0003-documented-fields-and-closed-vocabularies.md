# ADR 0003: Every field documented, closed vocabularies typed, SDK errors read as they say

**Status:** accepted · **Date:** 2026-10-04 · **Amends:** [ADR 0001](0001-contracts-v2.md),
[ADR 0002](0002-contracts-v3.md)

## Context
agent-runs serves these models over HTTP, so its OpenAPI schema is this package's field list.
A platform audit found three gaps. First, almost no field had a description, so the API
docs showed bare names such as `cadence`, `attempt` or `checkpoint`, with no units, formats
or allowed values. Second, several `str` fields only ever held a fixed set of values, and
nothing refused a typo in them. `AgentError.source` was one: a run failure recorded with
`source="langchain"` would be stored, and every `source == "langgraph"` filter would quietly
miss it. Third, `AgentError.of` ignored what an SDK exception says about itself. The Memory
Service SDK and bifrost-sdk both set `retryable` from the answer they got (a 409 the service
calls transient, a 4xx the gateway will never accept), and `of` replaced that with a guess
from the class name. The Memory Service SDK's own `TimeoutError` is not the builtin one, so
it was classified `UNKNOWN` and never retried.

## Decision
- **`AgentError.of` reads the exception first.** Retryability is decided in this order: the
  caller's `retryable=`, then the exception's own `retryable` attribute when it is a bool,
  then the category (`TIMEOUT`, `RATE_LIMIT` and `DEPENDENCY` are retryable). So the Memory
  Service SDK's base `MemoryError` keeps whatever the service said, and bifrost-sdk's
  `GatewayError` is retried for a 5xx and not for a 4xx.
- **Classification by name, nearest class first.** `classify` walks the exception's MRO and
  maps the first class from `trellis`, `bifrost_sdk` or `httpx` whose name it knows. A
  subclass an SDK adds later is classified like its parent (httpx's `WriteTimeout` under
  `TimeoutException`, a new `RateLimitedError`). Every timeout is `TIMEOUT` and retryable:
  the builtin (which `asyncio.TimeoutError` is), any SDK class named `TimeoutError`, and
  httpx's `TimeoutException` family. bifrost-sdk's names are mapped as well (`Unreachable`,
  `CircuitOpen`, `ServerError`, `RateLimited`, `EmptyResponse`, ...). Nothing is imported:
  the package still depends on `pydantic` alone.
- **`AgentError.source` is the `ErrorSource` literal.** Its values are the ones the sibling
  repos pass today: `agent-runs`, `tools`, `mcp`, `a2a`, and the harness's five adapter
  names (`function`, `langgraph`, `openai_agents`, `claude_agent_sdk`, `react`), which the
  harness records as the source of every failed run. `memory` is added for a Memory
  Service failure (`MemoryUnavailableError`), which nothing labels yet. The audit's list
  also had `human`, `system`, `local` and `openapi`. Those are `Feedback` and `ToolSpec`
  sources, not error sources, so they are not in it. A qualified value is split:
  the harness raises `ToolError(source=f"mcp.{tool}")`, and that becomes source `mcp` with
  `details.source_detail` set to the tool name. Any other value is refused. `HarnessError`
  still takes a plain `str` (it is an exception, not a record), and `to_error` validates it.
- **Other closed vocabularies become literals.** `ToolSpec.source` is `ToolSource`
  (`local`, `mcp`, `memory`, `openapi`, `a2a`), `MemoryObservation.kind` is
  `ObservationKind` (its refusal still lists what the Memory Service accepts), and
  `Interrupt.ui` is named as `InterruptUI`. They are `Literal` types, not `StrEnum`s, so a
  caller passing a string literal still type-checks. agent-runs and the harness run pyright
  in CI, and an enum would have failed every call site.
- **Left as `str`, on purpose.** Each of these was checked against the code that produces
  or reads it:
  - `ToolSpec.side_effects` (`read`, `write`, `irreversible`, or the default `unknown`): the
    harness's `side_effects_of` returns `str`, so a literal would break its type check.
    Tighten it once that function returns the harness's own `SideEffects` literal.
  - `EvidenceRef.source_type`: the Memory Service keeps it as an open `str`.
  - `AgentCard.preferred_transport` and `AgentInterface.transport`: A2A allows custom
    transports, and a card read from another agent must not be refused for naming one.
  - `AgentDescriptor.framework`: it is the adapter's name, which the harness types as `str`.
  - `ArtifactRef.type`, `ModelResponse.finish_reason` (the provider's own words), and the
    principals in `assignee`, `escalate_to` and `reviewer`.
- **Every field carries a description.** Each model field has a one-line
  `Field(description=...)`: what it is, what it is for, its unit or format (ISO 8601 and
  timezone-aware, seconds, bytes, US dollars) and its allowed values, with `examples=` where
  they help. `tests/test_field_docs.py` walks every model in every module and fails on a
  field without one, so the docs cannot fall behind again.
- **No version bump.** The harness's `uv.lock` records this package's version for its path
  dependency, and its CI runs `uv sync --locked`. A bump lands with the sibling relocks.

## Consequences
- An error stored with a source outside `ErrorSource` no longer reads back. No sibling
  writes one today. A new component adds its name to `ErrorSource` in the same change.
- A `ToolSpec` from a source outside `ToolSource` is refused. A new kind of tool source
  starts here.
- agent-runs' OpenAPI document and the JSON Schema of every model now describe every
  property, and list the allowed values of each literal.
