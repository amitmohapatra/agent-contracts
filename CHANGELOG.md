# Changelog

What changed in each version of `trellis-contracts`. Why each change was made is in its
[ADR](docs/adr/README.md). Which versions of the other Trellis packages go with which is in
[docs/versioning.md](docs/versioning.md).

## Unreleased

Documentation only, no change to the package:

* A "Start here" README; the export list moved to [docs/api.md](docs/api.md); the run
  lifecycle, wire conventions and the record lifecycle sequence live once, in
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
* Nine runnable examples in [examples/](examples/README.md) (`make examples`).
* A `Makefile` with the targets CI runs, and a link check (`make links`) in CI.
* [docs/versioning.md](docs/versioning.md): the compatibility table now gives each sibling's
  real pin (the harness requires `>=0.6.0,<0.7`, agent-runs `>=0.6.1,<0.7`).
* ADR headers: 0002 lists 0005 and 0006 as amendments, 0005 lists 0006, 0006 lists 0007; an
  ADR index.

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
