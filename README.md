# universal-agent-contracts

The types an agent must accept and return. No runtime, no transport, no I/O.

Every other repo in the platform depends on this one, and this one depends on nothing but
`pydantic`. That is the point: the contract can be read, versioned and reasoned about
without pulling in a gateway, a database or an agent framework.

## What is in here

| Module | What it defines |
| --- | --- |
| `request` / `response` | `AgentRequest`, `AgentResponse` — the two ends of one execution |
| `context` | `AgentExecutionContext`: tenant, user, thread, agent, run lineage, and `scope_fields()`, the exact keyword arguments the Memory Service expects |
| `artifacts` | What an execution produces on the way: memory observations, tool calls, evaluations |
| `errors` | The typed failures a caller can branch on (`MemoryUnavailableError`, `ConfigurationError`, …) |

## Install

```bash
uv add universal-agent-contracts
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
