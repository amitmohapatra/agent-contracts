# ADR 0007: A schedule carries everything a started run can: queue order and metadata

**Status:** accepted · **Date:** 2026-10-06 · **Amends:** [ADR 0006](0006-interrupts-v2-and-queue-order.md)

## Context
ADR 0006 gave `RunStart` a `priority` and a `concurrency_key`, and gave `ScheduleSpec` the
run limits of ADR 0005 (`timeout_seconds`, `agent_version`), but not those two. A fire builds
its `RunStart` from the schedule alone, so a scheduled run was always claimed at priority 0
and never shared a concurrency key: "the nightly digest may wait behind the people who are
here" or "never two runs of this export at once" could be said of a run somebody started,
not of one a schedule fired.

`ScheduleSpec.metadata` was kept with the schedule and never reached its runs. A caller that
keeps what it needs to start the run in `RunStart.metadata` (which tools a run goes without,
options for the framework that executes it) got a different run from a schedule than from a
direct start with the same arguments.

## Decision
- **`ScheduleSpec.priority`** and **`ScheduleSpec.concurrency_key`**: the same types,
  bounds, defaults and descriptions as on `RunStart` (`-1000` to `1000`, default `0`; an
  optional key of 1 to 200 characters). `Schedule` inherits them. agent-runs copies both
  into the `RunStart` of every fire.
- **`ScheduleSpec.metadata`** is copied into each fired run's `RunStart.metadata`. The keys
  the fire writes itself (in agent-runs: `schedule_id`, `schedule_name`, `fire_time`,
  `created_by`) are laid over it and win on conflict, so a schedule cannot pass its run off
  as another schedule's fire.
- A scheduled run now carries everything a started one can: selection and options work the
  same whether a run is started or scheduled.
- Version **0.6.1**: additive. Both new fields are optional with defaults that keep the old
  behaviour; the metadata rule is agent-runs' behaviour, documented on the field.

## Consequences
- `ScheduleSpec` refuses unknown fields (ADR 0001), so agent-runs on 0.6.0 refuses a schedule
  that sets `priority` or `concurrency_key`, and the new defaults when a client sends every
  field (as `ScheduleSpec.model_dump()` does). agent-runs moves to 0.6.1 before any caller
  sets them; consumers pin `trellis-contracts>=0.6.1,<0.7`.
- A schedule's metadata now reaches its runs, so anything a caller keeps there is visible on
  every run it fires. A caller that kept private notes on a schedule moves them to keys it
  does not read from runs, or off the schedule.
- The claim order and the per-key limit are still enforced by agent-runs, where the queue is;
  a fired run waits its turn exactly as a started one with the same fields would.
