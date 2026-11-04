# Field Types

Every block and document in Astraeus is defined using **field types** — dataclass-based descriptors that control validation, schema generation, and editor hints.

## Overview

| Field | Pydantic type | Use case |
|---|---|---|
| `TextField` | `str` | Short text, titles, slugs |
| `RichTextField` | `dict` | ProseMirror document JSON |
| `ImageField` | `str` | Media key or URL |
| `NumberField` | `float` | Numeric values with optional bounds |
| `SelectField` | `Literal[...]` | Choice from a fixed list |
| `BoolField` | `bool` | Toggles, flags |
| `URLField` | `str` | URLs |
| `JSONField` | `dict \| list \| None` | Arbitrary JSON blobs |
| `DocumentRef` | `str` | Foreign key to another document |
| `ListField` | `list[...]` | Ordered list of items or blocks |
| `BlockField` | model class | Single nested block |

## Common parameters

All field types share these base parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `required` | `bool` | `False` | If `True`, the field must be present and non-null |
| `label` | `str \| None` | `None` | Human-readable label for editors |
| `placeholder` | `str \| None` | `None` | Placeholder text hint |
| `help_text` | `str \| None` | `None` | Description shown below the field in editors |
| `display_order` | `int \| None` | `None` | Controls field ordering in editor UIs |
| `group` | `str \| None` | `None` | Groups fields into sections |
| `immutable` | `bool` | `False` | If `True`, the field cannot be changed after creation |

These are emitted into the JSON Schema under `cms:field_meta` and are available via `GET /api/schema/{block_type}`.

## TextField

Short text input. The most common field type.

```python
@cms.block("article")
class ArticleBlock:
    title: str = TextField(required=True, label="Title", max_length=200)
    slug: str = TextField(required=True, unique_per_type=True)
    subtitle: str = TextField(placeholder="Optional subtitle")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_length` | `int \| None` | `None` | Maximum character length |
| `unique_per_type` | `bool` | `False` | Enforce uniqueness within the block type |

When `required=False` (the default), the Pydantic type is `str | None` with a default of `None`.

## RichTextField

Stores [ProseMirror document JSON](https://prosemirror.net/docs/guide/#doc) — not HTML, not Markdown.

```python
@cms.block("text")
class TextBlock:
    body: dict = RichTextField(required=True)
```

The stored value is a ProseMirror document tree:

```json
{
  "type": "doc",
  "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "Hello, world."}]}
  ]
}
```

Consumers (e.g. an Astro frontend) are responsible for rendering this to HTML. Standard libraries exist for this in JavaScript.

## ImageField

Stores a media reference — either a URL or a astraeus-media asset key.

```python
@cms.block("hero")
class HeroBlock:
    background: str = ImageField(required=True, label="Background image")
    thumbnail: str = ImageField()
```

If a `MediaBackend` is configured on the CMS, `ImageField` values are validated against the media catalog on create and update. See [Media Integration](media.md).

## NumberField

Numeric field stored as a float. Supports bounds and decimal precision.

```python
@cms.block("pricing")
class PricingBlock:
    price: float = NumberField(required=True, min_value=0.0, label="Price ($)")
    discount: float = NumberField(min_value=0.0, max_value=100.0, precision=2)
    quantity: float = NumberField(default=1)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_value` | `float \| None` | `None` | Minimum allowed value |
| `max_value` | `float \| None` | `None` | Maximum allowed value |
| `precision` | `int \| None` | `None` | Decimal precision hint (for editors) |
| `default` | `float \| int \| None` | `None` | Default value |

## SelectField

Constrains input to a fixed list of choices. Generates a Pydantic `Literal` type.

```python
@cms.block("task")
class TaskBlock:
    status: str = SelectField(
        choices=["todo", "in_progress", "done"],
        required=True,
        label="Status",
    )
    priority: str = SelectField(choices=["low", "medium", "high"])
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `choices` | `list[str]` | `[]` | The allowed values |
| `multiple` | `bool` | `False` | Reserved for future multi-select support |

## BoolField

Boolean toggle. Always has a default — never nullable.

```python
@cms.block("feature")
class FeatureBlock:
    enabled: bool = BoolField(default=True, label="Enabled")
    featured: bool = BoolField()  # defaults to False
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `default` | `bool` | `False` | The default value |

## URLField

URL string field. No URL validation is enforced in v1 — the value is stored as a raw string. The `format: url` hint is included in `cms:field_meta` for editors.

```python
@cms.block("link")
class LinkBlock:
    url: str = URLField(required=True, label="URL")
    icon_url: str = URLField(max_length=512)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_length` | `int` | `2048` | Maximum character length |

## JSONField

Arbitrary JSON data (dict or list). Always nullable — defaults to `None`.

```python
@cms.block("test_case")
class TestCaseBlock:
    input_payload: dict | list | None = JSONField(label="Input JSON")
    expected: dict | list | None = JSONField(
        schema={"type": "object"},
        help_text="Expected output shape",
    )
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `schema` | `dict \| None` | `None` | JSON Schema hint for editors (not enforced in v1) |

## DocumentRef

A typed foreign key to another document. See the dedicated [Document References](document-references.md) guide.

```python
@cms.block("review")
class ReviewBlock:
    submission_ref: str = DocumentRef(
        block_type="submission",
        on_delete="block",
        immutable=True,
        label="Submission",
    )
    score: float = NumberField(required=True, min_value=0.0, max_value=100.0)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `block_type` | `str \| None` | `None` | Required block type of the referenced document |
| `on_delete` | `str` | `"block"` | `"block"`, `"nullify"`, or `"cascade"` |

## ListField

Ordered list of items. Two modes: homogeneous (single type) and polymorphic (multiple block types).

```python
# Homogeneous — all items are the same type
tags: list = ListField(str)

# Polymorphic — items can be any of the listed block types
body: list = ListField(blocks=[HeroBlock, TextBlock, ImageBlock])
```

Polymorphic lists use Pydantic discriminated unions. Each item must include a `block_type` field that matches one of the registered block types:

```json
[
  {"block_type": "hero", "title": "Welcome"},
  {"block_type": "text", "body": {"type": "doc", "content": []}}
]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `item_type` | `Any` | `None` | Type for homogeneous lists (positional arg) |
| `blocks` | `list[Any]` | `[]` | Block classes for polymorphic lists |

## BlockField

A single nested block (not a list).

```python
@cms.block("page")
class PageBlock:
    hero: HeroBlock = BlockField(block_type=HeroBlock, required=True)
    sidebar: SidebarBlock = BlockField(block_type=SidebarBlock)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `block_type` | `Any` | `None` | The block class to nest |

## Immutable fields

Any field can be marked `immutable=True`. Once a document is created, immutable fields cannot be changed via PATCH — the CMS silently strips them from update payloads.

```python
@cms.block("submission")
class SubmissionBlock:
    applicant_name: str = TextField(required=True, immutable=True)
    status: str = SelectField(choices=["pending", "approved", "rejected"])
```

Immutability is recorded in the JSON Schema (`cms:field_meta.immutable: true`) so editors can disable the field after creation.

## Field metadata in the schema API

Every field emits its metadata under `cms:field_meta` in the JSON Schema, available via `GET /api/schema/{block_type}`:

```json
{
  "properties": {
    "title": {
      "type": "string",
      "cms:field_meta": {
        "label": "Title",
        "placeholder": "Enter a title",
        "help_text": "The document title",
        "display_order": 1,
        "group": "Content"
      }
    }
  }
}
```

The schema endpoint also hoists `field_meta` into a top-level key for easier editor consumption.
