# ADR 0008: `acp` is an error source

**Status:** accepted · **Date:** 2026-10-27 · **Amends:** [ADR 0003](0003-documented-fields-and-closed-vocabularies.md)

## Context
ADR 0003 closed `ErrorSource` over the values the sibling repos pass, among them the harness's
adapter names: the harness records the adapter's name as the source of every failed run. The
harness gains an adapter for agents it speaks to over the Agent Client Protocol (ACP), named
`acp`: Kimi Code is driven that way (its `kimi acp` server), since Moonshot archived the Python
Kimi CLI and its Python agent SDK. A failed run of such an agent — its process ended in the
middle of a turn, it refused, it is a version the harness did not test — is refused as a record
while `acp` is not a source.

## Decision
- `ErrorSource` gains `acp`, set by the harness for every failed run of an ACP agent, as each
  adapter's name is set for its own.
- Nothing else changes: a qualified source is still split as ADR 0003 says, and any other value
  is refused.

## Consequences
- A patch release (0.6.2): an additive value, no field changes.
- A reader on 0.6.1 or earlier refuses a record whose source is `acp`, as a closed vocabulary
  does. agent-runs, which stores the errors of failed runs, needs 0.6.2 before the harness
  records an ACP agent's failure through it; the harness pins `>=0.6.2` from the release that
  adds the adapter.
