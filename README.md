# trellis-contracts

The types an agent must accept and return. No runtime, no transport, no I/O.

Every other repo in the platform depends on this one, and this one depends on nothing but
`pydantic`. That is the point: the contract can be read, versioned and reasoned about
without pulling in a gateway, a database or an agent framework.

## What is in here

| Module | What it defines |
| --- | --- |
| `messages` | `AgentRequest`, `AgentResponse`, `AgentStatus`: the two ends of one execution |
| `context` | `AgentExecutionContext`: tenant, user, thread, agent, run lineage, and `scope_fields()`, the exact keyword arguments the Memory Service expects |
| `artifacts` | What an execution produces on the way: artifact and evidence references, claims, recommended actions, memory observations, warnings |
| `tool` | `ToolSpec`, `ToolCall`, `ToolOutcome`, `ToolStatus` |
| `model` | `ModelRequest`, `ModelResponse`, `ModelUsage` |
| `errors` | The typed failures a caller can branch on, `AgentPaused`, and `classify()` |
| `events` | `AgentEvalEvent`, what a judge scores a finished run from |
| `descriptors` | `AgentDescriptor`, `SkillDescriptor` |
| `runs` | `RunStart`, `RunRecord`, `RunStatus`, the `RunEvent` stream, `Interrupt` and `InterruptResolution`, `ScheduleSpec` and `Schedule` |
| `feedback` | `Feedback` with its target kinds, verdicts and sources |
| `evaluation` | `JudgeVerdict`, `JudgeMethod` |
| `a2a` | `AgentCard` and its parts, mapped from `AgentDescriptor` |
| `ports` | Every outbound dependency as a Protocol: model, tool, artifact, memory, telemetry, redaction, evaluation, policy, event sink, run store, judge, agent directory, interceptor |
| `ids` | `new_id`, `stable_id`, `safe_id`, `now` |

## The shape of it

Two halves. **Records** travel between processes — a request, a result, a run event, a pause, a
judgement. **Ports** are the outbound dependencies, as `Protocol`s, so a harness can be built
against the seam and a service swapped behind it.

```mermaid
classDiagram
  direction LR
  class AgentExecutionContext {
    +tenant_id, workspace_id, user_id
    +thread_id, session_id, turn_id
    +agent_id, agent_run_id, parent_agent_run_id
    +request_id, correlation_id, trace_id, deadline
    +create(...) AgentExecutionContext
    +for_agent(agent_id) AgentExecutionContext
    +scope_fields() dict
    +idempotency_key(*parts) str
  }
  class AgentRequest {
    +request_id, objective, input
    +skills_requested, constraints, metadata
  }
  class AgentResponse {
    +status: AgentStatus
    +data, error, warnings, metrics
    +claims, evidence, artifacts
    +memory_observations, recommended_actions
    +succeeded
  }
  class RunStart {
    +run_id, tenant_id, agent_id, workspace_id
    +on_behalf_of, input, deadline
    +idempotency_key, webhook_url
  }
  class RunRecord {
    +status: RunStatus
    +awaiting: Interrupt
    +last_resolution: InterruptResolution
    +attempt
    +from_start(start, status) RunRecord
  }
  class RunEvent {
    +type: RunEventType
    +sequence, run_id, tenant_id
    +outcome: RunOutcome
  }
  class Interrupt {
    +interrupt_id, run_id, question
    +reason: InterruptReason
    +ui, expects, options
    +payload, payload_ref, tool_call
    +assignee, deadline, escalate_to
    +awaiting() dict
  }
  class InterruptResolution {
    +decision: InterruptDecision
    +answer, payload
  }
  class Feedback {
    +target_kind: FeedbackTargetKind
    +verdict: FeedbackVerdict
    +source: FeedbackSource
    +correction, score, reviewer
  }
  class JudgeVerdict {
    +method: JudgeMethod
    +score, label, rationale
    +as_feedback() Feedback
  }
  class AgentDescriptor {
    +agent_id, version
    +skills: SkillDescriptor[]
  }
  class AgentCard {
    +name, description, url
    +protocol_version
    +capabilities, skills, interfaces
  }
  class ScheduleSpec {
    +tenant_id, agent_id, name
    +cadence, timezone, on_behalf_of
    +input, workspace_id, enabled
  }
  class Schedule {
    +schedule_id, created_by
    +next_fire_at, last_fired_at, last_run_id
    +consecutive_failures, last_error, retry_after
  }

  AgentRequest --> AgentExecutionContext
  RunStart --> AgentExecutionContext : from_request
  RunRecord --|> RunStart
  RunRecord --> Interrupt : awaiting
  RunRecord --> InterruptResolution : last_resolution
  RunEvent --> Interrupt
  InterruptResolution --> Interrupt : interrupt_id
  Interrupt ..> Feedback : an approval is also feedback
  JudgeVerdict ..> Feedback : as_feedback()
  AgentDescriptor ..> AgentCard : mapped
  Schedule --|> ScheduleSpec
```

The ports, all of them, in one picture:

```mermaid
classDiagram
  direction LR
  class ModelClient {
    <<Protocol>>
    invoke()
    structured()
    stream()
  }
  class ToolClient {
    <<Protocol>>
    list_tools()
    call()
  }
  class ArtifactClient {
    <<Protocol>>
    put()
    get()
  }
  class MemoryPort {
    <<Protocol>>
    retrieve()
    observe()
    record_input()
    record_output()
    describe()
  }
  class TelemetryProvider {
    <<Protocol>>
    start_span()
    record_event()
    record_metric()
    flush()
  }
  class TelemetryRedactor {
    <<Protocol>>
    redact_attributes()
    redact_input()
    redact_output()
  }
  class EvaluationProvider {
    <<Protocol>>
    score()
    submit_dataset_item()
  }
  class AgentPolicyProvider {
    <<Protocol>>
    authorize_execution()
    authorize_tool()
    authorize_model()
  }
  class EventSink {
    <<Protocol>>
    publish(RunEvent)
  }
  class RunStore {
    <<Protocol>>
    queued()
    started()
    paused()
    resumed()
    finished()
    get()
    list_paused()
  }
  class Judge {
    <<Protocol>>
    judge()
  }
  class AgentDirectory {
    <<Protocol>>
    get()
    find()
    publish()
  }
  class AgentInterceptor {
    <<Protocol>>
    before()
    after()
    on_error()
  }
```

Each is a `Protocol`, so an implementation conforms by shape and nothing imports a base class from
here. `trellis-harness`'s `tests/contract/test_ports.py` asserts each of its adapters against the
protocol it claims.

## A run's lifecycle

`RunStatus.can_become(target)` is the one transition check; a run store refuses anything else.

```mermaid
stateDiagram-v2
  [*] --> QUEUED : RunStore.queued
  [*] --> RUNNING : RunStore.started
  QUEUED --> RUNNING : a worker claims it
  RUNNING --> QUEUED : the worker's lease lapsed
  RUNNING --> PAUSED : Interrupt
  PAUSED --> RUNNING : resumed in process
  PAUSED --> QUEUED : resumed for a worker
  QUEUED --> CANCELLED
  QUEUED --> TIMEOUT
  PAUSED --> CANCELLED
  PAUSED --> TIMEOUT : past the deadline, nobody to escalate to
  RUNNING --> SUCCESS
  RUNNING --> PARTIAL
  RUNNING --> ERROR
  RUNNING --> REJECTED
  RUNNING --> CANCELLED
  RUNNING --> TIMEOUT
```

A paused run waits on exactly one `Interrupt`: a `QUESTION`, an `APPROVAL` (carries the tool
call), a `REVIEW` (carries in `expects` what a correction looks like), a `CHOICE` (carries its
`options`) or an `AUTH`. `assignee` is who answers (`user:u1`, `role:procurement`); past
`deadline` the run goes to `escalate_to`, or times out when nobody is named.

## Versions, and what goes with what

The packages move together; a combination is "supported" when a test run exercised it, not when it
merely installs.

| Package | Version | Notes |
|---|---|---|
| `trellis-contracts` | **0.4.0** | this package: contracts v3 (ADR 0002) — queued runs, richer interrupts, the schedule fields agent-runs keeps, and only the ports something implements |
| `trellis-harness` | **0.4.0** | moves to contracts 0.4.0 in the same change set |
| `trellis-memory` (Memory Service SDK) | **0.3.0** | what the harness's memory port is written against |
| `pydantic` | `>=2.13,<3` | the only runtime dependency |
| Python | `>=3.12` | `StrEnum`, PEP 695 generics |

Two wire conventions worth knowing when reading the records:

* **What the platform writes and streams refuses unknown fields** — `RunStart`, `RunEvent`,
  `Interrupt`, `InterruptResolution`, `Feedback`, `JudgeVerdict`, `ScheduleSpec` — so a producer's
  typo is an error rather than a silently dropped field. What is *read back* from a store
  (`RunRecord`, `Schedule`) or from another agent (`AgentCard`) **ignores** them, so a newer peer
  does not break an older reader.
* **Every timestamp is timezone-aware.**

`A2A_PROTOCOL_VERSION` is `"1.0"`, the protocol `a2a-sdk` 1.x speaks; `trellis-harness`'s
compatibility tests pin it against the SDK's `PROTOCOL_VERSION_CURRENT`, so a card built here and
a card served over the wire say the same.

## Install

```bash
uv add trellis-contracts
```

## The one rule

`AgentExecutionContext.scope_fields()` applies the Memory Service's coherence rules at this
boundary — `agent_run_id` needs `agent_id`, `session_id` needs `thread_id`, `turn_id` needs
`session_id` — so a context that cannot be expressed coherently is corrected here rather
than rejected at the far end of an HTTP call.

## Tests

```bash
uv run pytest
```

## Contracts v3

Beyond the request/response pair, the package carries the seams the rest of the platform
builds on (ADRs 0001 and 0002 in `docs/adr`): `RunEvent` (the AG-UI event vocabulary plus
`CONTEXT_LOADED` and `INTERRUPT`), `Interrupt` and `InterruptResolution` (one pause
mechanism for every framework; approve, reject or edit of a tool call is also `Feedback`),
`Feedback` (human, judge and interrupt judgements on runs, answers, memories, tool calls,
briefs and procedures), `JudgeVerdict`, `AgentCard` (the A2A 1.0 card mapped from
`AgentDescriptor`), `RunStart`, `RunRecord`, `ScheduleSpec` and `Schedule` (what agent-runs
stores), and the ports `EventSink`, `RunStore`, `Judge` and `AgentDirectory`.

```python
from trellis.contracts import (
    AgentExecutionContext,
    AgentPaused,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    RunEvent,
    RunOutcome,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="buyer")
interrupt = Interrupt.from_paused(
    AgentPaused("Which supplier?"),
    context=ctx,
    reason=InterruptReason.CHOICE,
    ui="choice",
    options=["Acme", "Globex"],
    assignee="role:procurement",
)
event = RunEvent.finished(ctx, RunOutcome.INTERRUPT, sequence=7, interrupt=interrupt)
answer = InterruptResolution(
    interrupt_id=interrupt.interrupt_id,
    run_id=interrupt.run_id,
    decision=InterruptDecision.ANSWER,
    answer="Globex",
)
```
