"""08: failures as records: ``AgentError.of``, ``classify`` and the ``HarnessError`` family.

Any exception becomes a serialisable ``AgentError`` with a category, a source and whether a
retry may help. ``AgentPaused`` is not an error. Pure.

    uv run python examples/08_errors_and_classify.py
"""

from trellis.contracts import (
    AgentError,
    AgentPaused,
    ErrorCategory,
    HarnessError,
    PolicyDeniedError,
    ToolNotFoundError,
    classify,
)
from trellis.contracts.errors import is_pause_signal

assert classify(TimeoutError()) is ErrorCategory.TIMEOUT
assert classify(KeyError("sku")) is ErrorCategory.VALIDATION
assert classify(RuntimeError("?")) is ErrorCategory.UNKNOWN  # unknown is never retried

for exc, source in (
    (TimeoutError("model took too long"), "langgraph"),
    (PolicyDeniedError("refund over limit"), "tools"),
    (ToolNotFoundError("no tool erp.ordr"), "tools"),
    (ConnectionResetError("peer reset"), "mcp.refund"),  # a qualified source keeps its tool
):
    error = AgentError.of(exc, source=source)
    detail = error.details.get("source_detail", "")
    where = f"{error.source} {detail}".strip()
    print(f"{error.code:<20} {error.category.value:<11} retryable={error.retryable!s:<5} {where}")

kept = AgentError.of(ValueError("bad"), retryable=True)  # what the caller says wins
assert kept.retryable

paused = AgentPaused("Approve?")
assert is_pause_signal(paused) and not isinstance(paused, HarnessError)  # a pause is no failure
print("AgentError JSON keys:", sorted(AgentError.of(TimeoutError()).model_dump(mode="json")))
