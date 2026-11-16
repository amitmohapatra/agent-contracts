# ADR 0010: Exported names no sibling imports leave in 0.8.0

**Status:** proposed · **Date:** 2026-11-16

Nothing is deleted by this record. It says which names have no consumer and what 0.8.0 does
with each; the removal is its own change, made when this record is accepted. Accepting it
amends [ADR 0001](0001-contracts-v2.md) and [ADR 0002](0002-contracts-v3.md), whose headers
gain "amended by" then.

The `background-agents` branch also numbers a record 0010 (a run's usage, named in the
changelog under 0.6.4). It takes the next free number when it lands on `main`.

## Context
The package exports 85 names (`trellis.contracts.__all__`). On 2026-11-16 the source of the
three siblings that depend on it was read for imports of them: agent-harness (`src`),
agent-runs (`src` and its SDK `trellis.runs`) and agent-memory-service (`src` and its SDK,
without its vendored copy of this package). An import is `from trellis.contracts import X`,
the same from a submodule, or `contracts.X`.

**46 of the 85 names are imported by no sibling's source.** A search for the bare word finds
32, because the word is often not this type: the harness's `AgentCard` is the A2A SDK's, and
the Memory Service's `Claim` and `ToolSource` are its own.

ADR 0002 already set the rule for ports: only the ports something implements. Eleven of the
twelve ports exported today have no implementer, and the shapes those ports
exchange (the response, the model request, the eval event) have no reader either. Each unused
name is a promise the package keeps: a field description, a test, a row in
[docs/api.md](../api.md), and a reason for a consumer to wonder which of two types to use
(the harness's `Result` or `AgentResponse`, its `EvalScore` or `JudgeVerdict`).

## Decision
A name stays when a sibling imports it, when a sibling's tests or examples do, or when it is
the type of a field on a record that stays. Every other name leaves in 0.8.0.

**Seven of the 46 stay.**

| Name | Why it stays |
|---|---|
| `AgentRequest` | `RunStart.from_request(AgentRequest.create(ctx, input))` is how agent-runs' examples start a run from a context |
| `classify` | `AgentError.of` sorts by it; the harness's tests and its Way 2 example call it |
| `TelemetryRedactor` | the one port with an implementer: the harness's `Redactor`, asserted in its `tests/contract/test_ports.py` |
| `EvidenceRef` | the type of `Feedback.evidence_refs` and `AgentRequest.evidence_refs` |
| `InterruptUI` | the type of `Interrupt.ui` |
| `ToolSource` | the type of `ToolSpec.source` |
| `ERROR_SOURCES` | `ErrorSource` as a set, which `AgentError` reads |

**39 leave**, in seven groups.

| Group | Names | Why nothing reads them |
|---|---|---|
| Ports without an implementer (11) | `AgentDirectory`, `AgentInterceptor`, `AgentPolicyProvider`, `ArtifactClient`, `EvaluationProvider`, `EventSink`, `Judge`, `MemoryPort`, `ModelClient`, `TelemetryProvider`, `ToolClient` | the harness has its own event sink and runs client, and uses the `trellis-memory` SDK and `bifrost_sdk` directly |
| The response and what only it carries (8) | `AgentResponse`, `AgentStatus`, `AgentWarning`, `Claim`, `MemoryObservation`, `RecommendedAction`, `ObservationKind`, `OBSERVATION_KINDS` | the harness returns its own `Result`; no block reads `memory_observations` |
| Model call shapes (3) | `ModelRequest`, `ModelResponse`, `ModelUsage` | the shapes of `ModelClient`; `bifrost_sdk` is the model client |
| Agent card (6) | `AgentCard`, `AgentCapabilities`, `AgentInterface`, `AgentProvider`, `AgentSkill`, `A2A_PROTOCOL_VERSION` | the harness serves and reads cards with the A2A SDK's types |
| Descriptors (2) | `AgentDescriptor`, `SkillDescriptor` | the input of `AgentCard.from_descriptor` only |
| Errors nobody raises (6) | `AgentCancelledError`, `AgentTimeoutError`, `MemoryUnavailableError`, `PolicyDeniedError`, `ResultValidationError`, `ToolNotFoundError` | the harness raises `ConfigurationError`, `ToolError` and `ModelError` |
| Judging (3) | `AgentEvalEvent`, `JudgeVerdict`, `JudgeMethod` | the harness scores with its own `EvalScore`; the Memory Service's judge files its own `Feedback` |

Removal is the default. A group moves to the harness instead when the harness takes it as a
type of its own in the same change (the response as the declared return of a wrapped agent,
or the six errors if it starts raising them). Nothing moves without a reader.

Two members of a vocabulary that stays leave with them: `FeedbackTargetKind.ANSWER` and
`FeedbackTargetKind.BRIEF`. The Memory Service, the only store of feedback, keeps `run`,
`memory`, `tool_call` and `procedure` and refuses the other two.
`JudgeVerdict.as_feedback` already targets `run`. They stay if the Memory Service adds the
two kinds before 0.8.0.

What the change has to carry with it:

- `RunStatus.from_agent_status`, and the `AgentStatus` in the signature of
  `RunOutcome.from_status`, go with `AgentStatus`: `from_status` takes a `RunStatus` or its
  name, which is all the harness passes.
- `ports.py` keeps `TelemetryRedactor`. `a2a.py`, `descriptors.py`, `evaluation.py`,
  `events.py` and `model.py`'s request and response go, with their tests and the examples
  that show them (`examples/07_agent_card_a2a.py`, and the response and error-class parts of
  `examples/01_context_request_response.py` and `examples/08_errors_and_classify.py`).
  `Endpoint` and `ModelProfile` stay in `model.py`.
- Each removed name joins `test_retired_names_are_gone`, and
  [docs/api.md](../api.md), [docs/ARCHITECTURE.md](../ARCHITECTURE.md) and the README lose
  its rows. No aliases ([the version rules](../versioning.md#the-version-rules)).
- Before the removal, repeat the measurement on each sibling's `main`. A name a sibling has
  started to import since stays.

## Consequences
- The package exports 46 names instead of 85, and every one has a reader.
- It is the 0.8.0 minor, the same release that refuses a naive context deadline: each sibling
  raises its `<0.8` pin once, in the change that takes 0.8.0.
- A consumer outside these repos that imports one of the 39 names breaks at import on
  0.8.0. None is known. The changelog names each removed name.
- Until 0.8.0 nothing changes: every name above is still exported and tested.
