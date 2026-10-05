# ADR 0005: A run's working-time limit, its working time, and the agent version that ran it

**Status:** accepted · **Date:** 2026-10-05 · **Amends:** [ADR 0002](0002-contracts-v3.md)

## Context
A run could only be bounded by `RunStart.deadline`, an absolute time. That suits "finish by
9:00", but most callers mean "do not work on this for more than ten minutes": a limit on the
time the run spends working, not on the time it waits in the queue or for a person to
answer. Without it a run that pauses for a day's review would need a deadline a day long,
which no longer bounds the work at all. The limit must survive a crash, so the time already
worked must be kept with the run, not in a worker's memory.

Runs also outlive deploys: a run paused for review on Monday is resumed on Tuesday by
whichever code is running then. Nothing recorded which code started it.

## Decision
- **`RunStart.timeout_seconds`** (optional, > 0): the most working time the run may take,
  counted while it is `RUNNING`, across attempts. Past it the run ends `TIMEOUT`. Queued
  time and time waiting for a person do not count; `deadline` keeps its meaning (an
  absolute end, waiting included), and both may be set.
- **`RunRecord.worked_seconds`** (default 0): the working time so far, kept by the run
  store as the run moves in and out of `RUNNING`, so a new attempt continues the same
  clock.
- **`RunStart.agent_version`** (optional, at most 128 characters): the version of the
  agent's code that started the run, recorded for audit and for telling which code a
  resumed run continues on. The contract never interprets it.
- Version 0.5.1: the fields are optional and default to the old behaviour.

## Consequences
- `RunStart` refuses unknown fields, so a run store on 0.5.0 refuses a start that sets
  them. The run store moves to 0.5.1 before any caller sets them; a caller leaves them
  unset (not `null`) when it has nothing to say.
- agent-runs enforces `timeout_seconds` itself (it sees every move into and out of
  `RUNNING`), so a run is bounded even when its worker dies; the harness also stops a run
  kept in its own process.
