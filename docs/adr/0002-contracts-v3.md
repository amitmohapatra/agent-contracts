# ADR 0002: Contracts v3, queued runs and only the ports something implements

**Status:** accepted, amended by [ADR 0003](0003-documented-fields-and-closed-vocabularies.md) and [ADR 0004](0004-no-run-store-port.md) · **Date:** 2026-09-30 · **Amends:** [ADR 0001](0001-contracts-v2.md)

## Context
The platform overhaul (Trellis `OVERHAUL-SPEC.md` §1, §2, §5) removes the Temporal
integration, merges agent-schedules into agent-runs, makes the Memory Service the feedback
store and gives durable execution one shape: agent-runs queues a run, a `trellis worker`
claims it under a lease and runs it. Contracts v2 carried ports nothing implements any more,
had no way to say a run is waiting for a worker, could only ask a free-text question or an
approval, and described a schedule by fewer fields than the service stores, so agent-runs
kept parallel models. v3 closes those gaps. Still types only: no runtime, transport, I/O or
new dependency.

## Decision
- **Removed ports and events.** `FrameworkAdapter`, `PromptProvider`, `FeedbackStore`,
  `Scheduler`, `LifecycleListener`, `AgentRegistryClient`, `EvaluationSink`,
  `EvaluationProvider.submit_feedback` and `LifecycleEvent`. Feedback goes to the Memory
  Service; agent-runs owns schedules; a harness that wants a lifecycle vocabulary keeps it
  internal. No aliases: this is a pre-1.0 platform, and every caller moves in the same change.
  Kept: `ModelClient`, `ToolClient`, `ArtifactClient`, `MemoryPort`, `TelemetryProvider`,
  `TelemetryRedactor`, `EvaluationProvider` (`score`, `submit_dataset_item`),
  `AgentPolicyProvider`, `EventSink`, `RunStore`, `Judge`, `AgentDirectory`,
  `AgentInterceptor`.
- **Queued runs.** `RunStatus.QUEUED` is live, not final. The state machine lives in the
  contract, as `RunStatus.can_become(target)`, so the service, the harness and a test double
  cannot disagree about it:
  `QUEUED → RUNNING | CANCELLED | TIMEOUT`;
  `RUNNING → QUEUED | PAUSED | SUCCESS | PARTIAL | ERROR | TIMEOUT | CANCELLED | REJECTED`;
  `PAUSED → RUNNING | QUEUED | CANCELLED | TIMEOUT`; a final status moves nowhere.
  `RUNNING → QUEUED` is a worker's lease lapsing (the store counts the attempt);
  `PAUSED → QUEUED` is a durable run resumed for a worker to pick up. A paused run never ends
  without running again, except by cancellation or timing out unanswered.
  `RunStore.queued(start)` records a run for a worker; `RunRecord.from_start(start, status=)`
  starts a record `QUEUED` or `RUNNING` and nothing else. `RunOutcome.from_status` refuses
  both unsettled statuses.
- **`RunStart` is agent-runs' create body.** `run_id` defaults to a fresh `run_…` id so a
  service queuing a run nobody waits on (a schedule firing) can build one; the harness still
  passes the context's run id. `RunRecord` already carries every column agent-runs reads
  back (`workspace_id`, `last_resolution` included). Queue mechanics (`lease_owner`,
  `lease_expires_at`, `queued_at`) stay the service's own columns: a caller never reads or
  writes them through the contract, and `RunRecord` ignores them when read back. The inbox
  columns (`assignee`, the interrupt's `deadline`) are denormalised by the service from
  `awaiting`, not duplicated on the record.
- **Checkpoints.** `RunRecord.checkpoint` is the executor's opaque state (a resume journal:
  answered asks, completed tool outputs keyed by content, a framework's own resume state such
  as a LangGraph interrupt id or a serialized OpenAI `RunState`). It is written with the pause
  (`RunStore.paused(interrupt, checkpoint=)`), returned on every read and claim so a different
  worker resumes without repeating side effects, and cleared when the run finishes; a final
  record carries none. The service bounds its size. The contract never looks inside it.
- **Interrupts.** `InterruptReason` gains `REVIEW` and `CHOICE` (`QUESTION`, `APPROVAL`,
  `REVIEW`, `CHOICE`, `AUTH`). `Interrupt` gains `ui` (`approve` | `form` | `table` | `diff`
  | `choice`, default `approve`), `options` (for a `CHOICE`), `payload_ref` (an `ArtifactRef`
  for data too large to travel with the question), `assignee` (a principal such as `user:u1`
  or `role:procurement`), `deadline` and `escalate_to`. Validation: an `APPROVAL` carries its
  tool call (as before), a `CHOICE` its options, a `REVIEW` the shape of a correction in
  `expects`, and `escalate_to` needs a `deadline` (without one it would never happen).
  `Interrupt.from_paused` forwards any of these as keyword fields.
- **Schedules.** `Schedule` gains what agent-runs keeps: `created_by` (from the creating
  credential; `ScheduleSpec` refuses the field, so a caller cannot assert an author),
  `last_run_id`, `consecutive_failures` (fires that could not queue a run, `≥ 0`),
  `last_error` (a JSON object) and `retry_after` (a retryable fire failure backs off until
  then). `ScheduleSpec.name` is at most 200 characters, as the service stores it.
- **No per-run webhooks.** `RunStart`, `RunStart.from_request` and `ScheduleSpec` carry no
  `webhook_url` (it replaces ADR 0001's note that it travels in metadata). Notifications
  (`run.paused`, `run.escalated`, `run.finished`) go to the tenant's webhook subscriptions in
  agent-runs, which refuses a `webhook_url` field with a 422 (the models forbid it).
- **Payloads out of the checkpoint.** Large review data (an `ask` table, a diff) is uploaded
  to agent-runs as a run artifact and travels as `Interrupt.payload_ref` (an `ArtifactRef`:
  `artifact_id`, `uri`, `mime_type`, `checksum`, `size_bytes`), so checkpoints stay small.
- **Field policy unchanged** (ADR 0001): what the platform writes refuses unknown fields
  (`RunStart`, `Interrupt`, `ScheduleSpec`...); what is read back from a store (`RunRecord`,
  `Schedule`) ignores them. Every timestamp, the new `deadline` and `retry_after` included,
  is timezone-aware.
- **A2A.** `A2A_PROTOCOL_VERSION` is `"1.0"` and the docs say so.
- Version 0.4.0.

## Consequences
- agent-runs drops its `Run`, `RunCreate` and `Schedule` models and its private `RUNNING`
  state and transition table for `RunRecord`, `RunStart`, `Schedule` and
  `RunStatus.can_become`; its `InvalidTransition` stays as the service's error.
- The harness implements `RunStore.queued` in every store, removes its implementations of
  the deleted ports, keeps any lifecycle enum private, and sends feedback to the Memory
  Service only.
- A stored `awaiting` written by 0.3 reads back unchanged: every new `Interrupt` field has a
  default. An `awaiting` written by 0.4 carries `ui` and `options`, which a 0.3 reader
  refuses (`Interrupt` is closed); upgrade readers before writers.
