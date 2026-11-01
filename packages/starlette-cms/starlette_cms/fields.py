"""
Field types for starlette-cms block and document definitions.

Each field type wraps a Pydantic FieldInfo with additional schema metadata
that is passed through to /api/schema under the cms:field_meta key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _BaseField:
    required: bool = False
    label: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    display_order: int | None = None
    group: str | None = None
    immutable: bool = False

    def field_meta(self) -> dict[str, Any]:
        meta = {
            k: v
            for k, v in {
                "label": self.label,
                "placeholder": self.placeholder,
                "help_text": self.help_text,
                "display_order": self.display_order,
                "group": self.group,
            }.items()
            if v is not None
        }
        if self.immutable:
            meta["immutable"] = True
        return meta


@dataclass
class TextField(_BaseField):
    """Short text input — the most common field type.

    Examples::

        title: str = TextField(required=True, label="Title", max_length=200)
        slug: str = TextField(required=True, unique_per_type=True)
    """

    max_length: int | None = None
    unique_per_type: bool = False


@dataclass
class RichTextField(_BaseField):
    """Stores ProseMirror document JSON."""

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "rich_text"
        return m


@dataclass
class ImageField(_BaseField):
    """Stores a media reference — URL string or Mediakit asset key."""

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "image"
        return m


@dataclass
class ListField(_BaseField):
    """
    A list of items. Pass a single block class for a homogeneous list,
    or a list of block classes for a polymorphic block list.

    Pass a ``DocumentRef`` for an ordered list of references (ADR 010): the
    list stores document IDs, each checked on write like a single ref, with
    no ID listed twice. ``on_delete`` works per entry: ``"nullify"`` drops
    the deleted ID from the list.

    Examples::

        cards: list = ListField(CardBlock)
        body: list = ListField(blocks=[HeroBlock, CardSectionBlock])
        songs: list = ListField(DocumentRef(block_type="spotify_like", on_delete="nullify"))
    """

    item_type: Any = None
    blocks: list[Any] = field(default_factory=list)

    def __init__(self, item_type: Any = None, *, blocks: list[Any] | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        if blocks is not None:
            self.blocks = blocks
            self.item_type = None
        else:
            self.item_type = item_type
            self.blocks = []

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        if isinstance(self.item_type, DocumentRef):
            m.update(self.item_type.ref_meta())
            m["field_type"] = "document_ref_list"
            return m
        m["field_type"] = "block_list"
        return m


@dataclass
class BlockField(_BaseField):
    """A single nested block."""

    block_type: Any = None

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "block"
        return m


@dataclass
class NumberField(_BaseField):
    """Numeric field — stored as float.

    Examples::

        price: float = NumberField(required=True, min_value=0.0, label="Price")
        score: float = NumberField(min_value=0.0, max_value=100.0, precision=2)
    """

    min_value: float | None = None
    max_value: float | None = None
    precision: int | None = None
    default: float | int | None = None

    def field_meta(self) -> dict[str, Any]:
        base = super().field_meta()
        extras = {
            "min_value": self.min_value,
            "max_value": self.max_value,
            "precision": self.precision,
        }
        return {**base, **{k: v for k, v in extras.items() if v is not None}}


@dataclass
class SelectField(_BaseField):
    """Single-select (or future multi-select) from a fixed list of choices.

    Examples::

        status: str = SelectField(choices=["draft", "published"], required=True)
        tier: str = SelectField(choices=["bronze", "silver", "gold"])
    """

    choices: list[str] = field(default_factory=list)
    multiple: bool = False

    def field_meta(self) -> dict[str, Any]:
        base = super().field_meta()
        extras: dict[str, Any] = {"choices": self.choices}
        if self.multiple:
            extras["multiple"] = self.multiple
        return {**base, **extras}


@dataclass
class BoolField(_BaseField):
    """Boolean field — always has a default (never None).

    Examples::

        active: bool = BoolField(default=True, label="Active")
        featured: bool = BoolField()
    """

    default: bool = False

    def field_meta(self) -> dict[str, Any]:
        base = super().field_meta()
        return {**base, "default": self.default}


@dataclass
class URLField(_BaseField):
    """URL string field. Stored as raw str in v1 — no URL validation enforced.

    Examples::

        website: str = URLField(required=True, label="Website URL")
        thumbnail: str = URLField(max_length=512)
    """

    max_length: int = 2048

    def field_meta(self) -> dict[str, Any]:
        base = super().field_meta()
        return {**base, "max_length": self.max_length, "format": "url"}


@dataclass
class JSONField(_BaseField):
    """Arbitrary JSON blob (dict or list). Always nullable — defaults to None.

    Pass an optional ``schema`` for editor tooling hints (not enforced in v1).

    Examples::

        metadata: dict | list | None = JSONField()
        config: dict | list | None = JSONField(schema={"type": "object"})
    """

    schema: dict | None = None

    def field_meta(self) -> dict[str, Any]:
        base = super().field_meta()
        if self.schema is not None:
            return {**base, "schema": self.schema}
        return base


@dataclass
class TagsField(_BaseField):
    """A list of short tag strings, edited as chips.

    Stored as ``list[str]``, the same shape a ``JSONField`` holding tags
    already has, so switching a field to it needs no migration. The editor
    offers tags already used on the field's document type
    (``GET /api/tags``), and lowercases and hyphenates what you type. The
    API does not rewrite tags on write: a gateway that owns ``tags`` hashes
    what it sent, and a stored value it did not send would rewrite the
    document on every sync.

    ``suggest_from`` offers the tags of another type instead, as on
    ``TagField``: a slice naming several link tags suggests from ``link``.

    Examples::

        tags: list | None = TagsField()
        tags: list = TagsField(suggest_from="link")
    """

    suggest_from: str | None = None
    suggest_field: str = "tags"

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "tags"
        if self.suggest_from:
            m["suggest_from"] = self.suggest_from
            m["suggest_field"] = self.suggest_field
        return m


@dataclass
class TagField(_BaseField):
    """One tag string, picked from the tags already used on another type.

    For a field that names a group of documents by tag, such as a post's
    "every link tagged developer-blog" slice. ``suggest_from`` is the document
    type whose tags the editor offers (``GET /api/tags``), and
    ``suggest_field`` the field on it (default ``tags``). As with
    ``TagsField``, the editor lowercases and hyphenates what you type and the
    API stores the value as written.

    Examples::

        tag: str = TagField(required=True, suggest_from="link")
    """

    suggest_from: str | None = None
    suggest_field: str = "tags"

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "tag"
        if self.suggest_from:
            m["suggest_from"] = self.suggest_from
            m["suggest_field"] = self.suggest_field
        return m


@dataclass
class DateField(_BaseField):
    """A calendar date, stored as a ``YYYY-MM-DD`` string.

    Checked on write: ``2026-02-30`` or ``Oct 1`` fails with a 422 instead of
    reaching a site build. The editor shows a date picker.

    Examples::

        from_date: str = DateField(required=True)
        date_added: str | None = DateField()
    """

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "date"
        return m


@dataclass
class LinksField(_BaseField):
    """A list of external links, each ``{url, title, description, date_added, tags}``.

    Only ``url`` is required unless ``required_keys`` names more of
    ``title``, ``description`` and ``date_added`` (for a site whose build
    rejects a link without them). Unknown keys on an item are kept, so data
    written by an older schema or a script survives an edit in the editor.

    Examples::

        links: list | None = LinksField(help_text="Links shown on the post's card")
        links: list | None = LinksField(required_keys=["title", "date_added"])
    """

    required_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = set(self.required_keys) - {"title", "description", "date_added"}
        if unknown:
            raise ValueError(f"LinksField.required_keys: unknown keys {sorted(unknown)}")

    def field_meta(self) -> dict[str, Any]:
        m = super().field_meta()
        m["field_type"] = "links"
        if self.required_keys:
            m["required_keys"] = list(self.required_keys)
        return m


@dataclass
class DocumentRef(_BaseField):
    """
    A typed foreign key to another document.

    Stores the target document's ID string. Validates on write that the
    referenced document exists and has the declared block_type.

    ``on_delete`` controls behaviour when the referenced document is deleted:
    - ``"block"``   — refuse to delete the target (default)
    - ``"nullify"`` — set this field to None in all referencing documents
    - ``"cascade"`` — delete all documents referencing the target (dangerous)

    ``label_field`` names the target's body field the editor shows in its
    picker, for a type with no ``title`` or ``name`` (``label_field="track"``).

    Example::

        submission_ref: str = DocumentRef(block_type="jewelry_item", immutable=True)
    """

    block_type: str | None = None
    on_delete: str = "block"
    label_field: str | None = None

    def ref_meta(self) -> dict[str, Any]:
        """The target-describing keys, shared with ``ListField(DocumentRef(...))``."""
        meta: dict[str, Any] = {"on_delete": self.on_delete}
        if self.block_type:
            meta["ref_block_type"] = self.block_type
        if self.label_field:
            meta["ref_label_field"] = self.label_field
        return meta

    def field_meta(self) -> dict[str, Any]:
        meta = super().field_meta()
        meta.update(self.ref_meta())
        meta["field_type"] = "document_ref"
        return meta
