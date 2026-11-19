"""A model's values, as a rebuild of it reads them."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def fields_of(model: BaseModel) -> dict[str, Any]:
    """Every value of ``model``, its extra ones too: what ``dict(model)`` gives (its fields and
    extras, nothing else an instance may hold), read from the instance at once (``dict()``
    walks pydantic's iterator, about fifty times slower, and a run moves its record several
    times)."""
    values = model.__dict__
    own = {name: values[name] for name in type(model).model_fields if name in values}
    return {**own, **(model.__pydantic_extra__ or {})}
