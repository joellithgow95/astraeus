# starlette-cms

Headless CMS for Starlette — block registry, document API, webhooks, and schema versioning.

Part of the [Astraeus](https://github.com/ASneakyToast/astraeus) governed data platform.

## Install

```bash
pip install starlette-cms
```

## Quickstart

```python
from starlette_cms import CMS, TextField, RichTextField, ImageField, ListField
from starlette.applications import Starlette
from starlette.routing import Mount

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

app = Starlette(routes=[Mount("/cms", app=cms.app)], lifespan=cms.lifespan)
```

This gives you:

- `GET/POST /cms/api/documents` — list and create documents
- `GET/PATCH/DELETE /cms/api/documents/{id}` — get, update, delete
- `POST /cms/api/documents/{id}/publish` — publish with webhook delivery
- `GET /cms/api/schema` — JSON Schema introspection with editor hints
- `GET /cms/api/tags?type=<doc type>[&field=<name or a.b path>]` — distinct tags in use, most used first

## Field types

| Field | Type | Use case |
|---|---|---|
| `TextField` | `str` | Short text, titles, slugs |
| `RichTextField` | `dict` | ProseMirror document JSON |
| `ImageField` | `str` | Media key or URL |
| `NumberField` | `float` | Numeric values with bounds |
| `SelectField` | `Literal[...]` | Fixed choice list |
| `BoolField` | `bool` | Toggles |
| `URLField` | `str` | URLs |
| `JSONField` | `dict \| list \| None` | Arbitrary JSON |
| `TagsField` | `list[str]` | Tags, edited as chips with type-ahead |
| `TagField` | `str` | One tag, suggested from the tags on another type (`suggest_from="link"`), e.g. a "links tagged X" slice |
| `DateField` | `str` | A calendar date as `YYYY-MM-DD`, checked on write (a 422 for `2026-02-30`); a date picker in the editor |
| `LinksField` | `list[LinkItem]` | External links `{url, title, description, date_added, tags}`; unknown keys kept; `required_keys=` makes more than `url` required |
| `DocumentRef` | `str` | Foreign key to another document |
| `ListField` | `list[...]` | Ordered lists of blocks, or of document IDs with `ListField(DocumentRef(...))` |
| `BlockField` | model | Single nested block |

All fields support `required`, `label`, `help_text`, `immutable`, and other common parameters.

## Singletons

For configuration or settings where only one version should be active at a time:

```python
@cms.block("site_config", singleton=True)
class SiteConfig:
    site_name: str = TextField(required=True)
    maintenance_mode: bool = BoolField(default=False)
```

- `GET /api/documents/singleton/site_config` — get active config
- `POST /api/documents/singleton/site_config` — publish new version (archives the old one)
- `GET /api/documents/singleton/site_config/history` — version history

## Document references

`DocumentRef` provides typed foreign keys with referential integrity:

```python
@cms.block("review")
class Review:
    submission_ref: str = DocumentRef(block_type="submission", on_delete="block")
    score: float = NumberField(required=True)
```

A list of references is `ListField(DocumentRef(...))`. It stores IDs in order; on write each must
exist and have the declared type, and none may repeat. `on_delete` applies per entry
(`"nullify"` drops the deleted ID from the list). References inside blocks
(`ListField(blocks=[...])`, `BlockField`) are checked the same way, so a block can hold picked refs.
`label_field=` names the target's field the editor shows (`DocumentRef(block_type="spotify_like", label_field="track")`).

```python
@cms.document("blog_post")
class Post:
    see_also: list | None = ListField(DocumentRef(block_type="definition", on_delete="nullify"))
```

Reading: `GET /api/documents?type=blog_post&resolve_refs=see_also` adds `see_also__resolved`, the
referenced documents in stored order (one query per field). `?ids=a,b` fetches documents by ID, and
`?q=` matches a case-insensitive substring of the slug or any top-level text field.

## Testing utilities

```python
from starlette_cms.testing import validate_block, BlockTestCase

result = validate_block(HeroBlock, {"title": "Hello"})
assert result.title == "Hello"
```

`BlockTestCase` provides `assert_valid`, `assert_invalid`, `assert_field_required`, `assert_roundtrip`, and more.

## Schema versioning

The CMS tracks schema versions and blocks startup on mismatch. Write migrations with `@cms.migration()` and apply them via the CLI:

```bash
cms migrate status --app myapp:cms
cms migrate run --app myapp:cms
cms validate --app myapp:cms
```

## Webhooks

Register URLs to receive POST notifications on document events:

```bash
curl -X POST http://localhost:8000/cms/api/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "https://api.netlify.com/build_hooks/abc", "events": ["document.published"]}'
```

## Documentation

Full documentation: [asneakytoast.github.io/astraeus](https://asneakytoast.github.io/astraeus/)

## License

MIT
