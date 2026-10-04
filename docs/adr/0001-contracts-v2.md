# ADR 0001: Contracts v2, the seams every trellis package builds on

**Status:** accepted, amended by [ADR 0002](0002-contracts-v3.md),
[ADR 0003](0003-documented-fields-and-closed-vocabularies.md) and
[ADR 0004](0004-no-run-store-port.md) · **Date:** 2026-09-28

## Context
`trellis-contracts` 0.2 carried the V1 seams: `AgentRequest`/`AgentResponse`, the execution
context, tool and model types, and ports for memory, telemetry, policy and a registry. The
platform design (trellis-harness `docs/PLATFORM-DESIGN-2026-09-28.md`, §4, §7, §9–11, §14)
needs more: a run event stream every surface speaks, one framework-neutral interrupt with
its resolution, a feedback record the Memory Service stores, an A2A Agent Card mapped from
the descriptor, and ports for run stores, event sinks, judges and agent directories. Every later phase codes against these, so they land first, as types
with no runtime, transport, I/O or new dependency.

## Decision
- **Runs** (`runs.py`). `RunStatus` is `RUNNING`, `PAUSED` and the `AgentStatus` endings
  under the same spellings, so a run store and a response never disagree; `RunStart` is
  built from an `AgentRequest` (its idempotency key derives from the context), `RunRecord`
  extends it with the state (a typed `awaiting: Interrupt`, `error: AgentError`, the last
  resolution, the attempt) and holds its invariants: only a `PAUSED` run waits, on its own
  interrupt; only a failed ending carries an error. The contract carries `workspace_id`;
  agent-runs' rows do not yet, so its adapter keeps it in the run's metadata until the
  column exists, and it maps `RunStore.resumed` onto the service's resume plus transition
  metadata (`CANCEL` is the `CANCELLED` transition).
- **Events**. `RunEventType` uses the AG-UI spellings (`RUN_STARTED` … `CUSTOM`) plus
  `CONTEXT_LOADED` and `INTERRUPT`, which an AG-UI transport carries as `CUSTOM`; `RunEvent`
  is frozen, ordered by `sequence` within an `attempt`, carries the tenant, run and thread
  ids from the context (a hub fans out by tenant, never by the caller-chosen thread alone)
  and its payload in `data`, unredacted; `RUN_FINISHED` carries a `RunOutcome` (`rejected` is
  its own outcome; an AG-UI transport reports `error`, `rejected`, `cancelled` and `timeout`
  as `RUN_ERROR`) and, for an interrupt, the interrupt.
- **Interrupts**. `Interrupt` is `AgentPaused` plus an id, the tenant and run, a `reason`
  (`QUESTION`, `APPROVAL`, `AUTH`; an approval carries the tool call) and a non-blank
  question; `awaiting()` is the whole record as JSON, what run stores and webhooks carry and
  `Interrupt.model_validate` reads back. `InterruptResolution` names the interrupt and the
  run it answers (`resolves()`), records the decision (`ANSWER`, `APPROVE`, `REJECT`,
  `EDIT` with the edited arguments, `CANCEL`); approve, reject and edit of a tool call are
  also `Feedback` (`to_feedback`, attributed to the paused run and refused for any other
  interrupt, run or tenant), so the two records meet in one table. Resuming continues the
  same run: the store moves it back to `RUNNING` on its next attempt.
- **Feedback** (`feedback.py`). One record per judgement: target kind (run, answer, memory,
  tool call, brief, procedure), verdict (confirm, reject, correct, approve, edit), source
  (human, judge, interrupt), a score in [0, 1], the correction, the reviewer, evidence.
  `for_context` binds it to the request's identity.
- **Judging** (`evaluation.py`). `JudgeVerdict` is a score with its method (`GROUNDED` first,
  `LLM` second), model and cost; `as_feedback` turns it into a judge feedback record on the
  answer.
- **Agent Card** (`a2a.py`). `AgentCard.from_descriptor` maps the descriptor to the A2A
  1.0 card (skills, capabilities, security schemes, interfaces, signatures); `to_a2a`
  renders the camelCase JSON the protocol spells, without the platform's own metadata;
  `from_a2a` reads a foreign card as data (unknown keys dropped, every URL http(s)).
  `a2a-sdk` is not imported here.
- **Ports** (`ports.py`). `EventSink.publish`, `RunStore` (started/paused/resumed/finished/
  get/list_paused), `Judge.judge`, `AgentDirectory` (get/find/publish); all
  `runtime_checkable`. (The scheduler and feedback-store ports this ADR first added are
  removed by ADR 0002.) A store verifies a record's
  identity fields against the authenticated caller; a record only claims who it is from.
- **Tool status** is the enum `ToolStatus` (ok, error, timeout, rejected, cancelled); a
  `StrEnum`, so `outcome.status == "ok"` still holds, and assignments are validated.
  `AgentEvalEvent.status` is `AgentStatus`.
- **Schedules.** `ScheduleSpec` names the schedule and the person it acts for (required, as
  agent-schedules requires), a cron expression or a named bucket in a real timezone; the
  contract's `workspace_id` travels in the service's metadata until it has the column.
  (`webhook_url` was removed by ADR 0002.)
- **Field policy.** Records the platform writes and streams refuse unknown fields; records
  read back from a store (`RunRecord`, `Schedule`) and cards read from another agent ignore
  them. Every timestamp is timezone-aware. Ids are prefixed with an underscore (`int_`,
  `evt_`, `fb_`, `sch_`) like `run_` and `req_`.
- Version 0.3.0. The harness's current runs client (`started(context)`, `paused(context,
  reason, awaiting)`, `finished(context, status, output)`) predates `RunStore` and is
  replaced by an adapter of it in the harness-core phase.

## Consequences
- The Memory Service's `POST /v1/feedback` (phase 2) accepts `Feedback` as is; the harness
  (phase 3) emits `RunEvent`s and raises `Interrupt`s; the A2A server (phase 4) publishes
  `AgentCard.to_a2a()`; the Langfuse judge (phase 6) returns `JudgeVerdict`s.
- Every closed vocabulary here is an enum; a typo is a validation error, not a silent branch.
- The one break: `ToolOutcome.status` refuses strings outside `ToolStatus`; a foreign status
  belongs in `error_class` or `metadata`.
- Payloads that leave the process (`RunEvent.data`, `Interrupt.awaiting()`) are unredacted
  by design; the surface that emits them redacts. Whether to trust a foreign card's URLs is
  the A2A client's decision; the contract only guarantees they are http(s).
