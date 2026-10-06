"""02: a run's record and the one transition check, ``RunStatus.can_become``.

``RunStart`` is what starting a run records; ``RunRecord`` is the run as agent-runs keeps it.
A store moves a record only where ``can_become`` allows. Pure: the "store" here is a dict.

    uv run python examples/02_run_lifecycle.py
"""

from pydantic import ValidationError

from trellis.contracts import (
    AgentExecutionContext,
    AgentRequest,
    RunRecord,
    RunStart,
    RunStatus,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="digest")
start = RunStart.from_request(AgentRequest.create(ctx, {"day": "2026-10-06"}))
assert start.run_id == ctx.agent_run_id  # the run is the context's run
assert start.idempotency_key  # a retried start returns the same run

# A run starts QUEUED (for a worker) or RUNNING (in process), never anywhere else.
record = RunRecord.from_start(start, status=RunStatus.QUEUED)
try:
    RunRecord.from_start(start, status=RunStatus.SUCCESS)
except ValueError as exc:
    print("refused:", exc)


def move(current: RunRecord, to: RunStatus) -> RunRecord:
    """What a run store does under its row lock: check, then write."""
    if not current.status.can_become(to):
        raise ValueError(f"{current.status.value} cannot become {to.value}")
    return RunRecord.model_validate({**current.model_dump(), "status": to})


path = [RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUCCESS]
for status in path:  # claimed, lease lapsed (or released), claimed again, finished
    record = move(record, status)
    print(f"-> {record.status.value:<9} final={record.final}")

try:
    move(record, RunStatus.RUNNING)  # a final status moves nowhere
except ValueError as exc:
    print("refused:", exc)

# The record checks itself: only a failed ending carries an error, only PAUSED an interrupt.
try:
    RunRecord.model_validate({**record.model_dump(), "status": RunStatus.PAUSED})
except ValidationError as exc:
    print("refused:", exc.errors()[0]["msg"])
