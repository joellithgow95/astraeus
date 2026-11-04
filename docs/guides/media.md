# Media Integration

starlette-cms integrates with media backends through the `MediaBackend` protocol. When configured, `ImageField` values are validated against the media catalog on create and update.

## The MediaBackend protocol

`MediaBackend` is a `@runtime_checkable` Protocol with a single method:

```python
from starlette_cms import MediaBackend

class MediaBackend(Protocol):
    async def confirm_exists(self, key: str) -> bool:
        """Return True if the key exists in the media catalog."""
        ...
```

Any object that implements `confirm_exists` can be used as a media backend.

## Configuring a backend

Pass a `MediaBackend` implementation to the CMS constructor:

```python
from starlette_cms import CMS

cms = CMS(
    database_url="sqlite:///content.db",
    media_backend=my_media_backend,
)
```

## How validation works

When a `MediaBackend` is configured:

1. On `POST /api/documents` or `PATCH /api/documents/{id}`, the CMS scans the body for `ImageField` values
2. For each non-null image value, it calls `media_backend.confirm_exists(key)`
3. If any key doesn't exist, the request returns `422`:

```json
{
  "error": "Image key not found",
  "field": "background_image"
}
```

Without a configured backend, `ImageField` accepts any string value.

## Using with astraeus-media

[astraeus-media](https://github.com/ASneakyToast/astraeus) is the companion media management package in the Astraeus stack. It provides S3-compatible storage, IIIF Image API, and a presigned upload flow.

astraeus-media is a separate package (`pip install astraeus-media`) and is currently in development. When available, it will implement the `MediaBackend` protocol so `ImageField` values are validated against the astraeus-media catalog.

## Example

```python
@cms.block("hero")
class HeroBlock:
    title: str = TextField(required=True)
    background: str = ImageField(required=True, label="Background image")
    thumbnail: str = ImageField(label="Thumbnail")
```

Without a media backend, `background` and `thumbnail` accept any string (URL, asset key, etc.). With a backend configured, the CMS verifies that the given keys exist in the media catalog before saving.
