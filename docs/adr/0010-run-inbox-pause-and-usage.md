# ADR 0010: A run's inbox, a person's pause, and what a run has used

**Status:** accepted · **Date:** 2026-11-02

## Context
A sub-agent working in the background on a worker in another process could not be reached:
an instruction for it reached a run only in the process that held it (the harness's ADR 0008,
"Limits"). A person could not pause a run and resume it later, and a UI listing a user's tasks
could not say how far each had gone (model calls, tool calls, commands, tokens, cost) without
reading every event in its log.

## Decision
- `RunMessage` (frozen, unknown fields refused): one message in a run's inbox —
  `message_id` (the sender's: sent again, nothing is added), `tenant_id`, `run_id`, `kind`,
  `text`, `sender` (`kind:id`, set by the store), `position` (from 1) and `created_at`.
  agent-runs keeps the inbox; the harness reads past the last position it took, at each step.
- `RunMessageKind`: `INSTRUCTION` (taken at the run's next step; its text required) and `PAUSE`
  (pause at the next step and wait for a person). Resuming is not a message: it answers the
  pause, as any interrupt is answered (`InterruptResolution`).
- `InterruptReason.PAUSE`: the interrupt a run waits on when a person paused it, so a UI shows
  a "resume" control rather than a question.
- `RunUsage` (frozen, unknown fields ignored): `model_calls`, `tool_calls`, `commands`,
  `tokens`, `cached_tokens`, `cost`, all from 0. `RunRecord.usage` is the last one its executor
  saved (with its progress, its pause, its ending); `None` until one was.

## Consequences
- A patch release (0.6.4): new types, one new optional field and one new reason. A reader of
  records on 0.6.3 ignores `usage` (`RunRecord` ignores unknown fields); one that parses an
  `Interrupt` with reason `PAUSE` needs 0.6.4.
- One record carries the status view: listing a user's runs gives each one's usage without a
  read of its event log.
