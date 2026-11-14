"""Every field of every model says what it is.

The descriptions are the API documentation: agent-runs serves these models, so its OpenAPI
schema shows exactly what is written here. A field added without one fails this file rather
than reaching the docs as a bare name.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from trellis import contracts


def _models() -> list[type[BaseModel]]:
    """Every model defined in any ``trellis.contracts`` module, exported or not."""
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.iter_modules(contracts.__path__):
        module = importlib.import_module(f"{contracts.__name__}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [found[name] for name in sorted(found)]


MODELS = _models()


def test_every_exported_model_is_checked() -> None:
    exported = {
        obj
        for name in contracts.__all__
        if inspect.isclass(obj := getattr(contracts, name)) and issubclass(obj, BaseModel)
    }
    assert exported <= set(MODELS)
    assert len(MODELS) >= 30  # the walk found the package, not an empty directory


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_field_has_a_one_line_description(model: type[BaseModel]) -> None:
    missing = [
        name for name, field in model.model_fields.items() if not (field.description or "").strip()
    ]
    assert not missing, f"{model.__name__} fields without a description: {missing}"
    multiline = [
        name for name, field in model.model_fields.items() if "\n" in (field.description or "")
    ]
    assert not multiline, f"{model.__name__} descriptions over one line: {multiline}"


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_the_json_schema_carries_every_description(model: type[BaseModel]) -> None:
    """What an OpenAPI generator reads: the wire names (camelCase on an A2A card) included."""
    for by_alias in (True, False):
        properties = model.model_json_schema(by_alias=by_alias)["properties"]
        bare = sorted(name for name, schema in properties.items() if not schema.get("description"))
        assert not bare, f"{model.__name__} schema properties without a description: {bare}"


#: Each class diagram in docs/ARCHITECTURE.md labels a model ``<<frozen, closed>>``,
#: ``<<frozen, open>>``, ``<<frozen, ignores unknown>>`` and so on, and the page says what the
#: words mean: frozen refuses assignment, closed refuses unknown fields, open keeps them.
_ARCHITECTURE = Path(__file__).resolve().parents[1] / "docs" / "ARCHITECTURE.md"
_EXTRA = {"closed": "forbid", "open": "allow", "ignores unknown": "ignore"}


def _labelled_models() -> list[tuple[str, frozenset[str]]]:
    text = _ARCHITECTURE.read_text(encoding="utf-8")
    labelled = re.findall(r"class (\w+) \{\n\s*<<([^>]+)>>", text)
    return [
        (name, frozenset(word.strip() for word in label.split(",")))
        for name, label in labelled
        if label != "StrEnum"
    ]


LABELLED = _labelled_models()


def test_the_diagrams_label_the_records() -> None:
    assert len(LABELLED) >= 15  # the pattern found the diagrams, not nothing


@pytest.mark.parametrize(("name", "label"), LABELLED, ids=[name for name, _ in LABELLED])
def test_a_model_is_configured_as_its_diagram_labels_it(name: str, label: frozenset[str]) -> None:
    """A model whose diagram and code disagree fails here: a record that silently became
    mutable, or a written record that started accepting typos, is caught; so is a diagram
    left behind by a change."""
    model = getattr(contracts, name)
    assert issubclass(model, BaseModel), name
    assert bool(model.model_config.get("frozen")) == ("frozen" in label), name
    extra = [_EXTRA[word] for word in label if word in _EXTRA]
    # a label that names no extra policy means pydantic's default (ignore), as for AgentError
    assert (model.model_config.get("extra") or "ignore") == (extra[0] if extra else "ignore")
    assert bool(model.model_config.get("alias_generator")) == ("camelCase" in label), name
