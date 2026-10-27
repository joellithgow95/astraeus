"""
Finding the DocumentRef values inside a document body.

A reference can sit in three places (ADR 010, ADR 026):

- a ``DocumentRef`` field, holding one id;
- a ``ListField(DocumentRef(...))`` field, holding an ordered list of ids;
- either of those inside a block, at any depth: a ``ListField(blocks=[...])``,
  a homogeneous ``ListField(SomeBlock)`` or a ``BlockField(SomeBlock)``.

``iter_ref_sites`` walks a body against its model and yields one ``RefSite``
per reference field it meets, so write validation, delete integrity and
anything else that cares about refs share one definition of where they are.

Example::

    for site in iter_ref_sites(doc_model, body):
        print(site.path, site.ids())
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from starlette_cms.fields import DocumentRef


@dataclass
class RefSite:
    """One reference field found in a body.

    ``container[key]`` is the stored value: an id for a single ref, a list of
    ids for a list. ``path`` names it for error messages (``data[0].refs``).
    """

    container: dict[str, Any]
    key: str
    ref: DocumentRef
    many: bool
    path: str

    @property
    def value(self) -> Any:
        return self.container.get(self.key)

    def ids(self) -> list[str]:
        """The string ids stored here; anything else is left to ``check_shape``."""
        value = self.value
        if self.many:
            return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
        return [value] if isinstance(value, str) else []

    def check_shape(self) -> str | None:
        """An error message if the stored value is not a ref (or a list of distinct refs)."""
        value = self.value
        if value is None:
            return None
        if not self.many:
            return (
                None if isinstance(value, str) else f"{self.path}: DocumentRef must be a string ID"
            )
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"{self.path}: must be a list of document IDs"
        seen: set[str] = set()
        for v in value:
            if v in seen:
                return f"{self.path}: document {v!r} is listed more than once"
            seen.add(v)
        return None

    def remove(self, doc_id: str) -> None:
        """Drop ``doc_id``: a single ref becomes None, a list loses that entry."""
        if self.many:
            self.container[self.key] = [v for v in self.value or [] if v != doc_id]
        else:
            self.container[self.key] = None


def _block_models(model: Any) -> dict[str, list[Any]]:
    return getattr(model, "__block_fields__", {})


def _model_for(models: list[Any], item: dict[str, Any]) -> Any | None:
    """The block model an item belongs to: by its ``block_type``, or the only one there is."""
    if len(models) == 1:
        return models[0]
    block_type = item.get("block_type")
    return next((m for m in models if getattr(m, "__block_type__", None) == block_type), None)


def iter_ref_sites(model: Any, body: dict[str, Any], path: str = "") -> Iterator[RefSite]:
    """Yield every reference field in ``body``, descending into nested blocks.

    Fields missing from ``body`` are skipped, so a partial (PATCH) body walks
    only what it carries.
    """
    if not isinstance(body, dict):
        return
    for name, ref in getattr(model, "__ref_fields__", {}).items():
        if name in body:
            yield RefSite(body, name, ref, False, f"{path}{name}")
    for name, ref in getattr(model, "__ref_list_fields__", {}).items():
        if name in body:
            yield RefSite(body, name, ref, True, f"{path}{name}")
    for name, models in _block_models(model).items():
        value = body.get(name)
        if isinstance(value, dict):
            if (sub := _model_for(models, value)) is not None:
                yield from iter_ref_sites(sub, value, f"{path}{name}.")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict) and (sub := _model_for(models, item)) is not None:
                    yield from iter_ref_sites(sub, item, f"{path}{name}[{i}].")


def refs_to(model: Any, doc_type: str, _seen: set[int] | None = None) -> list[DocumentRef]:
    """The reference descriptors in ``model`` (nested blocks included) that target ``doc_type``.

    Lets a delete skip every type that cannot point at the document.
    """
    seen = _seen if _seen is not None else set()
    if id(model) in seen:
        return []
    seen.add(id(model))
    found = [
        ref
        for ref in [
            *getattr(model, "__ref_fields__", {}).values(),
            *getattr(model, "__ref_list_fields__", {}).values(),
        ]
        if ref.block_type == doc_type
    ]
    for models in _block_models(model).values():
        for sub in models:
            found.extend(refs_to(sub, doc_type, seen))
    return found
