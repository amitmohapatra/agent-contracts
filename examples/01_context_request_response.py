"""01: one execution's identity, the request it is given and the response it returns.

The simplest use of the package: build an ``AgentExecutionContext`` once per execution, wrap
the input in an ``AgentRequest``, and answer with an ``AgentResponse``. Pure: no I/O.

    uv run python examples/01_context_request_response.py
"""

from trellis.contracts import (
    AgentExecutionContext,
    AgentRequest,
    AgentResponse,
    Claim,
    EvidenceRef,
)

ctx = AgentExecutionContext.create(
    tenant_id="acme",
    user_id="u1",
    agent_id="analyst",
    thread_id="chat-42",
    turn_id="t3",
    timeout_seconds=30,  # becomes an absolute deadline
)
request = AgentRequest.create(ctx, "Why did revenue fall?")
assert request.query == "Why did revenue fall?"  # the text a memory retrieval uses

response = AgentResponse.ok(
    "Revenue fell 4% on lower renewals.",
    claims=[Claim(claim_id="c1", text="Revenue fell 4%", evidence_ids=["chunk_1"])],
    evidence=[EvidenceRef(source_id="chunk_1", citation="Q3 report, p. 4")],
)
assert response.succeeded

# The Memory Service's scope keywords, made coherent: a turn with no session gets one.
scope = ctx.scope_fields()
assert scope["session_id"] == "chat-42-session"

# A nested agent keeps tenant, user, thread and trace, and never gets a later deadline.
child = ctx.for_agent("worker")
assert child.parent_agent_run_id == ctx.agent_run_id
assert child.deadline is not None and ctx.deadline is not None
assert child.deadline <= ctx.deadline

# A key a retry repeats: the same parts give the same key.
assert ctx.idempotency_key("refund", 91) == ctx.idempotency_key("refund", 91)

print("context:", ctx.log_fields()["agent_run_id"], "scope:", sorted(scope))
print("response:", response.status.value, response.data)
