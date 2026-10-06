# Examples

Nine scripts, simplest first. Each is pure: no network, no service, no environment variable.
Each asserts what it shows, so a script that exits 0 is a passing check.

```bash
make examples                                   # all of them, as CI does
uv run python examples/04_pause_answer_resume.py  # one
```

| # | Script | What it shows | Read with |
|---|---|---|---|
| 01 | [01_context_request_response.py](01_context_request_response.py) | `AgentExecutionContext`, `AgentRequest`, `AgentResponse`; `scope_fields()`, `for_agent`, `idempotency_key` | [Quickstart](../README.md#quickstart) |
| 02 | [02_run_lifecycle.py](02_run_lifecycle.py) | `RunStart`, `RunRecord.from_start`, every move checked by `RunStatus.can_become`, what a record refuses | [A run's lifecycle](../docs/ARCHITECTURE.md#a-runs-lifecycle) |
| 03 | [03_tool_call_and_outcome.py](03_tool_call_and_outcome.py) | `ToolSpec` (and its `side_effects`), `ToolCall`, `ToolOutcome`, `ToolStatus` | [api.md: tool](../docs/api.md#tool) |
| 04 | [04_pause_answer_resume.py](04_pause_answer_resume.py) | The record lifecycle: `RunStart` → `RunRecord` → `Interrupt` → `InterruptResolution` → `RunEvent`s → `Feedback` | [The records across a run](../docs/ARCHITECTURE.md#the-records-across-a-run) |
| 05 | [05_choices_forms_and_answers.py](05_choices_forms_and_answers.py) | Labelled `Option`s, several picks, a `REVIEW` form with `expects` and `ui_schema`, the asker's own `component` | [What a pause carries](../docs/ARCHITECTURE.md#what-a-pause-carries) |
| 06 | [06_schedule_spec.py](06_schedule_spec.py) | `ScheduleSpec`, `Schedule.from_spec`, and what a fired run copies (priority, concurrency key, metadata) | [ADR 0007](../docs/adr/0007-schedules-carry-queue-order-and-metadata.md) |
| 07 | [07_agent_card_a2a.py](07_agent_card_a2a.py) | `AgentDescriptor` → `AgentCard` → A2A JSON, and reading a foreign card | [api.md: descriptors and a2a](../docs/api.md#descriptors-and-a2a) |
| 08 | [08_errors_and_classify.py](08_errors_and_classify.py) | `AgentError.of`, `classify`, the `HarnessError` family, `AgentPaused` is no error | [Errors](../docs/ARCHITECTURE.md#errors) |
| 09 | [09_wire_conventions.py](09_wire_conventions.py) | Strict when written, lenient when read, frozen, every field documented | [Wire conventions](../docs/ARCHITECTURE.md#wire-conventions) |

The same records sent to real services: agent-runs' examples
([agent-runs/examples](https://github.com/amitmohapatra/agent-runs/tree/main/examples)) start,
pause and resume runs over HTTP with `trellis.runs`.
