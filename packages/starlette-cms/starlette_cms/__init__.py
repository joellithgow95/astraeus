"""
starlette-cms — Headless CMS for Starlette.

A mountable Starlette sub-application providing a block registry,
document store, content API, schema introspection, and webhook system.

Quickstart::

    from starlette_cms import CMS, TextField, RichTextField, ImageField, ListField

    cms = CMS(database_url="sqlite:///content.db", auth="apikey", api_key="secret")

    @cms.block("hero")
    class HeroBlock:
        title: str = TextField(required=True, label="Headline")
        body: dict = RichTextField()

    @cms.document("page")
    class PageDocument:
        title: str = TextField(required=True)
        slug: str = TextField(required=True)
        body: list = ListField(blocks=[HeroBlock])

    # Mount in your Starlette app:
    # app.mount("/cms", app=cms.app)
"""

from starlette_cms.app import CMS
from starlette_cms.exceptions import (
    BlockNotFound,
    BlockRegistrationError,
    BlockTypeMismatch,
    DocumentNotFound,
    ImmutableDocumentError,
    ReferencedDocumentError,
    SingletonConflict,
)
from starlette_cms.fields import (
    BlockField,
    BoolField,
    DateField,
    DocumentRef,
    ImageField,
    JSONField,
    LinksField,
    ListField,
    NumberField,
    RichTextField,
    SelectField,
    TagField,
    TagsField,
    TextField,
    URLField,
)
from starlette_cms.media import MediaBackend
from starlette_cms.registry import block

__version__ = "0.5.0"

# Library contract: install NullHandler so the host app controls log routing.
# See ADR 017.
import logging as _logging
_logging.getLogger("starlette_cms").addHandler(_logging.NullHandler())

__all__ = [
    "CMS",
    "block",
    "TextField",
    "RichTextField",
    "ImageField",
    "ListField",
    "BlockField",
    "NumberField",
    "SelectField",
    "BoolField",
    "URLField",
    "JSONField",
    "TagsField",
    "TagField",
    "DateField",
    "LinksField",
    "DocumentRef",
    "MediaBackend",
    "BlockNotFound",
    "BlockRegistrationError",
    "BlockTypeMismatch",
    "ReferencedDocumentError",
    "DocumentNotFound",
    "SingletonConflict",
    "ImmutableDocumentError",
]
