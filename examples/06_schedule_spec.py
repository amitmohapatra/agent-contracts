"""06: a schedule as a caller writes it (``ScheduleSpec``) and as agent-runs keeps it.

``timeout_seconds``, ``agent_version``, ``priority`` and ``concurrency_key`` are copied into
every run the schedule fires, and ``metadata`` into the run's metadata under the fire's own
keys (ADR 0007). The copy below mirrors agent-runs' ``firing.py``, which does it for real.
Pure.

    uv run python examples/06_schedule_spec.py
"""

from trellis.contracts import RunStart, Schedule, ScheduleSpec

spec = ScheduleSpec(
    tenant_id="acme",
    agent_id="digest",
    name="Weekday digest",
    cadence="0 8 * * 1-5",
    timezone="Europe/Berlin",
    on_behalf_of="user:u1",
    input={"team": "sales"},
    timeout_seconds=600,
    priority=-10,  # waits behind runs people started
    concurrency_key="digest",  # never two at once
    metadata={"without": ["memory_push"]},
)
schedule = Schedule.from_spec(spec, created_by="user:u1")
print("schedule:", schedule.schedule_id, schedule.cadence, schedule.timezone)

fire_time = "2026-10-06T06:00:00+00:00"
fired = RunStart(
    tenant_id=spec.tenant_id,
    agent_id=spec.agent_id,
    on_behalf_of=spec.on_behalf_of,
    input=spec.input,
    timeout_seconds=spec.timeout_seconds,
    agent_version=spec.agent_version,
    priority=spec.priority,
    concurrency_key=spec.concurrency_key,
    metadata={  # the fire's own keys win on conflict
        **spec.metadata,
        "schedule_id": schedule.schedule_id,
        "schedule_name": schedule.name,
        "fire_time": fire_time,
        "created_by": schedule.created_by,
    },
)
assert (fired.priority, fired.concurrency_key) == (-10, "digest")
print("fired run:", fired.run_id, fired.metadata)

try:
    ScheduleSpec(**{**spec.model_dump(), "timezone": "Mars/Base"})
except ValueError as exc:
    print("refused:", str(exc).splitlines()[0])
