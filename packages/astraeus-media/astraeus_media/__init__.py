"""
astraeus-media — Media management for Starlette.

S3-compatible object storage, local SQLite catalog, presigned upload flow,
asset CRUD API, and optional admin UI.

Quickstart::

    from astraeus_media import MediaKit, MediakitConfig

    mk = MediaKit(config=MediakitConfig(
        bucket="my-bucket",
        endpoint_url="https://storage.googleapis.com",
        catalog_path="./media.db",
        api_key="secret",
        auth="apikey",
    ))

    # Mount in your Starlette app:
    app = Starlette(routes=[Mount("/media", app=mk.app)], lifespan=mk.lifespan)
"""

from astraeus_media.app import MediaKit
from astraeus_media.config import MediakitConfig
from astraeus_media.storage.backend import StorageBackend

__version__ = "0.2.0"

# Library contract: install NullHandler so the host app controls log routing.
# See ADR 017.
import logging as _logging

_logging.getLogger("astraeus_media").addHandler(_logging.NullHandler())

__all__ = ["MediaKit", "MediakitConfig", "StorageBackend"]
