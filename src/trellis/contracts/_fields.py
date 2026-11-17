"""A model's values, as a rebuild of it reads them."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def fields_of(model: BaseModel) -> dict[str, Any]:
    """Every value of ``model``, its extra ones too: what ``dict(model)`` gives, read from the
    instance at once (``dict()`` walks pydantic's iterator, about fifty times slower, and a run
    moves its record several times)."""
    return {**model.__dict__, **(model.__pydantic_extra__ or {})}
