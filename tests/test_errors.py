"""The pause contract: how an agent asks a person something.

`test_contracts.py` covers the request/response types; this file covers the one exception
that is deliberately not an error.
"""

from __future__ import annotations

import pytest

from trellis.contracts import AgentPaused
from trellis.contracts.errors import ErrorCategory, HarnessError, classify, is_pause_signal

# --------------------------------------------------------------- pausing for a human


def test_an_agent_can_pause_without_being_a_langgraph_graph() -> None:
    """The framework-agnostic pause.

    Pause detection used to match four LangGraph class names and nothing else, so an
    ordinary async agent could only stop a run by failing it — or by naming its own
    exception ``GraphInterrupt`` and hoping. Agents built on this platform rather than on
    LangGraph could not do human-in-the-loop at all.
    """
    paused = AgentPaused("Approve a EUR 240 refund for order 91?")
    assert is_pause_signal(paused)
    assert str(paused) == "Approve a EUR 240 refund for order 91?"


def test_a_pause_is_not_an_error() -> None:
    """Inheriting from HarnessError would classify it, count it in the error rate and hand
    it to on_agent_error — which is exactly what the pause handling exists to avoid."""
    assert not issubclass(AgentPaused, HarnessError)


def test_a_pause_carries_the_question_to_whoever_has_to_answer_it() -> None:
    """awaiting() is what the run store keeps and the webhook delivers, so a UI can render
    the question without the harness knowing anything about the UI."""
    paused = AgentPaused(
        "Approve a EUR 240 refund for order 91?",
        expects={"type": "boolean"},
        payload={"order_id": 91, "amount_eur": 240},
    )
    assert paused.awaiting() == {
        "question": "Approve a EUR 240 refund for order 91?",
        "expects": {"type": "boolean"},
        "payload": {"order_id": 91, "amount_eur": 240},
    }
    # Absent extras are left out rather than sent as nulls a UI has to special-case.
    assert AgentPaused("Continue?").awaiting() == {"question": "Continue?"}


def test_a_pause_with_no_question_is_refused() -> None:
    """A run that stops with nothing to show anyone is a hang, not a pause."""
    for empty in ("", "   "):
        with pytest.raises(ValueError, match="needs a question"):
            AgentPaused(empty)


def test_the_langgraph_signals_are_still_recognised() -> None:
    """Matched by class name across the hierarchy, so langgraph stays un-imported."""

    class GraphBubbleUp(Exception):
        pass

    class GraphInterrupt(GraphBubbleUp):
        pass

    class ParentCommand(GraphBubbleUp):
        pass

    for cls in (GraphBubbleUp, GraphInterrupt, ParentCommand):
        assert is_pause_signal(cls("suspended")), cls.__name__
    assert not is_pause_signal(RuntimeError("a real failure"))


def test_memory_sdk_errors_are_classified_by_module_and_name():
    """``trellis.memory`` exceptions are recognised without importing the SDK: a class of
    the right name from the ``trellis`` namespace maps, the same name elsewhere does not."""
    sdk_error = type("RateLimitedError", (Exception,), {"__module__": "trellis.memory.errors"})
    assert classify(sdk_error("slow down")) is ErrorCategory.RATE_LIMIT
    elsewhere = type("RateLimitedError", (Exception,), {"__module__": "somewhere.else"})
    assert classify(elsewhere("slow down")) is ErrorCategory.UNKNOWN
