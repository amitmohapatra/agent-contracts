"""03: describing a tool, one call of it, and the call's normalised outcome.

``ToolSpec.side_effects`` is what governance decides from; ``ToolCall`` is what an approval
interrupt carries; ``ToolOutcome`` is how the call ended. Pure.

    uv run python examples/03_tool_call_and_outcome.py
"""

from trellis.contracts import (
    AgentExecutionContext,
    ToolCall,
    ToolOutcome,
    ToolSpec,
    ToolStatus,
)

spec = ToolSpec(
    name="refund",
    description="Refund an order",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "integer"}, "amount_eur": {"type": "number"}},
        "required": ["order_id", "amount_eur"],
    },
    side_effects="irreversible",  # read, write or irreversible
)
print("memory-service descriptor:", spec.descriptor()["name"])

ctx = AgentExecutionContext.create(tenant_id="acme", agent_id="support")
call = ToolCall(
    tool=spec.name,
    args={"order_id": 91, "amount_eur": 240},
    idempotency_key=ctx.idempotency_key("refund", 91),  # a retry runs it once
)

ok = ToolOutcome(tool=call.tool, output={"refund_id": "rf_1"}, latency_ms=41.0)
assert ok.ok and ok.status == "ok"  # ToolStatus is a StrEnum

failed = ToolOutcome(
    tool=call.tool, status=ToolStatus.TIMEOUT, attempts=3, error_class="ReadTimeout"
)
failed.output_summary = "gave up after three attempts"  # assignments are validated
assert not failed.ok

for outcome in (ok, failed):
    print(f"{outcome.tool}: {outcome.status.value} after {outcome.attempts} attempt(s)")
