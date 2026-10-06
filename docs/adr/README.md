# Architecture decision records

Each record says what was decided about the contracts, why, and what it changed. A record is
never rewritten after it is accepted. A later decision **amends** it, and both headers say so:
"amended by" on the older one and "Amends" on the newer one.

No record is superseded as a whole. Where a later record replaced part of an earlier one, that
part is noted in the last column, and the earlier record lists the later one as "amended by".

| ADR | Decision | Version | Status | Amends | Amended by | Replaced parts |
|---|---|---|---|---|---|---|
| [0001](0001-contracts-v2.md) | Contracts v2: runs, events, interrupts, feedback, judging and agent cards | 0.3.0 | accepted | — | 0002, 0003, 0004 | the ports nothing implemented, such as `FeedbackStore`, `Scheduler` and `AgentRegistryClient` (0002); `RunStore` (0004) |
| [0002](0002-contracts-v3.md) | Contracts v3: queued runs, richer interrupts, schedules; only the ports something implements | 0.4.0 | accepted | 0001 | 0003, 0004, 0005, 0006 | the `RunStore` port it kept (0004) |
| [0003](0003-documented-fields-and-closed-vocabularies.md) | Every field documented, closed vocabularies typed, SDK errors read as they say | 0.5.0 | accepted | 0001, 0002 | — | — |
| [0004](0004-no-run-store-port.md) | No `RunStore` port; `trellis.runs` is the runs client | 0.5.0 | accepted | 0001, 0002 | — | — |
| [0005](0005-run-working-time-and-agent-version.md) | A run's working-time limit, its working time and its agent version | 0.5.1 | accepted | 0002 | 0006 | — |
| [0006](0006-interrupts-v2-and-queue-order.md) | Interrupts v2 (labelled options, several picks, own screens), decision scope, queue order | 0.6.0 | accepted | 0002, 0005 | 0007 | — |
| [0007](0007-schedules-carry-queue-order-and-metadata.md) | A schedule carries its runs' queue order and metadata | 0.6.1 | accepted | 0006 | — | — |

What each version changed, in one place: [CHANGELOG.md](../../CHANGELOG.md).

## Writing a new record

Number it after the last one, start it with a `**Status:** · **Date:** · **Amends:**` line,
and add "amended by" to the header of every record it amends, plus a row here. A record that
replaces another as a whole says "superseded by" in the older header instead.
