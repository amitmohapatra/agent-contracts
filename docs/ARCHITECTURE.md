# Architecture

`trellis-contracts` is types and nothing else: Pydantic records, `StrEnum` vocabularies and
`typing.Protocol` ports. It has no runtime, transport or I/O, and its only dependency is
`pydantic` (`tests/test_contracts.py` fails the build if a module imports anything else).
This page shows how the pieces fit, who uses which, and the one state machine the package
owns. The decisions behind it are ADRs [0001](adr/0001-contracts-v2.md),
[0002](adr/0002-contracts-v3.md), [0003](adr/0003-documented-fields-and-closed-vocabularies.md),
[0004](adr/0004-no-run-store-port.md), [0005](adr/0005-run-working-time-and-agent-version.md),
[0006](adr/0006-interrupts-v2-and-queue-order.md) and
[0007](adr/0007-schedules-carry-queue-order-and-metadata.md).

## In the platform

Two sibling repos import it (agent-harness, and agent-runs with its SDK `trellis.runs`); the
Memory Service speaks some of its shapes without importing it; `bifrost-sdk` is independent
of it. The arrows from the harness are Way 1 of the
[two ways to use Trellis](../README.md#where-this-fits-two-ways-to-use-trellis); in Way 2 your
own code takes the harness's place and sends the same records with `trellis.runs` and
`trellis.memory`.

```mermaid
flowchart LR
  CT["trellis-contracts<br/>(this package)"]
  H["agent-harness<br/>(trellis-harness)"]
  R["agent-runs<br/>(runs, schedules, inbox)"]
  M["agent-memory-service<br/>(trellis-memory)"]
  B["bifrost-sdk<br/>(LLM gateway client)"]

  H -->|imports| CT
  R -->|imports| CT
  H -->|"RunStart, Interrupt, InterruptResolution<br/>over HTTP"| R
  H -->|"Feedback from InterruptResolution.to_feedback<br/>via the trellis-memory SDK"| M
  H -->|model calls| B
  M -. "accepts Feedback as is (POST /v1/feedback);<br/>scope_fields() = its Scope;<br/>OBSERVATION_KINDS = its ObservationKind" .- CT
```

| Repo | What it takes from here |
| --- | --- |
| `agent-harness` | The run event stream (`RunEvent`, `RunEventType`, `RunOutcome`); the pause (`Interrupt`, `InterruptReason`, `InterruptDecision`, `InterruptResolution` and its `to_feedback`); its runs client's records (`RunStart`, `RunRecord`, `RunStatus.can_become`, `Schedule`, `ScheduleSpec`); tool types (`ToolSpec`, `ToolCall`, `ToolOutcome`, `ToolStatus`); `AgentExecutionContext`; errors (`AgentError`, `ConfigurationError`, `ToolError`, `ModelError`); `FeedbackVerdict`; the id helpers. Its `trellis.harness.redaction.Redactor` implements `TelemetryRedactor`, asserted in its `tests/contract/test_ports.py`. |
| `agent-runs` | Its stored and returned shapes: `RunStart` (its create body subclasses it), `RunRecord`, `RunStatus.can_become` (checked under a row lock), `Interrupt` (read back from `awaiting`), `InterruptResolution`, `InterruptDecision`, `Schedule`, `ScheduleSpec`, `AgentError`, `ErrorCategory`, `ArtifactRef`, `ToolCall`, and `now`, `new_id`, `stable_id`. |
| `agent-memory-service` | Nothing imported. Its `POST /v1/feedback` takes the `Feedback` record unchanged; `AgentExecutionContext.scope_fields()` returns exactly its `Scope` keywords; `OBSERVATION_KINDS` mirrors its `ObservationKind`; `classify()` maps its SDK's exceptions (module `trellis.*`) by class name, and `AgentError.of` keeps their own `retryable`. |
| `bifrost-sdk` | Nothing. The harness reaches models through it directly. `classify()` maps its exceptions (module `bifrost_sdk`) by class name, and `AgentError.of` keeps their own `retryable`. |

## Inside the package

Each arrow is an import. Records sit low, ports sit on top, and `__init__` re-exports every
public name.

```mermaid
flowchart BT
  ids["ids<br/>new_id, stable_id, safe_id, now"]
  artifacts["artifacts<br/>ArtifactRef, EvidenceRef, Claim, ..."]
  errors["errors<br/>AgentError, AgentPaused, HarnessError family"]
  descriptors["descriptors<br/>AgentDescriptor, SkillDescriptor"]
  context["context<br/>AgentExecutionContext"]
  tool["tool<br/>ToolSpec, ToolCall, ToolOutcome"]
  model["model<br/>ModelRequest, ModelResponse, ModelUsage"]
  messages["messages<br/>AgentRequest, AgentResponse, AgentStatus"]
  feedback["feedback<br/>Feedback"]
  events["events<br/>AgentEvalEvent"]
  evaluation["evaluation<br/>JudgeVerdict"]
  a2a["a2a<br/>AgentCard"]
  runs["runs<br/>RunStatus, RunStart, RunRecord, Interrupt,<br/>RunEvent, ScheduleSpec, Schedule"]
  ports["ports<br/>12 Protocols"]

  context --> ids
  tool --> artifacts
  model --> artifacts
  a2a --> descriptors
  messages --> artifacts & context & errors
  feedback --> artifacts & context & ids
  events --> artifacts & messages
  evaluation --> artifacts & events & feedback
  runs --> artifacts & context & errors & feedback & ids & messages & tool
  ports --> a2a & artifacts & context & errors & evaluation & events & messages & model & runs & tool
```

## The ports

Every outbound dependency is a `runtime_checkable` `Protocol`: an implementation conforms by
shape, with no base class imported from here. The harness core is written against the port;
an adapter is injected behind it.

```mermaid
flowchart LR
  core(["an agent harness<br/>(written against the ports)"])

  subgraph exec[Executing an agent]
    MC[ModelClient]
    TC[ToolClient]
    AC[ArtifactClient]
    MP[MemoryPort]
    AI[AgentInterceptor]
    PP[AgentPolicyProvider]
  end
  subgraph observe[Observing it]
    TP[TelemetryProvider]
    TR[TelemetryRedactor]
    EP[EvaluationProvider]
    JU[Judge]
  end
  subgraph durable[Runs and surfaces]
    ES[EventSink]
    AD[AgentDirectory]
  end

  core --> exec
  core --> observe
  core --> durable

  TR -. "implemented by trellis.harness.redaction.Redactor" .- HR["agent-harness"]
  core -. "runs: no port; trellis.runs.RunsClient<br/>sends and returns the run types" .- RUNS["agent-runs"]
  MP -. "fronts the Memory Service<br/>(no adapter declares it today)" .- MEM["agent-memory-service"]
```

| Port | Methods | Use it to | Implemented today |
| --- | --- | --- | --- |
| `ModelClient` | `invoke`, `structured`, `stream` | call a model provider-neutrally | no sibling declares it; the harness calls models through `bifrost-sdk` |
| `ToolClient` | `list_tools`, `call` | list and call tools (local, MCP, gateway) | no sibling declares it |
| `ArtifactClient` | `put`, `get` | move large payloads out of results and state | no sibling declares it |
| `MemoryPort` | `enabled`, `retrieve`, `observe`, `record_input`, `record_output`, `describe` | read and write the Memory Service | no sibling declares it; the harness uses the `trellis-memory` SDK |
| `TelemetryProvider` | `start_span`, `record_event`, `record_metric`, `flush` | emit spans, events and metrics | no sibling declares it |
| `TelemetryRedactor` | `redact_attributes`, `redact_input`, `redact_output` | strip what may not leave the process | `agent-harness` `Redactor` (asserted) |
| `EvaluationProvider` | `score`, `submit_dataset_item` | write scores and dataset items to the tracing backend | no sibling declares it |
| `AgentPolicyProvider` | `authorize_execution`, `authorize_tool`, `authorize_model` | allow or refuse a run, a tool call, a model call | no sibling declares it |
| `EventSink` | `publish` | deliver a run's `RunEvent` stream | no sibling declares it |
| `Judge` | `judge` | score a finished run off the critical path | no sibling declares it |
| `AgentDirectory` | `get`, `find`, `publish` | find agents another agent may call, as `AgentCard`s | no sibling declares it |
| `AgentInterceptor` | `name`, `order`, `before`, `after`, `on_error` | add a stage to the execution pipeline | no sibling declares it |

"No sibling declares it" means none of the four sibling repos asserts or imports that port
today. The port is still the seam an adapter is written against, and this package's tests
check each one against a double with the exact signatures (`tests/test_ports.py`,
`tests/test_contracts_v2.py`).

Runs have no port (ADR 0004). A run is kept beyond the process by agent-runs, and its client
is `trellis.runs.RunsClient` (pip `trellis-runs`, shipped from agent-runs). Its verbs are the
service's operation ids (`start`, `claim`, `heartbeat`, `pause`, `resume`, `finish`, `get`,
`list`), and it sends and returns the types in this package: `RunStart`, `Interrupt`,
`InterruptResolution` and `RunRecord`.

## A run's lifecycle

`RunStatus.can_become(target)` is the one transition check. It reads the `_TRANSITIONS` table
in `runs.py`, and a final status has no entry, so it moves nowhere. `RunRecord.from_start`
only creates `QUEUED` or `RUNNING` records. The labels are `RunsClient` verbs.

```mermaid
stateDiagram-v2
  [*] --> QUEUED : start(queue=True) / from_start(status=QUEUED)
  [*] --> RUNNING : start / from_start()
  QUEUED --> RUNNING : claim, by a worker
  QUEUED --> CANCELLED
  QUEUED --> TIMEOUT
  RUNNING --> QUEUED : the worker's lease lapsed
  RUNNING --> PAUSED : pause(interrupt, checkpoint=)
  RUNNING --> SUCCESS : finish
  RUNNING --> PARTIAL : finish
  RUNNING --> ERROR : finish
  RUNNING --> REJECTED : finish
  RUNNING --> CANCELLED : finish
  RUNNING --> TIMEOUT : finish
  PAUSED --> RUNNING : resume, in process
  PAUSED --> QUEUED : resume, for a worker
  PAUSED --> CANCELLED
  PAUSED --> TIMEOUT : past the deadline, nobody to escalate to
  SUCCESS --> [*]
  PARTIAL --> [*]
  ERROR --> [*]
  REJECTED --> [*]
  CANCELLED --> [*]
  TIMEOUT --> [*]
```

`RunRecord` checks these whenever it is built:

* A `PAUSED` record carries `awaiting` (the `Interrupt`), and no other record does. The
  interrupt names the same `run_id` and `tenant_id` as the record.
* Only `ERROR`, `TIMEOUT` and `REJECTED` carry an `error`.
* A final record carries no `checkpoint`.
* `attempt >= 1`, and every timestamp is timezone-aware.

`RunOutcome.from_status` maps a settled status to what `RUN_FINISHED` reports. `PAUSED`
becomes `interrupt`, the endings become their lower-case names, and `QUEUED` and `RUNNING`
raise `ValueError` because they have no outcome yet.

### Pausing, answering and resuming

A run that pauses for an approval, driven the way the contract expects. agent-harness
pauses through its own `Runtime.ask` rather than raising `AgentPaused`, but it resumes in this
order: agent-runs accepts the resolution before the feedback is sent, so a decision that never
took effect is never learned from.

```mermaid
sequenceDiagram
  autonumber
  participant A as Agent
  participant H as Harness
  participant S as RunsClient (agent-runs)
  participant E as EventSink
  participant P as Person / UI
  participant M as Memory Service

  H->>S: start(RunStart.from_request(request))
  S-->>H: RunRecord (RUNNING)
  H->>E: publish(RunEvent.started(ctx, 0))
  A-->>H: raise AgentPaused("Approve the refund?")
  H->>H: Interrupt.from_paused(paused, context=ctx, reason=APPROVAL, tool_call=...)
  H->>S: pause(interrupt, checkpoint={...})
  S-->>H: RunRecord (PAUSED, awaiting=interrupt)
  H->>E: publish(RunEvent.finished(ctx, "interrupt", n, interrupt=interrupt))
  P->>H: InterruptResolution(decision=APPROVE)
  H->>S: resume(resolution)
  S-->>H: RunRecord (RUNNING, attempt+1, checkpoint returned)
  H->>M: resolution.to_feedback(interrupt, ctx) as Feedback
  A-->>H: AgentResponse.ok(data)
  H->>S: finish(run_id, SUCCESS, output=data)
  S-->>H: RunRecord (SUCCESS, checkpoint cleared)
  H->>E: publish(RunEvent.finished(ctx, "success", n+1))
```

## The records

### Request, response and context

```mermaid
classDiagram
  direction LR
  class AgentExecutionContext {
    <<frozen, closed>>
    +tenant_id, workspace_id, user_id, group_ids
    +thread_id, session_id, turn_id
    +work_id, task_id
    +agent_id, agent_group_id, agent_run_id, parent_agent_run_id
    +request_id, correlation_id, causation_id, trace_id
    +deadline
    +create(tenant_id, ...)$ AgentExecutionContext
    +for_agent(agent_id, ...) AgentExecutionContext
    +with_fields(**changes) AgentExecutionContext
    +with_deadline(deadline) AgentExecutionContext
    +remaining_seconds float
    +expired bool
    +idempotency_key(*parts, prefix) str
    +scope_fields() dict
    +log_fields() dict
  }
  class AgentRequest {
    <<frozen, open>>
    +request_id, objective, input
    +skills_requested, constraints
    +artifact_refs, evidence_refs, metadata
    +create(context, input, **fields)$ AgentRequest
    +with_fields(**changes) AgentRequest
    +query str
  }
  class AgentResponse {
    <<open>>
    +status: AgentStatus
    +data, confidence, metrics
    +claims, evidence, artifacts
    +memory_observations, recommended_actions, warnings
    +error: AgentError
    +ok(data)$ AgentResponse
    +failed(error, status)$ AgentResponse
    +coerce(value)$ AgentResponse
    +succeeded bool
    +add_warning(code, message, **details) AgentResponse
  }
  class AgentStatus {
    <<StrEnum>>
    SUCCESS
    PARTIAL
    ERROR
    TIMEOUT
    CANCELLED
    REJECTED
    PAUSED
    +ok bool
  }
  class AgentError {
    <<frozen>>
    +code, category: ErrorCategory
    +message, retryable, source: ErrorSource
    +details, trace_id
    +of(exc, ...)$ AgentError
  }
  AgentRequest --> AgentExecutionContext : context
  AgentRequest --> ArtifactRef
  AgentRequest --> EvidenceRef
  AgentResponse --> AgentStatus
  AgentResponse --> AgentError
  AgentResponse --> Claim
  AgentResponse --> EvidenceRef
  AgentResponse --> ArtifactRef
  AgentResponse --> MemoryObservation
  AgentResponse --> RecommendedAction
  AgentResponse --> AgentWarning
```

"Frozen" models refuse assignment. "Closed" ones refuse unknown fields (`extra="forbid"`),
and "open" ones keep them (`extra="allow"`).

### Runs, interrupts, feedback and schedules

```mermaid
classDiagram
  direction LR
  class RunStart {
    <<frozen, closed>>
    +run_id, tenant_id, agent_id, parent_run_id
    +thread_id, user_id, workspace_id, on_behalf_of
    +input, deadline, timeout_seconds, idempotency_key
    +agent_version, priority, concurrency_key, metadata
    +from_request(request)$ RunStart
  }
  class RunRecord {
    <<frozen, ignores unknown>>
    +status: RunStatus
    +output, error: AgentError
    +awaiting: Interrupt
    +last_resolution: InterruptResolution
    +checkpoint, attempt, worked_seconds, created_at, updated_at
    +final bool
    +from_start(start, status)$ RunRecord
  }
  class Interrupt {
    <<frozen, closed>>
    +interrupt_id, tenant_id, run_id
    +reason: InterruptReason
    +question, ui, expects, ui_schema
    +options: str | Option, multiple, component, props
    +payload, payload_ref: ArtifactRef, tool_call: ToolCall
    +assignee, deadline, escalate_to, created_at
    +from_paused(paused, context, reason, **fields)$ Interrupt
    +option_values list
    +awaiting() dict
  }
  class Option {
    <<frozen, closed>>
    +value, label, description
  }
  class InterruptResolution {
    <<frozen, closed>>
    +interrupt_id, run_id
    +decision: InterruptDecision
    +answer, reviewer, payload, comment
    +remember: once | run, resolved_at
    +resolves(interrupt) bool
    +feedback_id str
    +to_feedback(interrupt, context) Feedback
  }
  class Feedback {
    <<frozen, closed>>
    +feedback_id, tenant_id, workspace_id, user_id
    +agent_id, agent_run_id, trace_id
    +target_kind: FeedbackTargetKind
    +target_id, verdict: FeedbackVerdict
    +source: FeedbackSource
    +score, correction, comment, reviewer
    +evidence_refs, metadata, created_at
    +for_context(context, ...)$ Feedback
  }
  class JudgeVerdict {
    <<frozen, closed>>
    +score, method: JudgeMethod
    +label, rationale, model, cost_usd
    +reviewer str
    +as_feedback(event, threshold) Feedback
  }
  class RunEvent {
    <<frozen, closed>>
    +event_id, type: RunEventType
    +tenant_id, run_id, thread_id
    +sequence, attempt, timestamp
    +step, message_id, tool_call_id
    +outcome: RunOutcome, error, data
    +of() started() finished() text() tool() interrupt() custom()$
  }
  class ScheduleSpec {
    <<frozen, closed>>
    +tenant_id, agent_id, name, cadence
    +timezone, on_behalf_of, input
    +workspace_id, enabled, metadata
    +timeout_seconds, agent_version
    +priority, concurrency_key
  }
  class Schedule {
    <<frozen, ignores unknown>>
    +schedule_id, created_by
    +next_fire_at, last_fired_at, last_run_id
    +consecutive_failures, last_error, retry_after
    +from_spec(spec, **fields)$ Schedule
  }

  RunStart <|-- RunRecord
  ScheduleSpec <|-- Schedule
  RunRecord --> Interrupt : awaiting
  RunRecord --> InterruptResolution : last_resolution
  Interrupt --> Option : options
  InterruptResolution ..> Interrupt : resolves()
  InterruptResolution ..> Feedback : to_feedback()
  JudgeVerdict ..> Feedback : as_feedback()
  RunEvent ..> Interrupt : data["interrupt"]
```

`Interrupt` validation: the question is not blank; an `APPROVAL` carries `tool_call`, a
`CHOICE` carries `options`, a `REVIEW` carries `expects`; `escalate_to` needs a `deadline`;
option values are distinct and not blank; `multiple` needs `options` or `expects`; `props`
needs a `component` (a component needs no `expects`).
`InterruptResolution` validation: an `EDIT` carries the edited arguments in `payload`; only
an `APPROVE` may be remembered for the run (`remember="run"`).
`Feedback` validation: `target_id` is not blank, a `CORRECT` or `EDIT` verdict carries a
`correction`, and `score` is a number in [0, 1] (strict, so `True` is not read as `1.0`).

`to_feedback` returns a record for `APPROVE`, `REJECT` and `EDIT` of a tool call, and `None`
for `ANSWER`, `CANCEL` or an interrupt without a tool call. It raises `ValueError` when the
resolution, interrupt and context do not name the same run and tenant.

### Agent identity and the A2A card

```mermaid
classDiagram
  direction LR
  class AgentDescriptor {
    <<frozen, open>>
    +agent_id, version, description
    +agent_group_id, framework, framework_version, harness_version
    +skills: SkillDescriptor[]
    +skill_ids list
    +build(agent_id, skills, **fields)$ AgentDescriptor
  }
  class SkillDescriptor {
    <<frozen, open>>
    +skill_id, version, description
    +input_schema, output_schema, tags
  }
  class AgentCard {
    <<frozen, ignores unknown, camelCase>>
    +name, description, url, version
    +protocol_version = "1.0"
    +provider, capabilities, skills
    +additional_interfaces, security_schemes, security
    +metadata (never published)
    +from_descriptor(descriptor, url, ...)$ AgentCard
    +to_a2a() dict
    +from_a2a(card)$ AgentCard
    +skill(skill_id) AgentSkill
  }
  class AgentSkill {
    +id, name, description, tags, examples
    +input_modes, output_modes, security
    +from_descriptor(skill)$ AgentSkill
  }
  AgentDescriptor --> SkillDescriptor
  AgentCard --> AgentSkill
  AgentCard --> AgentCapabilities
  AgentCard --> AgentProvider
  AgentCard --> AgentInterface
  AgentDescriptor ..> AgentCard : from_descriptor()
  SkillDescriptor ..> AgentSkill : from_descriptor()
```

Every URL on a card (`url`, `documentation_url`, `icon_url`, the provider's and each
interface's) must be http(s) with a host.

### Errors

```mermaid
classDiagram
  class Exception
  class AgentPaused {
    +question, expects, payload
    +awaiting() dict
  }
  class HarnessError {
    +code, category, retryable, source
    +message, details
    +to_error(trace_id, source) AgentError
  }
  Exception <|-- AgentPaused
  Exception <|-- HarnessError
  HarnessError <|-- ConfigurationError
  HarnessError <|-- AgentTimeoutError
  HarnessError <|-- AgentCancelledError
  HarnessError <|-- PolicyDeniedError
  HarnessError <|-- MemoryUnavailableError
  HarnessError <|-- ModelError
  HarnessError <|-- ToolError
  HarnessError <|-- ResultValidationError
  ToolError <|-- ToolNotFoundError
  HarnessError ..> AgentError : to_error()
```

`AgentPaused` is deliberately not a `HarnessError`, because a pause is not a failure.
`AgentError.of(exc)` normalises any exception. A `HarnessError` keeps its own
classification, and anything else is sorted by `classify()`:

```mermaid
flowchart TD
  X[exception] --> C{CancelledError?}
  C -- yes --> CAN[CANCELLED]
  C -- no --> T{"builtin TimeoutError?<br/>(asyncio's is the same class)"}
  T -- yes --> TIM[TIMEOUT]
  T -- no --> V{ValueError, TypeError, KeyError?}
  V -- yes --> VAL[VALIDATION]
  V -- no --> P{PermissionError?}
  P -- yes --> AUTH[AUTHORIZATION]
  P -- no --> S{"a class in its MRO from trellis,<br/>bifrost_sdk or httpx with a known name?"}
  S -- yes --> MAP["that name's category, nearest class first<br/>(TimeoutError, TimeoutException: TIMEOUT)"]
  S -- no --> UNK[UNKNOWN]
```

Then whether it is retryable, first answer wins:

```mermaid
flowchart LR
  A{"retryable= passed<br/>to AgentError.of?"} -- yes --> USE[that value]
  A -- no --> O{"exception has a bool<br/>retryable attribute?"}
  O -- yes --> OWN["the exception's own<br/>(Memory Service SDK, bifrost-sdk)"]
  O -- no --> CAT{"category in<br/>RETRYABLE_CATEGORIES?"}
  CAT -- yes --> YES[retryable]
  CAT -- no --> NO[not retryable]
```

Only `TIMEOUT`, `RATE_LIMIT` and `DEPENDENCY` are retryable by category
(`errors.RETRYABLE_CATEGORIES`).

`AgentError.source` is an `ErrorSource`: the component that reported the failure, never the
exception class (that is `code`). Every value but `memory` is one a sibling repo passes today:

| Source | Set by |
| --- | --- |
| `function`, `langgraph`, `openai_agents`, `claude_agent_sdk`, `react` | the harness, for every failed run (the adapter's name) |
| `tools` | the harness's tool layer (a tool missing from the run, or called outside one) |
| `mcp` | the harness's Bifrost client, as `mcp.<tool>`: stored as `mcp`, with the tool in `details.source_detail` |
| `a2a` | the harness's A2A client (a remote agent failed or ended badly) |
| `memory` | nobody yet: kept for a Memory Service failure (`MemoryUnavailableError`) |
| `agent-runs` | agent-runs: a lease that lapsed on every attempt, an interrupt nobody answered, a schedule that could not fire |

## Wire conventions

* **Written records refuse unknown fields**, so a producer's typo is an error: `RunStart`,
  `RunEvent`, `Interrupt`, `InterruptResolution`, `Feedback`, `JudgeVerdict`, `ScheduleSpec`
  and `AgentExecutionContext`.
* **Records read back ignore unknown fields**, so a newer peer does not break an older reader:
  `RunRecord` and `Schedule` (from a store) and `AgentCard` with its parts (from another
  agent).
* **Every timestamp on a run, interrupt, event, feedback or schedule is timezone-aware**
  (`AwareDatetime`), and `ids.now()` is UTC.
* **Every field is described.** Each model field carries a one-line `Field(description=...)`
  with its unit, format and allowed values, so the JSON Schema (and agent-runs' OpenAPI
  document) documents every property. `tests/test_field_docs.py` keeps it that way.
* **Closed vocabularies are typed.** Statuses, reasons, decisions, kinds and verdicts are
  `StrEnum`s. `ErrorSource`, `ToolSource`, `ObservationKind` and `InterruptUI` are `Literal`s,
  so a caller's string literal still type-checks. The fields left as `str` on purpose
  (`ToolSpec.side_effects`, `EvidenceRef.source_type`, the A2A transports, ...) are listed in
  ADR 0003 with the reason for each.
* **Payloads that leave the process are unredacted** (`RunEvent.data`,
  `Interrupt.awaiting()`). The surface that sends them out passes them through a
  `TelemetryRedactor`.
* **Ids carry a prefix**: `run_`, `req_`, `int_`, `evt_`, `fb_`, `sch_`.
