# ADR 0006: Interrupts v2 (labelled options, several picks, own screens), decision scope, and queue order

**Status:** accepted, amended by [ADR 0007](0007-schedules-carry-queue-order-and-metadata.md) · **Date:** 2026-10-05 · **Amends:** [ADR 0002](0002-contracts-v3.md),
[ADR 0005](0005-run-working-time-and-agent-version.md)

## Context
An `Interrupt` could offer only plain strings as options, and its answer was one of them. A
reviewer sees what the agent wrote (`"eu-central-1"`), not what a person reads ("Europe
(Frankfurt)"), and cannot pick several ("which of these five invoices may be paid?"). An
application with its own review screen (a refund page, a diff viewer with its own controls)
had no way to say "render mine": `ui` names one of five generic controls, and `payload` is
data for them. A form built from `expects` could not say which widget a field wants.

A decision carried no words. A reviewer who approves "this once, but not over EUR 500" has
nowhere to say so, and the feedback memory learns from never sees it. Nor could a reviewer
say "stop asking me about this tool for the rest of this run": every similar call paused
again.

Runs on the queue were claimed oldest first, with no way to put an urgent run ahead or to
say "never two runs of this conversation at once" (a second message in a thread must wait
for the first run, or both write the same thread).

A schedule's runs could not be bounded: `RunStart.timeout_seconds` and `agent_version`
(ADR 0005) existed, but a fire builds its `RunStart` from the schedule alone, which had
neither.

## Decision
- **`Option(value, label=None, description=None)`.** `Interrupt.options` is
  `list[str | Option]`: a plain string is an option whose value is that string, shown as it
  is, and stays a plain string on the wire, so every interrupt written before reads and
  writes the same. Values are distinct (an answer could not tell two apart) and not blank.
  `Interrupt.option_values` lists them in display order. An answer carries the value, never
  the label.
- **`Interrupt.multiple`** (default `False`): several may be picked, and the answer is a
  list of distinct option values (or, without options, whatever `expects` describes, which
  must then be given). The contract states the rule; `trellis.runs.answers` checks an answer
  against it.
- **`Interrupt.ui_schema`**: widget hints for the form `expects` describes, in the
  react-jsonschema-form `uiSchema` convention, which the common form renderers read as is.
  The contract never interprets it.
- **`Interrupt.component` and `props`**: the name of the asker's own screen and the data
  for it. A surface that has a screen by that name renders it with `props` as they are;
  any other surface renders `ui`, which stays the fallback. `props` needs a `component`.
  A component needs no `expects` (the screen knows what it collects), but when `expects` is
  given an answer must still fit it: the asker's screen is not trusted to have checked.
- **`InterruptResolution.comment`** (optional, at most 4000 characters): the reviewer's
  remark, on any decision. `to_feedback` carries it into `Feedback.comment`.
- **`InterruptResolution.remember`**: `"once"` (default: the decision covers this call) or
  `"run"` (approve calls like this one for the rest of the run without asking). Only an
  `APPROVE` may be remembered, refused otherwise; what "like this one" means, and keeping
  the promise, is the harness's (it sees the calls); the run store only keeps the record.
- **`RunStart.priority`** (`-1000` to `1000`, default `0`): among a tenant's queued runs,
  higher is claimed first, then the oldest. **`RunStart.concurrency_key`** (optional): runs
  of one tenant sharing a key run only a few at a time (the run store's limit, one unless
  its operator says otherwise); the rest wait `QUEUED`. Both are kept on `RunRecord`.
- **`ScheduleSpec.timeout_seconds` and `agent_version`** (optional, as on `RunStart`): copied
  into the `RunStart` of every fire, so a scheduled run is bounded and says which code set
  it up.
- Version **0.6.0**: new names (`Option`, `InterruptRemember`) and new fields on closed
  models. Every new field is optional with a default that keeps the old behaviour.

## Consequences
- The closed models refuse unknown fields (ADR 0001), so a run store on 0.5.x refuses an
  interrupt, a resolution, a start or a schedule that sets a new field, and even the new
  defaults when a client sends every field (as `RunStart.model_dump()` does). agent-runs
  moves to 0.6 before any caller does; consumers pin `trellis-contracts<0.7`.
- `Interrupt.awaiting()` and a dumped `InterruptResolution` now also carry `multiple` and
  `remember` (their defaults included, as `options` always was). A reader that compares the
  whole JSON of an interrupt or a resolution sees two more keys.
- Fair share between tenants, the per-key limit and the claim order are enforced by
  agent-runs, where the queue is; the contract only names what a run asks for.
