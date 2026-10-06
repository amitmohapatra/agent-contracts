# trellis-contracts

The types an agent must accept and return. No runtime, no transport, no I/O.

`trellis-contracts` (imported as `trellis.contracts`) is the vocabulary the five Trellis repos
share. **Records** travel between processes: a run, a pause, its answer, a tool call, an event,
a judgement, an agent card. **Ports** are the outbound dependencies, written as `Protocol`s,
so an adapter can be built against the seam. The only dependency is `pydantic`, so the
contract can be read, versioned and reasoned about without a gateway, a database or an agent
framework.

## Start here

1. **Install** it: `uv add trellis-contracts` ([Install](#install)).
2. **Read the [Quickstart](#quickstart)**: a context, a request, a response, a run record,
   and a run that pauses for a person.
3. **Run the examples**: `make examples` runs nine pure scripts, from the simplest record to
   the full pause-and-resume lifecycle ([examples/](examples/README.md)).
4. **Pick your types** with [Which type do I use?](#which-type-do-i-use), and see how each
   block takes them in [Each group, in each way](#each-group-in-each-way).
5. **Go deeper** in the [documentation](#documentation): the architecture and its diagrams,
   every export, versions and the ADRs.

## Where this fits: two ways to use Trellis

Trellis is five repos: **agent-harness** runs your agent, **agent-runs** keeps runs durable
(the inbox, schedules, workers, webhooks), **agent-memory-service** gives agents memory,
**bifrost-sdk** reaches models and MCP tools through the Bifrost gateway, and **this package**
is the types they agree on. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#in-the-platform)
draws how they connect.

Each block works in both ways of using Trellis:

- **Way 1, wrapped.** `from trellis import Harness; h = Harness(); agent = h.wrap(my_agent)`.
  The harness runs your agent (LangGraph, Deep Agents, OpenAI Agents SDK, Claude Agent SDK,
  a plain function) and builds every record for you.
- **Way 2, pluggable blocks.** Keep your framework, import only the blocks you want
  (`trellis.runs`, `trellis.memory`, `bifrost_sdk`, and from the harness repo
  `trellis.harness.governance`, `trellis.harness.evals`, `trellis.harness.a2a.remote`), and
  hand them these types yourself.

| | What happens with `trellis.contracts` |
|---|---|
| **Way 1, wrapped** | The harness builds and reads every record: each run is a `RunStart` in agent-runs, a pause an `Interrupt`, its answer an `InterruptResolution`, an approval the `Feedback` memory learns from. You write no contracts code. You meet the types in what the harness returns (`Result.interrupt`, the `RunEvent`s of `agent.stream()`, the `RunRecord` of `handle.status()`), and you answer a pause with `agent.resume(interrupt_id, decision)`. The one field you set yourself is a tool's `side_effects` (`tool(fn, side_effects=...)`), which governance reads. |
| **Way 2, pluggable** | You build the records and hand them to the blocks you imported, which take and return exactly these types: |

```python
from trellis.contracts import (
    AgentExecutionContext,
    AgentPaused,
    AgentRequest,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    RunStart,
    ToolCall,
)
from trellis.memory import MemoryClient
from trellis.runs import RunsClient

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="buyer")
async with RunsClient() as runs:  # RUNS_URL and TRELLIS_API_KEY
    run = await runs.start(RunStart.from_request(AgentRequest.create(ctx, {"sku": "A-1"})))
    asked = Interrupt.from_paused(
        AgentPaused("Order 12 of A-1?"),
        context=ctx,
        reason=InterruptReason.APPROVAL,
        tool_call=ToolCall(tool="erp.order", args={"sku": "A-1", "qty": 12}),
    )
    await runs.pause(asked, checkpoint={"step": 2})  # the run waits for a person
    answer = InterruptResolution(
        interrupt_id=asked.interrupt_id, run_id=run.run_id, decision=InterruptDecision.APPROVE
    )
    await runs.resume(answer, tenant="acme")  # a RunRecord, RUNNING again

memory = MemoryClient().bind(**ctx.scope_fields())  # the context is the memory scope
await memory.feedback(answer.to_feedback(asked, ctx))  # what memory learns approvals from
```

- **Choose Way 1 when** you want the records built, stored and answered for you: the harness
  keeps run, pause, answer and feedback consistent with each other.
- **Choose Way 2 when** your framework runs the agent and you call agent-runs, the memory
  service or governance yourself: build these types once and pass them as they are.
  [Each group, in each way](#each-group-in-each-way) says which block takes which type.

**Why one set of types.** One `Interrupt` is the pause in agent-runs, the inbox entry a
reviewer reads (`RunSummary.awaiting`) and, with its `InterruptResolution`, the `Feedback` the
memory service learns approval rules from, with no translation between them. One
`AgentExecutionContext` is the run, the memory scope and the feedback's attribution. A run your
LangGraph code started and a run a wrapped agent started are the same `RunRecord`: one inbox,
one webhook receiver, one resolution format. agent-runs' OpenAPI document is built from these
models, so a record that changes is a new version the pins must admit
([docs/versioning.md](docs/versioning.md)).

Harness docs: [the two ways](https://github.com/amitmohapatra/agent-harness/blob/main/README.md#two-ways-to-use-trellis) ·
[every page](https://github.com/amitmohapatra/agent-harness/blob/main/docs/README.md) ·
blocks: [memory](https://github.com/amitmohapatra/agent-harness/blob/main/docs/blocks/memory.md),
[runs](https://github.com/amitmohapatra/agent-harness/blob/main/docs/blocks/runs.md),
[governance](https://github.com/amitmohapatra/agent-harness/blob/main/docs/blocks/governance.md),
[evaluation](https://github.com/amitmohapatra/agent-harness/blob/main/docs/blocks/evaluation.md),
[A2A](https://github.com/amitmohapatra/agent-harness/blob/main/docs/blocks/a2a.md).

## Install

```bash
uv add trellis-contracts
# or, from a sibling checkout, the way agent-harness and agent-runs do it:
uv add --editable ../agent-contracts
```

It needs Python 3.12 or newer and `pydantic>=2.13,<3`. It has no settings and reads no
environment variable ([docs/configuration.md](docs/configuration.md)).

## Quickstart

An application creates the context once per execution. An agent takes a request and returns a
response:

```python
from trellis.contracts import (
    AgentExecutionContext,
    AgentRequest,
    AgentResponse,
    Claim,
    EvidenceRef,
    RunRecord,
    RunStart,
    RunStatus,
)

ctx = AgentExecutionContext.create(
    tenant_id="acme",
    user_id="u1",
    agent_id="analyst",
    thread_id="chat-42",
    turn_id="t3",
    timeout_seconds=30,
)
request = AgentRequest.create(ctx, "Why did revenue fall?")

response = AgentResponse.ok(
    "Revenue fell 4% on lower renewals.",
    claims=[Claim(claim_id="c1", text="Revenue fell 4%", evidence_ids=["chunk_1"])],
    evidence=[EvidenceRef(source_id="chunk_1", citation="Q3 report, p. 4")],
)
assert response.succeeded

# The Memory Service's Scope keywords, made coherent: a turn with no session gets one.
assert ctx.scope_fields()["session_id"] == "chat-42-session"

# What agent-runs keeps, and the one transition check.
record = RunRecord.from_start(RunStart.from_request(request))
assert record.status is RunStatus.RUNNING and record.status.can_become(RunStatus.PAUSED)
```

A run that pauses to ask a person, streams that it did, and gets an answer:

```python
from trellis.contracts import (
    AgentExecutionContext,
    AgentPaused,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    Option,
    RunEvent,
    RunOutcome,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="buyer")
interrupt = Interrupt.from_paused(
    AgentPaused("Which supplier?"),
    context=ctx,
    reason=InterruptReason.CHOICE,
    ui="choice",
    options=["Acme", Option(value="globex", label="Globex GmbH", description="EU stock")],
    assignee="role:procurement",
)
event = RunEvent.finished(ctx, RunOutcome.INTERRUPT, sequence=7, interrupt=interrupt)
answer = InterruptResolution(
    interrupt_id=interrupt.interrupt_id,
    run_id=interrupt.run_id,
    decision=InterruptDecision.ANSWER,
    answer="globex",  # an option's value, never its label
    comment="Acme is out of stock",
)
assert answer.resolves(interrupt)
assert interrupt.option_values == ["Acme", "globex"]
assert event.data["interrupt"]["options"][0] == "Acme"  # a plain string stays one
```

Both blocks run as they are. So do the [examples](examples/README.md): `make examples`.

## Each group, in each way

What each exported group is, when you touch it wrapped (mostly never: the harness builds the
records), and which block takes or returns it when you plug blocks in yourself. "No block"
means no sibling package takes that type today; it is there for code of your own.

| Group | What it is | Way 1, wrapped | Way 2, pluggable |
| --- | --- | --- | --- |
| `context` | `AgentExecutionContext`: who, for whom, in which thread and run, until when | never: one is built per run | build one per execution; `MemoryClient().bind(**ctx.scope_fields())` is its memory scope, `RunStart.from_request(AgentRequest.create(ctx, input))` its run, and `to_feedback(interrupt, ctx)` attributes a decision to it |
| `messages` | `AgentRequest`, `AgentResponse`, `AgentStatus` | never: `agent.run` takes your input and returns the harness's `Result` | `AgentRequest` feeds `RunStart.from_request`; `AgentResponse` is a result shape for your own agents (no block takes it) |
| `artifacts` | `ArtifactRef`, `EvidenceRef`, `Claim`, `MemoryObservation`, ... | never: an `ask` table or diff is uploaded for you and becomes the interrupt's `payload_ref` | `RunsClient.artifacts.upload(...)` returns the `ArtifactRef` you put on `Interrupt.payload_ref`; the rest are fields of `AgentResponse` (no block reads them) |
| `tool` | `ToolSpec`, `ToolCall`, `ToolOutcome`, `ToolStatus`, `ToolSource` | only `side_effects`: `tool(fn, side_effects="irreversible")` becomes `ToolSpec.side_effects`, which governance reads; an approval's `Result.interrupt.tool_call` is a `ToolCall` | `Governance.check(spec.name, args, side_effects=spec.side_effects)` and `Governance.publish(specs)` read `ToolSpec`s; `remote(url, ...).spec` is one; an `APPROVAL` `Interrupt` carries the `ToolCall` |
| `model` | `ModelRequest`, `ModelResponse`, `ModelUsage` | never: models go through Bifrost | no block: `bifrost_sdk` is the model client; these are the `ModelClient` port's shapes, for an adapter of your own |
| `errors` | `AgentError`, `AgentPaused`, the `HarnessError` family, `classify` | you read `AgentError` on `Result.error` and `RunRecord.error`; you raise nothing | `RunsClient.finish(run_id, RunStatus.ERROR, error=AgentError.of(exc, source=...))`; `classify(exc)` sorts memory, Bifrost and `httpx` failures |
| `runs` | `RunStart`, `RunRecord`, `RunStatus`, `RunEvent`, `Interrupt`, `InterruptResolution`, `ScheduleSpec`, `Schedule`, ... | you read them: `agent.stream()` yields `RunEvent`s, `Result.interrupt` is an `Interrupt`, `handle.status()` a `RunRecord`, `agent.schedule(...)` a `Schedule`; you answer with `agent.resume(interrupt_id, decision)` (an `InterruptDecision` or its name), which becomes the `InterruptResolution` | `RunsClient.start(RunStart)` → `RunRecord`; `pause(Interrupt, checkpoint=)`, `resume(InterruptResolution)` and `finish(...)` → `RunRecord`; `list(status=RunStatus.PAUSED, assignee=...)` is the inbox; `schedules.create(ScheduleSpec)` → `Schedule`; a `Worker`'s `Job.record` is a `RunRecord`. `RunEvent` is yours to stream (no block takes it) |
| `feedback`, `evaluation` | `Feedback`, `FeedbackVerdict`, `JudgeVerdict`, `AgentEvalEvent` | never: each approve, reject or edit goes to memory as `InterruptResolution.to_feedback(...)`; `h.feedback(run_id, verdict)` takes a `FeedbackVerdict` or its name | the memory SDK's `feedback(record)` sends a `Feedback` as it is (`to_feedback(...)`, `Feedback.for_context(...)`, `JudgeVerdict.as_feedback(...)`); `Governance.decided(...)` builds the one for a tool call. `trellis.harness.evals` scores with its own `EvalScore`, not `JudgeVerdict` |
| `descriptors`, `a2a` | `AgentDescriptor`, `SkillDescriptor`, `AgentCard` and its parts | never: `serve_a2a` publishes the card with `a2a-sdk` | build or read a card in A2A code of your own (`AgentCard.from_descriptor(...).to_a2a()`, `AgentCard.from_a2a(json)`); `trellis.harness.a2a.remote` reads cards with `a2a-sdk` instead (no block takes these) |
| `ids` | `new_id`, `stable_id`, `safe_id`, `now` | never | `stable_id(...)` or `ctx.idempotency_key(...)` for a key a retry repeats (`RunStart.idempotency_key`, `ToolCall.idempotency_key`) |
| ports | the 12 `Protocol`s in `trellis.contracts.ports` | never; the harness's `Redactor` is a `TelemetryRedactor` | only when you write an adapter of your own; no block requires one |

## Which type do I use?

The task-by-task view of [the table above](#each-group-in-each-way). Wrapped, the harness
does these for you; the rows are what you write when you plug the blocks in yourself.

| When you want to... | Use |
| --- | --- |
| start an execution from an application | `AgentExecutionContext.create(...)`, then `AgentRequest.create(ctx, input)` |
| call a nested agent | `ctx.for_agent("worker")`: it keeps the tenant, user, thread and trace, makes this run the parent, and never extends the deadline |
| return a result from an agent | `AgentResponse.ok(data)`, `AgentResponse.failed(error)`, or `AgentResponse.coerce(anything)` to wrap an existing return value |
| say where an answer came from | `Claim` with `evidence_ids`, plus `EvidenceRef`s on `AgentResponse.evidence` |
| return something too large for the result | `RunsClient.artifacts.upload(run_id, data)` for a run's payload, which returns an `ArtifactRef`; `ArtifactClient.put(...)` is the port for a store of your own |
| say what an agent wants remembered, in its result | `MemoryObservation` on `AgentResponse.memory_observations`, whose `kind` must be one of `OBSERVATION_KINDS` (the `ObservationKind` literal). No block reads it today: to write to the Memory Service, call `trellis.memory` (`remember`, `history.add`) |
| propose a next step instead of calling another agent | `RecommendedAction` |
| report a non-fatal problem | `response.add_warning(code, message, **details)`, which adds an `AgentWarning` |
| fail in a way callers can branch on | raise a `HarnessError` subclass; turn any exception into an `AgentError` with `AgentError.of(exc, source=...)`, where `source` is an `ErrorSource` |
| ask a person something in the middle of a run | wrapped, `await trellis.current().ask(question, ...)`, and the harness builds the `Interrupt`; in your own code, `Interrupt.from_paused(AgentPaused(question), context=ctx, ...)`, then `RunsClient.pause(interrupt, checkpoint=...)` |
| get a tool call approved before it runs | `Interrupt(reason=InterruptReason.APPROVAL, tool_call=ToolCall(...))` |
| record how a person answered | `InterruptResolution`, sent with `RunsClient.resume(resolution)`; for an approve, reject or edit of a tool call, `resolution.to_feedback(interrupt, ctx)` gives the matching `Feedback`, which the memory SDK's `feedback(record)` sends as it is |
| record a judgement on a run, answer, memory, tool call, brief or procedure | `Feedback.for_context(ctx, target_kind=..., target_id=..., verdict=...)` |
| score a finished run automatically | `trellis.harness.evals` (`judge`, `evaluate`) with its own evaluators; for a judge of your own, the `Judge` port returns a `JudgeVerdict`, and `verdict.as_feedback(event)` turns it into judge `Feedback` on the `AgentEvalEvent`'s answer |
| keep a run beyond the process, or queue it for a worker | `RunStart.from_request(request)`, then `RunsClient.start(start)` or `RunsClient.start(start, queue=True)` from `trellis.runs` (the agent-runs SDK), which return a `RunRecord` |
| check whether a status change is allowed | `RunStatus.can_become(target)` |
| stream progress to a UI | wrapped, `agent.stream()` yields the `RunEvent`s; in your own code, the `RunEvent` constructors (`started`, `text`, `tool`, `interrupt`, `custom`, `finished`), sent with `EventSink.publish` |
| run an agent on a schedule | `ScheduleSpec` is what a caller writes (`RunsClient.schedules.create(spec)`); `Schedule` is what agent-runs returns |
| publish an agent to other agents from A2A code of your own | `AgentDescriptor.build(...)`, then `AgentCard.from_descriptor(descriptor, url=...)`, then `card.to_a2a()` (a wrapped agent's `serve_a2a` publishes its card itself) |
| read another agent's card | `AgentCard.from_a2a(json)`, which drops unknown keys and refuses non-http(s) URLs |
| describe, call and report tools | `ToolSpec` (`descriptor()` is the Memory Service shape; `side_effects` is what governance decides from), `ToolCall`, `ToolOutcome` with its `ToolStatus` |
| call a model provider-neutrally | `bifrost_sdk` is the platform's model client; `ModelRequest`, a `ModelClient`, `ModelResponse.coerce(raw)` and `ModelUsage.extract(raw)` (token counts) are for an adapter of your own |
| scope a Memory Service call | `MemoryClient().bind(**ctx.scope_fields())` |
| make a write idempotent across retries | `ctx.idempotency_key(*parts)`, or `stable_id(*parts)` |
| log with the run's identifiers | `ctx.log_fields()` |

## Documentation

| Page | What it answers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it fits among the five repos, the modules, the ports, the run lifecycle, which record becomes which across a run (sequence diagram), the records as class diagrams, the wire conventions |
| [docs/api.md](docs/api.md) | Every exported name and every port, with what it is |
| [examples/](examples/README.md) | Nine runnable scripts, simplest first |
| [docs/configuration.md](docs/configuration.md) | There are no settings; the defaults the records apply |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Validation messages, their causes and fixes; FAQ |
| [docs/versioning.md](docs/versioning.md) | Which versions of the five repos go together, and the version rules |
| [CHANGELOG.md](CHANGELOG.md) | What each version changed |
| [docs/adr/](docs/adr/README.md) | Why: one record per decision, 0001 to 0007 |

## Development

```bash
make sync          # uv sync --all-extras --locked
make check         # lint, format-check, test, examples, links: what CI runs
```

The targets one by one are `lint` (`ruff check`), `format-check` (`ruff format --check`),
`test` (`pytest`), `examples` and `links` (every relative Markdown link and anchor resolves).
CI (`.github/workflows/ci.yml`) runs them on every push to `main` and every pull request. One
test fails the build if the package imports anything beyond `pydantic` and the standard
library. There is no type checker: the tests deliberately pass wrong types to prove the models
refuse them.
