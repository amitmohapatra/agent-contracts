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
| `events` | `LifecycleEvent`, `AgentEvalEvent` |
| `descriptors` | `AgentDescriptor`, `SkillDescriptor` |
| `runs` | `RunStart`, `RunRecord`, `RunStatus`, the `RunEvent` stream, `Interrupt` and `InterruptResolution`, `ScheduleSpec` and `Schedule` |
| `feedback` | `Feedback` with its target kinds, verdicts and sources |
| `evaluation` | `JudgeVerdict`, `JudgeMethod` |
| `a2a` | `AgentCard` and its parts, mapped from `AgentDescriptor` |
| `ports` | Every outbound dependency as a Protocol: model, tool, artifact, memory, telemetry, evaluation, prompt, policy, registry, event sink, run store, scheduler, feedback store, judge, agent directory, interceptor, framework adapter |
| `ids` | `new_id`, `stable_id`, `safe_id`, `now` |

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

## Contracts v2

Beyond the request/response pair, the package carries the seams the rest of the platform
builds on (ADR 0001 in `docs/adr`): `RunEvent` (the AG-UI event vocabulary plus
`CONTEXT_LOADED` and `INTERRUPT`), `Interrupt` and `InterruptResolution` (one pause
mechanism for every framework; approve, reject or edit of a tool call is also `Feedback`),
`Feedback` (human, judge and interrupt judgements on runs, answers, memories, tool calls,
briefs and procedures), `JudgeVerdict`, `AgentCard` (the A2A 0.3.0 card mapped from
`AgentDescriptor`), `RunRecord`, `RunStart`, `ScheduleSpec` and `Schedule`, and the ports
`EventSink`, `RunStore`, `Scheduler`, `FeedbackStore`, `Judge` and `AgentDirectory`.

```python
from trellis.contracts import (
    AgentExecutionContext, AgentPaused, Interrupt, InterruptDecision, InterruptResolution,
    RunEvent, RunOutcome,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="refund-agent")
interrupt = Interrupt.from_paused(
    AgentPaused("Approve the refund?", expects={"type": "boolean"}), context=ctx
)
event = RunEvent.finished(ctx, RunOutcome.INTERRUPT, sequence=7, interrupt=interrupt)
answer = InterruptResolution(
    interrupt_id=interrupt.interrupt_id,
    run_id=interrupt.run_id,
    decision=InterruptDecision.ANSWER,
    answer=True,
)
```

Records the platform writes and streams (`RunStart`, `RunEvent`, `Interrupt`,
`InterruptResolution`, `Feedback`, `JudgeVerdict`, `ScheduleSpec`) refuse unknown fields;
records read back from a store (`RunRecord`, `Schedule`) and cards read from another agent
(`AgentCard`) ignore them. Every timestamp is timezone-aware.
