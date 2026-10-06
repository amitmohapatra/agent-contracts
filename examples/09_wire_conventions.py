"""09: strict where written, lenient where read, and a JSON round trip.

What a producer writes refuses unknown fields and is frozen; what is read back from a store
or a peer ignores unknown fields, so a newer peer does not break an older reader. Pure.

    uv run python examples/09_wire_conventions.py
"""

from pydantic import ValidationError

from trellis.contracts import RunRecord, RunStart

start = RunStart(tenant_id="acme", agent_id="digest", input={"day": "mon"})

try:
    RunStart(tenant_id="acme", agent_id="digest", prority=5)  # a typo fails at the sender
except ValidationError as exc:
    print("refused:", exc.errors()[0]["type"], exc.errors()[0]["loc"])

try:
    start.priority = 5  # type: ignore[misc]
except ValidationError:
    print("refused: written records are frozen")

wire = RunRecord.from_start(start).model_dump_json()
newer = wire[:-1] + ', "a_field_from_a_newer_service": 1}'
read = RunRecord.model_validate_json(newer)  # ignored, not refused
assert read == RunRecord.model_validate_json(wire)

schema = RunStart.model_json_schema()
undocumented = [n for n, p in schema["properties"].items() if not p.get("description")]
assert not undocumented  # every field is documented; agent-runs' OpenAPI shows each
print("round trip ok;", len(schema["properties"]), "documented RunStart fields")
