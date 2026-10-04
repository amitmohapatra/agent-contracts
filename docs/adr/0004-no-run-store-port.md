# ADR 0004: No `RunStore` port; `trellis.runs` is the runs client

**Status:** accepted · **Date:** 2026-10-04 · **Amends:** [ADR 0001](0001-contracts-v2.md),
[ADR 0002](0002-contracts-v3.md)

## Context
ADR 0002 kept `RunStore` (`queued`, `started`, `paused`, `resumed`, `finished`, `get`,
`list_paused`) as the port a durable run is written against. Nothing implemented it. The
harness's store had the same lifecycle verbs but listed its inbox differently, so it was
never asserted as a `RunStore`, and agent-runs served HTTP rather than the port. Meanwhile
agent-runs gained stable operation ids (`runs.start`, `runs.claim`, `runs.heartbeat`,
`runs.pause`, `runs.resume`, `runs.finish`, `runs.get`, `runs.list`) and ships its own SDK,
`trellis.runs` (pip `trellis-runs`), whose `RunsClient` uses those names. A port with a
second vocabulary, implemented by nobody, only invited a third.

## Decision
- **`RunStore` is deleted** from `trellis.contracts.ports` and `trellis.contracts`. No alias.
- **The runs client is `trellis.runs.RunsClient`**, from agent-runs. Its verbs are the
  service's operation ids, and it sends and returns the types here unchanged: `RunStart`,
  `Interrupt`, `InterruptResolution`, `RunRecord`, `Schedule`, `ScheduleSpec`, `ArtifactRef`.
- **The lifecycle is described in those verbs**: `start` (or `start(queue=True)`), `claim`,
  `pause`, `resume`, `finish`. `RunStatus.can_become` stays the one transition check.
- Version 0.5.0.

## Consequences
- Code that imported `RunStore` fails to import. No sibling did.
- agent-runs pins `trellis-contracts<0.6`, so it accepts 0.5.0. Each sibling that records
  this package's version in its `uv.lock` relocks in the same change set.
- A harness that keeps an in-process store for when agent-runs is absent declares its own
  protocol, the subset of `RunsClient` it calls.
