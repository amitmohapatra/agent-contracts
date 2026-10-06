# API reference

Every public name of `trellis.contracts`, what it is, and the ports. There is no HTTP API and
no OpenAPI document in this repo: the package is types only. The HTTP API built from these
types is agent-runs' ([its OpenAPI document](https://github.com/amitmohapatra/agent-runs/blob/main/docs/openapi.json),
[its wire reference](https://github.com/amitmohapatra/agent-runs/blob/main/docs/api.md)).

Each field's description, unit and allowed values are in the code (`Field(description=...)`)
and in the JSON Schema of each model (`RunStart.model_json_schema()`). Which type to use for
which job: [Which type do I use?](../README.md#which-type-do-i-use). How the types fit
together: [ARCHITECTURE.md](ARCHITECTURE.md).

## Everything it exports

Every name below is in `trellis.contracts.__all__` and importable from `trellis.contracts`.

### `context`

| Name | What it is |
| --- | --- |
| `AgentExecutionContext` | The frozen identity and lineage of one execution: tenant, user, thread, agent, run lineage, correlation ids and an absolute deadline. Build it with `create`, derive a child with `for_agent`, and use `scope_fields()`, `idempotency_key()` and `log_fields()` |

### `messages`

| Name | What it is |
| --- | --- |
| `AgentRequest` | What an agent was asked to do: objective, input, context, requested skills, constraints and references. `query` is the text a memory retrieval should use |
| `AgentResponse` | What an agent produced: status, data, claims, evidence, artifacts, memory observations, recommended actions, warnings, metrics and error |
| `AgentStatus` | How an execution ended (`SUCCESS`, `PARTIAL`, `ERROR`, `TIMEOUT`, `CANCELLED`, `REJECTED`), or `PAUSED`. `ok` is true for `SUCCESS` and `PARTIAL` |

### `artifacts`

| Name | What it is |
| --- | --- |
| `ArtifactRef` | A pointer to content stored outside the result |
| `EvidenceRef` | Where a claim came from, in the Memory Service's evidence shape |
| `Claim` | One assertion an agent made, with the ids of its evidence |
| `RecommendedAction` | A next step the agent proposes, with a short rationale |
| `MemoryObservation` | Something the agent wants remembered. Its `kind` is checked against `OBSERVATION_KINDS` |
| `ObservationKind` | The `kind` literal: `MESSAGE`, `FILE`, `AGENT_RESULT`, `TOOL_RESULT`, `DECISION`, `FEEDBACK`, `EVENT`, `IMPORT` |
| `AgentWarning` | A non-fatal problem the caller should see |
| `OBSERVATION_KINDS` | The observation kinds the Memory Service accepts |

### `tool`

| Name | What it is |
| --- | --- |
| `ToolSpec` | What a tool is: name, schemas, source or server, idempotency, side effects and authorization metadata |
| `ToolCall` | One call: the tool, its arguments, and an optional idempotency key |
| `ToolOutcome` | The normalised result of a call. Assignments are validated |
| `ToolStatus` | `ok`, `error`, `timeout`, `rejected`, `cancelled`. It is a `StrEnum`, so `outcome.status == "ok"` holds |
| `ToolSource` | Where a tool comes from, `ToolSpec.source`: `local`, `mcp`, `memory`, `openapi`, `a2a` |

### `model`

| Name | What it is |
| --- | --- |
| `ModelRequest` | One model invocation: model, provider, prompt or messages, params and tools |
| `ModelResponse` | The normalised result. `raw` keeps the provider object, and `coerce` wraps any provider response |
| `ModelUsage` | Token and cost accounting. `extract` reads the common provider spellings and never invents counts |

### `errors`

| Name | What it is |
| --- | --- |
| `AgentError` | A normalised, serialisable failure with `category`, `retryable` and `source`. `AgentError.of(exc)` builds one from any exception, keeping the exception's own `retryable` when it has one |
| `ErrorCategory` | The closed set of failure categories |
| `ErrorSource` | The components that report failures, `AgentError.source`: `agent-runs`, `tools`, `mcp`, `a2a`, `memory` and the harness's adapters (`function`, `langgraph`, `openai_agents`, `claude_agent_sdk`, `react`). A qualified `mcp.<tool>` is stored as `mcp` with the tool in `details.source_detail` |
| `ERROR_SOURCES` | `ErrorSource` as a set |
| `classify` | Best-effort category for any exception, including Memory Service SDK, bifrost-sdk and `httpx` errors matched by class name (nearest class first). Every timeout is `TIMEOUT` |
| `AgentPaused` | Raised by an agent to suspend the run and ask a person something. It is not an error |
| `HarnessError` | Base of the failures a harness raises. Each subclass carries its own `code`, `category` and `retryable` |
| `ConfigurationError` | A misconfiguration (`VALIDATION`) |
| `AgentTimeoutError` | The execution ran out of time (`TIMEOUT`, retryable) |
| `AgentCancelledError` | The execution was cancelled (`CANCELLED`) |
| `PolicyDeniedError` | A policy refused the execution, a tool or a model (`POLICY`) |
| `MemoryUnavailableError` | The Memory Service could not be reached (`MEMORY`, retryable) |
| `ModelError` | A model call failed (`MODEL`) |
| `ToolError` | A tool call failed (`TOOL`) |
| `ToolNotFoundError` | A `ToolError` for a tool that does not exist (`VALIDATION`) |
| `ResultValidationError` | An agent's result failed validation (`VALIDATION`) |

`trellis.contracts.errors` also has `is_pause_signal(exc)`, which recognises `AgentPaused`
and LangGraph's suspend signals by class name, and `RETRYABLE_CATEGORIES`.

### `runs`

| Name | What it is |
| --- | --- |
| `RunStatus` | Where a run is: `QUEUED`, `RUNNING`, `PAUSED` or a final status spelled like `AgentStatus`. `can_become` is the state machine and `final` says whether it has ended |
| `RunStart` | What starting a run records. It is agent-runs' create body, and `from_request` builds it from an `AgentRequest`. Optional: `deadline`, `timeout_seconds` (working time), `agent_version`, and how a queued run waits its turn: `priority` (higher first) and `concurrency_key` (runs sharing it run a few at a time) |
| `RunRecord` | A run as a store keeps it: the start plus status, output, error, the interrupt it waits on, the last resolution, an opaque `checkpoint` and the attempt |
| `RunEvent` | One event of a run's stream, in the AG-UI vocabulary, ordered by `sequence` within an `attempt` |
| `RunEventType` | The event vocabulary: AG-UI's names plus `CONTEXT_LOADED` and `INTERRUPT` |
| `RunOutcome` | How a run finished, as `RUN_FINISHED` reports it. `from_status` maps a settled status to it |
| `Interrupt` | The record of a paused run: the question, the expected answer shape (`expects`) and its widget hints (`ui_schema`), the UI hint (`ui`), options (plain strings or `Option`s) and whether several may be picked (`multiple`), the asker's own screen (`component`, `props`), payload, the tool call under approval, the assignee, the deadline and the escalation |
| `Option` | One choice an interrupt offers: the `value` an answer carries, and the `label` and `description` a person sees |
| `InterruptReason` | `QUESTION`, `APPROVAL`, `REVIEW`, `CHOICE`, `AUTH` |
| `InterruptUI` | The control a surface renders, `Interrupt.ui`: `approve`, `form`, `table`, `diff`, `choice` |
| `InterruptResolution` | How an interrupt was answered, with the reviewer's `comment` and how far an approval reaches (`remember`: `once`, or the rest of the `run`). `resolves` checks it answers that interrupt, and `to_feedback` gives the feedback for a tool-call decision |
| `InterruptRemember` | `InterruptResolution.remember`: `once` or `run` |
| `InterruptDecision` | `ANSWER`, `APPROVE`, `REJECT`, `EDIT`, `CANCEL` |
| `ScheduleSpec` | A standing intent: which agent, what input, which cadence and timezone, on whose behalf; `timeout_seconds`, `agent_version`, `priority` and `concurrency_key` are copied into every fired run, and `metadata` into its metadata under the fire's own keys |
| `Schedule` | A schedule as agent-runs keeps it: the spec plus its id, author and the history of its fires |

### `feedback` and `evaluation`

| Name | What it is |
| --- | --- |
| `Feedback` | One judgement about one target. `for_context` binds it to the identity of the request it was given in |
| `FeedbackTargetKind` | `run`, `answer`, `memory`, `tool_call`, `brief`, `procedure` |
| `FeedbackVerdict` | `confirm`, `reject`, `correct`, `approve`, `edit`. `correct` and `edit` need a `correction` |
| `FeedbackSource` | `human`, `judge`, `interrupt` |
| `JudgeVerdict` | A judge's score in [0, 1] with its method, model and cost. `as_feedback` turns it into a `Feedback` |
| `JudgeMethod` | `grounded` (deterministic citation and claim checks) or `llm` |
| `AgentEvalEvent` (from `events`) | What a judge scores a finished run from. It carries references, not payloads |

### `descriptors` and `a2a`

| Name | What it is |
| --- | --- |
| `AgentDescriptor` | An agent's identity and skills. `build` accepts plain skill ids |
| `SkillDescriptor` | One skill: id, version, description, schemas and tags |
| `AgentCard` | The A2A 1.0 Agent Card. `from_descriptor` maps a descriptor, `to_a2a` writes camelCase JSON without the platform's `metadata`, and `from_a2a` reads a foreign card as data |
| `AgentSkill` | A skill on the card |
| `AgentCapabilities` | Streaming, push notifications, state history and extensions |
| `AgentProvider` | The organisation behind the agent, with an http(s) URL |
| `AgentInterface` | Another URL and transport the same agent answers on |
| `A2A_PROTOCOL_VERSION` | `"1.0"`, the protocol `a2a-sdk` 1.x speaks |

### `ids`

| Name | What it is |
| --- | --- |
| `new_id(prefix)` | A fresh opaque id, such as `run_<hex>` |
| `stable_id(*parts, prefix, size)` | A deterministic id derived from its parts, so a retry produces the same id |
| `safe_id(value, max_len)` | Any value coerced into the id alphabet shared with the Memory Service |
| `now()` | The platform clock: timezone-aware UTC |

## The ports

Each port is a `runtime_checkable` `Protocol` in `trellis.contracts.ports`, so an
implementation conforms by shape and imports no base class from here.

| Port | Methods | Use it when you need to |
| --- | --- | --- |
| `ModelClient` | `invoke`, `structured`, `stream` | call a model provider without naming the provider |
| `ToolClient` | `list_tools`, `call` | run tools from local callables, an MCP server or a gateway |
| `ArtifactClient` | `put`, `get` | keep large payloads out of results and graph state |
| `MemoryPort` | `enabled`, `retrieve`, `observe`, `record_input`, `record_output`, `describe` | read from and write to the Memory Service (or switch memory off) |
| `TelemetryProvider` | `start_span`, `record_event`, `record_metric`, `flush` | emit spans, events and metrics |
| `TelemetryRedactor` | `redact_attributes`, `redact_input`, `redact_output` | strip what may not leave the process before it reaches a backend, a UI or a webhook |
| `EvaluationProvider` | `score`, `submit_dataset_item` | write scores and dataset items to the tracing backend (feedback goes to the Memory Service instead) |
| `AgentPolicyProvider` | `authorize_execution`, `authorize_tool`, `authorize_model` | allow or refuse a run, a tool call or a model call |
| `EventSink` | `publish` | deliver a run's `RunEvent` stream: SSE, a webhook outbox, a test collector |
| `Judge` | `judge` | score a finished run off the critical path (`None` means it abstained) |
| `AgentDirectory` | `get`, `find`, `publish` | find agents another agent may call, as `AgentCard`s |
| `AgentInterceptor` | `name`, `order`, `before`, `after`, `on_error` | add an ordered stage to the execution pipeline |

`ports.Runtime` (the object an interceptor is handed) is deliberately `Any`, because the
concrete runtime belongs to whatever executes the agent.

Runs have no port. Their client is `trellis.runs.RunsClient` (pip `trellis-runs`, shipped
from agent-runs), whose verbs are agent-runs' operation ids: `start`, `claim`, `heartbeat`,
`pause`, `resume`, `finish`, `get`, `list`. Its requests and replies are the types here
(`RunStart`, `Interrupt`, `InterruptResolution`, `RunRecord`). Which sibling repo implements which
port today is in [docs/ARCHITECTURE.md](ARCHITECTURE.md#the-ports).
