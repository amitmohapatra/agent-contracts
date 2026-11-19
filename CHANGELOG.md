# Changelog

What changed in each version of `trellis-contracts`. Why each change was made is in its
[ADR](docs/adr/README.md). Which versions of the other Trellis packages go with which is in
[docs/versioning.md](docs/versioning.md).

## Unreleased

The next release is 0.8.0, a minor ([the version rules](docs/versioning.md#the-version-rules)):
some entries below refuse what was accepted (a naive context deadline, a copy the type
refuses, an error where its field says there is none). The version in `pyproject.toml` stays
0.7.1 until agent-harness, agent-runs and agent-memory-service, which
all require `<0.8`, admit 0.8.

* `RunRecord.moved`, `with_fields` and `AgentError.of` read a model's values from the instance
  (`_fields.fields_of`) instead of `dict(model)`, which walks pydantic's iterator: a move is
  about twice as fast (13 to 7 µs), and a run moves its record several times. Same values,
  extra ones included; still validated.
* `AgentExecutionContext.deadline` is timezone-aware (`AwareDatetime`), as `RunStart.deadline`
  already was. A naive `datetime` was accepted, and `remaining_seconds` then raised
  `TypeError` in the middle of the run. It is now refused where it is given: a
  `ValidationError` from the constructor, `create` and `model_validate`, a `ValueError` from
  `with_deadline` and `for_agent`. A caller that passed a naive deadline attaches its zone
  (`deadline.replace(tzinfo=UTC)`).

* `with_fields` validates its copy, on `AgentExecutionContext`, `AgentRequest` and
  `AgentResponse` (and so do `with_deadline` and `add_warning`, which use it). It was
  `model_copy(update=...)`, which checks nothing: `ctx.with_fields(tenant_id=None, bogus=1)`
  returned a context with no tenant and an attribute the type refuses. The copy is now built
  as the constructor builds one, so a change the type refuses raises `ValidationError`, and a
  context given a `turn_id` in a thread gets its derived `session_id` (it stayed `None`).
  Unchanged values are passed as they are, not rebuilt.

* An `error` is refused where its description already said there is none:
  * `RunEvent.error` only on `RUN_ERROR`, or on `RUN_FINISHED` whose outcome is `error`,
    `timeout` or `rejected` (the endings a `RunRecord` carries an error for). An error on any
    other event, or on a run finished `success`, `partial`, `cancelled` or `interrupt`, is a
    `ValidationError`. A tool's failure in a `TOOL_CALL_RESULT` event's `data` is untouched.
  * `AgentResponse.error` only with a status that is not ok: `SUCCESS` or `PARTIAL` with an
    error is a `ValidationError`.
  * `RunRecord.error` is unchanged: refused on a status that did not fail, and still optional
    on `ERROR`, `TIMEOUT` and `REJECTED` (a stored record without one keeps reading). Its
    description now says so.

  The harness emits an error only on those events. A run store that validates stored events
  (agent-runs reads them with `RunEvent.model_validate`) refuses an event another producer
  appended with an error elsewhere.

* `Schedule.last_error` is an `AgentError | None`, as `RunRecord.error` is; it was a
  `dict[str, Any]` described as a JSON `AgentError`. The JSON is the same, and a stored
  `AgentError` dict (what agent-runs writes) reads as before. A reader gets an `AgentError`,
  not a dict (`schedule.last_error.code`, not `["code"]`), and a dict that is not an
  `AgentError` is refused.

* `JudgeVerdict.as_feedback` targets the run: `target_kind` is `run` and `target_id` the
  event's `agent_run_id`. It was `answer`, with the result artifact's id as `target_id` when
  the event named one, and the Memory Service, the only store of feedback, has no `answer`
  kind and answered 422. The result artifact is now `metadata.result_ref`; `metadata.target`
  is gone. `FeedbackTargetKind` is unchanged: `ANSWER` and `BRIEF` stay until
  [ADR 0010](docs/adr/0010-exported-names-without-a-consumer.md) is decided.

* `AgentError.of` applies `category=` and `retryable=` to a `HarnessError` too. It returned
  the error's own classification before reading them, so
  `AgentError.of(ToolError(), retryable=True)` was not retryable. Without them a
  `HarnessError` keeps its own, as before.

* `AgentError.of` and `HarnessError.to_error` no longer raise on a source outside
  `ErrorSource`. They run on the failure path, and the harness passes its adapter's name as
  the source: an adapter this version does not list raised a `ValidationError` in place of
  the run's error. The source is now `None` and the name is kept in
  `details.source_detail`. A blank source is no source. `AgentError(source=...)` and
  `model_validate` still refuse an unknown source: the vocabulary of a stored error stays
  closed ([ADR 0003](docs/adr/0003-documented-fields-and-closed-vocabularies.md), which had
  the two normalisers refuse it too).

* `RunRecord.read(data)` and `Schedule.read(data)`: how a store or a client reads a record
  back. `RunRecord.model_validate` ignores an unknown field only at the top level; the
  `Interrupt`, `Option` and `InterruptResolution` inside a record refused one, so a reader on
  an older patch could not read a record whose `awaiting` carried a field a newer patch added.
  `read` drops unknown fields at every depth and validates everything else as before. A
  record being written still refuses an unknown field, and `model_validate` is unchanged.
  Additive. An unknown value of a closed vocabulary is still refused, so a new member on a
  record that is read back is a minor from 0.8.0 ([the version rules](docs/versioning.md#the-version-rules)).

* `RunRecord.moved(to, *, keep_checkpoint=False, **changes)`: the record after a status
  change, so a run store no longer spells out what a move ends. It raises `ValueError` for a
  move `RunStatus.can_become` refuses (a status never becomes itself, so `SUCCESS` to
  `SUCCESS` is refused) and for a change that names no field; it clears `awaiting` unless the
  run pauses and `checkpoint` when the run ends, unless `keep_checkpoint`; it sets
  `updated_at` to now unless given, and validates the result. Additive: agent-runs and the
  harness's local store each still do this themselves until they call it.

* `Schedule.run_start(*, fire_time, thread_id=None, run_id=None)`: the `RunStart` of the run
  a fire queues, so every store that fires a schedule queues the same run. It copies each
  field a `ScheduleSpec` shares with a `RunStart`; `metadata` is the schedule's under
  `schedule_id`, `schedule_name`, `fire_time` (UTC) and `created_by`; `idempotency_key` is
  `<schedule_id>@<fire time in UTC, ISO 8601>`, the key agent-runs already derives; `user_id`
  is `None` (the run acts for `on_behalf_of`). Additive. The two hand-written copies differ
  today: agent-runs sets no `user_id`, and the harness's local store sets `user_id` to
  `on_behalf_of`, no idempotency key and only `schedule_id` in `metadata`.

* `InterruptResolution.problem_with(interrupt)`: why the resolution cannot answer the
  interrupt, or `None`. It names an answer to another interrupt or run, and an approval
  remembered beyond its call (`run` or `always`) where the interrupt has no tool call. The
  second rule lived only in `trellis.runs.answers.answer_problem`, which checked `run` and
  missed `always` (0.7.1). Whether an answer fits `expects` stays there: it needs JSON Schema.
  Additive.

* `is_pause_signal` is exported from `trellis.contracts`. It was importable only from
  `trellis.contracts.errors`, where the harness imports it in six modules; that import keeps
  working. Additive.

* `AgentError.of` cuts a `HarnessError`'s message to `MESSAGE_MAX_CHARS` (2,000), as it
  already cut any other exception's and as the field's description says. A caller that
  relied on a longer message reads its first 2,000 characters.

* A finished `RunRecord` may carry a `checkpoint`: the one its caller asked the run store to
  keep past the run's end (agent-runs `finish` / `cancel` with `keep_checkpoint`), so an ended
  run can be rewound. Relaxed, not added: a store that clears the checkpoint at the end, as
  before, reads the same.

* `FeedbackSource.SYSTEM` (`"system"`): a judgement the platform derives from a run's final
  status, from no reviewer. The harness already files run outcomes with it and the memory
  service's own enum has it; the contract now lists it. Additive.

* `RunRecord.placed_at` (optional, read-only, set by the run store): a run's place among the
  runs sharing its `concurrency_key` — when it was started, or when its latest answer was
  recorded. Additive: a reader that does not know it ignores it (`RunRecord` ignores unknown
  fields), and a record from a store that does not send it reads `None`. agent-runs computes
  it under the key's lock; the harness orders a conversation's turns by it instead of
  recomputing it.

Documentation:

* [ADR 0010](docs/adr/0010-exported-names-without-a-consumer.md) (proposed): 46 of the 85
  exported names are imported by no sibling's source. It lists the seven that stay and the 39
  that leave in 0.8.0, and what the removal has to carry with it. Nothing is removed yet.
* [docs/versioning.md](docs/versioning.md): the table gives the pins and versions on each
  sibling's `main` today (the harness and agent-runs require `>=0.7.1,<0.8`; `trellis-memory`
  is 0.6.6, `bifrost-sdk` 0.4.0). [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) said the Memory
  Service does not import this package; it imports `Endpoint` and `ModelProfile`, from a copy
  it vendors.
* The `RunRecord` docstring, the `checkpoint` field description and
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) said a final record carries no `checkpoint`;
  it does when its caller asked the store to keep it. [docs/troubleshooting.md](docs/troubleshooting.md)
  listed an error (`a finished run carries no checkpoint`) the package no longer raises.
* A "Start here" README; the export list moved to [docs/api.md](docs/api.md); the run
  lifecycle, wire conventions and the record lifecycle sequence live once, in
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
* Nine runnable examples in [examples/](examples/README.md) (`make examples`).
* A `Makefile` with the targets CI runs, and a link check (`make links`) in CI.
* [docs/versioning.md](docs/versioning.md): the compatibility table now gives each sibling's
  real pin (the harness requires `>=0.6.0,<0.7`, agent-runs `>=0.6.1,<0.7`).
* ADR headers: 0002 lists 0005 and 0006 as amendments, 0005 lists 0006, 0006 lists 0007; an
  ADR index.

## 0.7.1 (2026-11-10)

* `InterruptRemember` gains `always`: an approval that allows the tool for the user in every
  conversation until the permission is revoked (the harness keeps it in the memory service's
  tool permissions). `run` now reads "for this conversation": the tool's later calls in later
  runs of the same thread for the same user, as the harness has kept it since its "Allow for
  this conversation" choice. Only an `APPROVE` is remembered beyond its call. A reader that
  receives `always` needs 0.7.1, so a run store (agent-runs) that keeps resolutions takes it
  before a harness sends one.

## 0.7.0 (2026-11-09)

Minor: names a consumer may use are removed ([the version rules](docs/versioning.md#the-version-rules)).

* `RunMessage`, `RunMessageKind`, `RunUsage`, `RunRecord.usage` and `InterruptReason.PAUSE`
  (0.6.4) are removed from `main`: the run inbox, a person's pause and a run's usage continue
  on the `background-agents` branch with the harness's background agents and agent-runs' inbox,
  and come back in the release that lands them. Everything else is 0.6.5. agent-runs, the
  harness and the memory service on `main` admit it (`<0.8`).

## 0.6.5 (2026-11-03)

* `Option.recommended`: `True` on the option the asker recommends, its `description` saying
  why; left out on the wire otherwise (`exclude_none`), so a question that recommends nothing
  reads as before.

## 0.6.4 (2026-11-02)

* `RunMessage` and `RunMessageKind` (`INSTRUCTION`, `PAUSE`): a message in a run's inbox,
  which agent-runs keeps and the harness reads at each step, on any worker.
* `InterruptReason.PAUSE`: a person paused the run; resuming continues it.
* `RunUsage` and `RunRecord.usage`: what a run has used so far (model and tool calls, commands,
  tokens, cost), as its executor last saved it (ADR 0010, on the `background-agents` branch).

## 0.6.3 (2026-10-30)

* `Endpoint`: where a model is and how to reach it (`base_url`, `gateway`, `auth`, `model`,
  `api_key` as a `SecretStr`, `profile`), what the harness hands the Memory Service.
* `ModelProfile`: what a model accepts (`reasoning_output`, `temperature`,
  `max_output_tokens`, `tool_calling`), named as LangChain's model-profile keys, so a chat
  model's `profile` validates as one.
* `classify` maps bifrost-sdk's `BifrostOnlyError` to `VALIDATION`
  ([ADR 0009](docs/adr/0009-endpoint-and-model-profile.md)).

## 0.6.2 (2026-10-27)

* `ErrorSource` gains `acp`: the harness's adapter for agents spoken to over the Agent Client
  Protocol (Kimi Code), the source of its failed runs
  ([ADR 0008](docs/adr/0008-acp-error-source.md)). A reader needs 0.6.2 to read such a record.

## 0.6.1 (2026-10-06)

* `ScheduleSpec` gains `priority` and `concurrency_key`. Both, with `timeout_seconds` and
  `agent_version`, are copied into every run the schedule fires.
* A schedule's `metadata` reaches the runs it fires, under the fire's own keys
  ([ADR 0007](docs/adr/0007-schedules-carry-queue-order-and-metadata.md)).

## 0.6.0 (2026-10-05)

* Interrupts v2: `Option` (a `value` with a `label` and `description`), `multiple` (several
  picks), `component` and `props` (the asker's own screen), `ui_schema` (widget hints).
* `InterruptResolution` gains `comment` and `remember` (`once`, or the rest of the `run`).
* `RunStart` gains `priority` and `concurrency_key` (queue order).
* `ScheduleSpec` gains `timeout_seconds` and `agent_version`
  ([ADR 0006](docs/adr/0006-interrupts-v2-and-queue-order.md)).

## 0.5.1 (2026-10-05)

* `RunStart.timeout_seconds` (a working-time limit), `RunRecord.worked_seconds` and
  `RunStart.agent_version` ([ADR 0005](docs/adr/0005-run-working-time-and-agent-version.md)).

## 0.5.0 (2026-10-04)

* A paused run carries an opaque executor `checkpoint`, returned on read and claim and
  cleared when the run finishes.
* Runs and schedules carry no `webhook_url`: agent-runs delivers notifications to the
  tenant's webhook subscriptions.
* The feedback for a decision has one id, and tool-call feedback names the arguments that
  were asked about.
* The `RunStore` port is removed; `trellis.runs.RunsClient` is the runs client
  ([ADR 0004](docs/adr/0004-no-run-store-port.md)).
* Every model field carries a description; closed vocabularies (`ErrorSource`, `ToolSource`,
  `ObservationKind`, `InterruptUI`) are typed; `AgentError.of` keeps an SDK exception's own
  `retryable` ([ADR 0003](docs/adr/0003-documented-fields-and-closed-vocabularies.md)).

## 0.4.0 (2026-09-30)

* Contracts v3: queued runs (`QUEUED`, claim, lease), richer interrupts, schedules
  (`ScheduleSpec`, `Schedule`). Ports nothing implemented are removed ([ADR 0002](docs/adr/0002-contracts-v3.md)).

## 0.3.0 (2026-09-28)

* Contracts v2: run records and events, interrupts and their resolution, feedback, judge
  verdicts and the A2A Agent Card ([ADR 0001](docs/adr/0001-contracts-v2.md)).
* The package is renamed `trellis-contracts`, importable as `trellis.contracts` (0.2.0).

## 0.1.0 (2026-09-21)

* The first agent contract: request, response, execution context, tool and model types.
