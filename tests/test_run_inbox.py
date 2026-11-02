"""A run's inbox (``RunMessage``), a person's pause (``InterruptReason.PAUSE``) and what a run
has used so far (``RunUsage``, ``RunRecord.usage``): the format agent-runs keeps and the
harness reads at each step."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trellis.contracts import (
    Interrupt,
    InterruptReason,
    RunMessage,
    RunMessageKind,
    RunRecord,
    RunUsage,
)


def _message(**changes: object) -> RunMessage:
    fields: dict[str, object] = {
        "message_id": "msg_1",
        "tenant_id": "acme",
        "run_id": "run_1",
        "kind": RunMessageKind.INSTRUCTION,
        "text": "also check the March figures",
        "sender": "user:ada",
        "position": 1,
    }
    return RunMessage.model_validate({**fields, **changes})


def test_an_instruction_carries_its_text_and_where_it_is_in_the_inbox() -> None:
    message = _message()
    assert message.kind is RunMessageKind.INSTRUCTION
    assert message.position == 1
    assert message.created_at.tzinfo is not None
    assert RunMessage.model_validate_json(message.model_dump_json()) == message


def test_an_instruction_without_text_is_refused() -> None:
    with pytest.raises(ValidationError, match="instruction"):
        _message(text="  ")


def test_a_pause_needs_no_text() -> None:
    assert _message(kind="PAUSE", text=None).kind is RunMessageKind.PAUSE


def test_positions_start_at_one_and_a_message_is_immutable() -> None:
    with pytest.raises(ValidationError):
        _message(position=0)
    with pytest.raises(ValidationError):
        _message(colour="red")
    with pytest.raises(ValidationError):
        _message().text = "changed"  # type: ignore[misc]


def test_a_person_pausing_a_run_is_an_interrupt_of_its_own() -> None:
    paused = Interrupt(
        interrupt_id="int_1",
        tenant_id="acme",
        run_id="run_1",
        reason=InterruptReason.PAUSE,
        question="Paused by ada: resume to continue.",
    )
    assert paused.reason.value == "PAUSE"


def test_usage_counts_from_zero_and_never_below() -> None:
    usage = RunUsage()
    assert usage.model_dump() == {
        "model_calls": 0,
        "tool_calls": 0,
        "commands": 0,
        "tokens": 0,
        "cached_tokens": 0,
        "cost": 0.0,
    }
    with pytest.raises(ValidationError):
        RunUsage(tool_calls=-1)


def test_a_record_carries_its_usage_and_an_older_record_reads_without_it() -> None:
    record = RunRecord(
        run_id="run_1",
        tenant_id="acme",
        agent_id="research",
        usage=RunUsage(model_calls=3, tool_calls=2, commands=1, tokens=900, cost=0.002),
    )
    assert record.usage is not None and record.usage.commands == 1
    again = RunRecord.model_validate_json(record.model_dump_json())
    assert again.usage == record.usage
    older = RunRecord.model_validate({"run_id": "run_2", "tenant_id": "acme", "agent_id": "a"})
    assert older.usage is None
