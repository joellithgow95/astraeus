# astraeus-media

Media management for Starlette — S3-compatible storage, IIIF Image API 3.0, presigned upload flow, and an admin UI.

Part of the [Astraeus](https://github.com/ASneakyToast/astraeus) content stack. Called `mediakit` until November 2026: the import is now `astraeus_media`, the CLI `astraeus-media`, and the classes (`MediaKit`, `MediakitConfig`) and URLs are unchanged.

## Install

```bash
pip install astraeus-media            # core only
pip install astraeus-media[admin]     # + admin UI (Jinja2 templates)
pip install astraeus-media[mcp]       # + MCP server for agent tool use
pip install astraeus-media[full]      # everything
```

## Quickstart

```python
from astraeus_media.adapters.starlette import create_media_mount

media = create_media_mount(
    bucket="my-bucket",
    endpoint_url="https://storage.googleapis.com",  # GCS, R2, or None for AWS S3
    catalog_path="./media.db",
    auth=lambda request: request.user.is_authenticated,
)

app = Starlette(routes=[Mount("/media", app=media)], lifespan=media.lifespan)
# Admin at: /media/admin
# IIIF at:  /media/iiif/{key}/full/800,/0/default.webp
```

## Status

Pre-release. Spec complete, implementation in progress.
