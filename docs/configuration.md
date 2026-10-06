# Configuration

`trellis-contracts` has **no settings**. It reads no environment variable, no file and no
flag, and it has nothing to switch on or off: it is types only. Importing it changes nothing.

What can look like configuration is a record field with a default. These are the defaults
callers most often rely on, and whether you get them without asking.

| Field | Default | Automatic? | Example |
|---|---|---|---|
| `AgentExecutionContext.create(agent_run_id=)` | a fresh `run_` id | yes | `AgentExecutionContext.create(tenant_id="acme")` |
| `AgentExecutionContext.create(timeout_seconds=)` | no deadline | no: the deadline is now plus this many seconds | `create(tenant_id="acme", timeout_seconds=30)` |
| `RunStart.run_id` | the context's `agent_run_id` (`from_request`), else a fresh `run_` id | yes | `RunStart.from_request(request)` |
| `RunStart.idempotency_key` | derived from the context by `from_request` | yes, with `from_request` | `RunStart(..., idempotency_key="order-91")` |
| `RunStart.timeout_seconds` | `None`: no working-time limit of its own (agent-runs may apply a maximum) | no | `RunStart(..., timeout_seconds=600)` |
| `RunStart.priority` | `0` (range -1000 to 1000, higher is claimed first) | no | `RunStart(..., priority=10)` |
| `RunStart.concurrency_key` | `None`: no limit | no | `RunStart(..., concurrency_key="thread:chat-42")` |
| `Interrupt.ui` | `"approve"` | yes | `Interrupt.from_paused(..., ui="choice", options=[...])` |
| `InterruptResolution.remember` | `"once"` | yes | `InterruptResolution(..., remember="run")` (an `APPROVE` only) |
| `ScheduleSpec.timezone` | `"UTC"` | yes | `ScheduleSpec(..., timezone="Europe/Berlin")` |
| `ScheduleSpec.enabled` | `True` | yes | `ScheduleSpec(..., enabled=False)` |
| `JudgeVerdict.as_feedback(threshold=)` | `0.5` | yes | `verdict.as_feedback(event, threshold=0.8)` |

Constants (in `trellis.contracts.errors` unless noted), fixed in code:

| Constant | Value | What it is |
|---|---|---|
| `MESSAGE_MAX_CHARS` | `2000` | `AgentError.of` truncates an exception's message to this |
| `RETRYABLE_CATEGORIES` | `TIMEOUT`, `RATE_LIMIT`, `DEPENDENCY` | the categories retried when nothing else says |
| `A2A_PROTOCOL_VERSION` (`trellis.contracts`) | `"1.0"` | the A2A protocol an `AgentCard` declares |

Every field, its type and its description: [api.md](api.md), or the JSON Schema of any model
(`RunStart.model_json_schema()`).

The services built on these types have settings of their own:
[agent-runs](https://github.com/amitmohapatra/agent-runs/blob/main/docs/configuration.md),
[agent-harness](https://github.com/amitmohapatra/agent-harness/blob/main/docs/configuration.md),
[bifrost-sdk](https://github.com/amitmohapatra/bifrost-sdk/blob/main/docs/configuration.md).
