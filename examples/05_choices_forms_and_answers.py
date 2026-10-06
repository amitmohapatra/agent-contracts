"""05: richer interrupts: labelled options, several picks, a form, the asker's own screen.

An answer carries an option's ``value``, never its label. With ``multiple=True`` it is a list
of distinct values. A ``REVIEW`` says in ``expects`` what a correction looks like. Pure.

    uv run python examples/05_choices_forms_and_answers.py
"""

from pydantic import ValidationError

from trellis.contracts import (
    AgentExecutionContext,
    AgentPaused,
    Interrupt,
    InterruptDecision,
    InterruptReason,
    InterruptResolution,
    Option,
)

ctx = AgentExecutionContext.create(tenant_id="acme", user_id="u1", agent_id="payables")

which = Interrupt.from_paused(
    AgentPaused("Which invoices may be paid today?"),
    context=ctx,
    reason=InterruptReason.CHOICE,
    ui="choice",
    options=["INV-1", Option(value="INV-2", label="Globex, EUR 9,800", description="due today")],
    multiple=True,
)
assert which.option_values == ["INV-1", "INV-2"]
print("options:", which.awaiting()["options"])

review = Interrupt.from_paused(
    AgentPaused(
        "Check the extracted address",
        expects={"type": "object", "properties": {"street": {"type": "string"}}},
    ),
    context=ctx,
    reason=InterruptReason.REVIEW,  # a REVIEW says in expects what a correction looks like
    ui="form",
    ui_schema={"street": {"ui:autofocus": True}},
)

own_screen = Interrupt.from_paused(
    AgentPaused("Approve this refund"),
    context=ctx,
    reason=InterruptReason.QUESTION,
    component="refund-review",  # a surface that has it renders it with props as they are
    props={"order_id": 91},
)

picked = InterruptResolution(
    interrupt_id=which.interrupt_id,
    run_id=which.run_id,
    decision=InterruptDecision.ANSWER,
    answer=["INV-2"],
)
assert picked.resolves(which)
assert picked.to_feedback(which, ctx) is None  # only tool-call decisions become feedback

for bad in (
    {"reason": InterruptReason.CHOICE},  # a CHOICE carries options
    {"reason": InterruptReason.QUESTION, "props": {"a": 1}},  # props need a component
):
    try:
        Interrupt(tenant_id="acme", run_id="run_x", question="?", **bad)
    except ValidationError as exc:
        print("refused:", exc.errors()[0]["msg"])

print("review ui:", review.ui, "| own screen:", own_screen.component)
