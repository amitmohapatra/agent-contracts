# Troubleshooting and FAQ

Each entry is a message you see, why, and the fix. The messages are the models' own, so a
search for the text finds the entry.

## Validation errors

**`extra_forbidden` on a field you did set** (`RunStart`, `Interrupt`, `ScheduleSpec`, ...).
Either the name has a typo (`prority`), or the field is newer than the installed version.
Written records refuse unknown fields on purpose. Check the spelling against
[api.md](api.md), then `uv pip show trellis-contracts` against the version that added the field
([CHANGELOG.md](../CHANGELOG.md)).

**`Input should have timezone info`.** Every timestamp on a
run, interrupt, event, feedback or schedule is timezone-aware. Use `trellis.contracts.now()` or
`datetime.now(UTC)`, never `datetime.now()`.

**`Instance is frozen`.** Written records are immutable. Build a new one:
`record.model_copy(update={...})`, or `Model.model_validate({**old.model_dump(), ...})` when the
change must be validated again.

**`Input should be 'agent-runs', 'tools', 'mcp', ...`** on `AgentError.source`. `source` is a closed
`ErrorSource`. Pass one of them, optionally qualified (`mcp.refund` is stored as `mcp`, with the
tool in `details.source_detail`).

## Runs and interrupts

**`a run starts QUEUED or RUNNING, not ...`.** `RunRecord.from_start` creates only the two entry
states. A run reaches any other status through `RunStatus.can_become`.

**`a PAUSED run waits on an interrupt; no other run does`.** `status=PAUSED` and `awaiting` go
together. Set both when pausing, and clear `awaiting` when resuming.

**`the interrupt belongs to another run`.** `awaiting.run_id` and `awaiting.tenant_id` must equal
the record's own. Build the interrupt with `Interrupt.from_paused(..., context=ctx)` so they
come from the same context.

**`a finished run carries no checkpoint`.** Clear `checkpoint` when a run reaches a final
status. agent-runs does this on `finish`.

**`can_become` says no, and agent-runs answers 409.** The transition is not in the state
machine; [the run's lifecycle](ARCHITECTURE.md#a-runs-lifecycle) lists every allowed one. A
final status moves nowhere.

**`Interrupt() got multiple values for keyword argument 'expects'`.** `Interrupt.from_paused`
takes `expects` and `payload` from the `AgentPaused`. Pass them there:
`AgentPaused(question, expects={...})`.

**`AgentPaused needs a question`.** A pause nobody can answer is a hang, so a blank question is
refused.

**An `APPROVAL` without `tool_call`, a `CHOICE` without `options`, a `REVIEW` without
`expects`.** Each reason carries what it asks about. `escalate_to` needs a `deadline`,
`multiple` needs `options` or `expects`, and `props` needs a `component`.

**`to_feedback` returns `None`.** Only an `APPROVE`, `REJECT` or `EDIT` of an interrupt that
carries a `tool_call` becomes feedback. An `ANSWER` or a `CANCEL` does not.

**`to_feedback` raises `ValueError`.** The resolution, the interrupt and the context name
different runs or tenants. Use the context the run was started with.

**`... come from the context, not from the caller`** (`Feedback.for_context`). The identity
fields (tenant, user, agent, run, trace) are bound from the context and cannot be passed again.

## Imports and installs

**`ModuleNotFoundError: No module named 'trellis.contracts'`.** `trellis` is a namespace
package shared by every `trellis-*` distribution and has no `__init__.py` here. Install the
distribution (`uv add trellis-contracts`, or `uv add --editable ../agent-contracts`). Do not
create a `trellis/__init__.py` in your own code, because it would hide the others.

**A sibling refuses to install with this version.** Its pin does not admit it. The pins are in
[versioning.md](versioning.md).

## FAQ

**Does it talk to agent-runs, memory or Bifrost?** No. It has no I/O. `trellis.runs`,
`trellis.memory` and `bifrost_sdk` send these records.

**Why does `RunRecord` accept a field `RunStart` refuses?** Records read back from a store or a
peer ignore unknown fields, so a newer service does not break an older reader. Records you
write refuse them, so your typo fails at your end.

**Where is the run store port?** There is none ([ADR 0004](adr/0004-no-run-store-port.md)).
The runs client is `trellis.runs.RunsClient`.

**Is there a type checker in CI?** No. The repo configures none, because the tests deliberately
pass wrong types to prove the models refuse them. Ruff and the tests run in CI.
