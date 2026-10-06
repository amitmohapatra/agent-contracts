"""04: the record lifecycle of a run that pauses for an approval.

RunStart -> RunRecord (RUNNING) -> Interrupt (PAUSED) -> InterruptResolution -> RunRecord
(RUNNING again) -> RunEvents -> Feedback. This is what agent-runs and the harness exchange;
here every step is a plain constructor or check. Pure.

    uv run python examples/04_pause_answer_resume.py
"""

from trellis.contracts import (
    AgentExecutionContext,
    AgentPaused,
    AgentRequest,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    RunEvent,
    RunEventType,
    RunOutcome,
    RunRecord,
    RunStart,
    RunStatus,
    ToolCall,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="buyer")
start = RunStart.from_request(AgentRequest.create(ctx, {"sku": "A-1"}))
record = RunRecord.from_start(start)  # RUNNING
events = [RunEvent.started(ctx, 0)]

# The agent wants to call a tool that needs a person's approval.
call = ToolCall(tool="erp.order", args={"sku": "A-1", "qty": 12})
events.append(RunEvent.tool(ctx, RunEventType.TOOL_CALL_START, "tc_1", 1, name=call.tool))
asked = Interrupt.from_paused(
    AgentPaused("Order 12 of A-1?"),
    context=ctx,
    reason=InterruptReason.APPROVAL,  # an APPROVAL must carry the tool call
    tool_call=call,
    assignee="role:procurement",
)
assert record.status.can_become(RunStatus.PAUSED)
record = RunRecord.model_validate(
    {
        **record.model_dump(),
        "status": RunStatus.PAUSED,
        "awaiting": asked,
        "checkpoint": {"step": 2},
    }
)
events.append(RunEvent.finished(ctx, RunOutcome.INTERRUPT, 2, interrupt=asked))

# A person answers. The resolution must name this interrupt and this run.
answer = InterruptResolution(
    interrupt_id=asked.interrupt_id,
    run_id=record.run_id,
    decision=InterruptDecision.APPROVE,
    reviewer="user:ada",
    comment="within budget",
)
assert answer.resolves(asked)

# Resumed: RUNNING again, one attempt more, the checkpoint handed back to the executor.
record = RunRecord.model_validate(
    {
        **record.model_dump(),
        "status": RunStatus.RUNNING,
        "awaiting": None,
        "last_resolution": answer,
        "attempt": record.attempt + 1,
    }
)
events.append(RunEvent.started(ctx, 0, resumed=True).model_copy(update={"attempt": 2}))

# What memory learns approval rules from: approve, reject or edit of a tool call.
feedback = answer.to_feedback(asked, ctx)
assert feedback is not None
print("feedback:", feedback.target_kind.value, feedback.verdict.value, feedback.reviewer)

record = RunRecord.model_validate(
    {
        **record.model_dump(),
        "status": RunStatus.SUCCESS,
        "output": {"po": "PO-7"},
        "checkpoint": None,
    }
)
events.append(RunEvent.finished(ctx, RunOutcome.SUCCESS, 1).model_copy(update={"attempt": 2}))

for event in events:
    print(f"attempt {event.attempt} #{event.sequence} {event.type.value} {event.outcome or ''}")
print("record:", record.status.value, "attempt", record.attempt, record.output)
