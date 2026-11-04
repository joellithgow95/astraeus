"""Starlette adapter — backwards-compatible ``create_media_mount()`` factory.

New code should use :class:`astraeus_media.MediaKit` directly::

    from astraeus_media import MediaKit, MediakitConfig

    mk = MediaKit(config=MediakitConfig(bucket="...", ...))
    app = Starlette(..., lifespan=mk.lifespan)
    app.mount("/media", app=mk.app)

The ``create_media_mount()`` helper remains for convenience::

    media = create_media_mount(bucket="my-bucket", ...)
    app.mount("/media", app=media)
"""

from __future__ import annotations

from starlette.applications import Starlette

from astraeus_media.app import MediaKit
from astraeus_media.config import MediakitConfig


def create_media_mount(
    bucket: str,
    *,
    endpoint_url: str | None = None,
    catalog_path: str = "./media_catalog.db",
    auth=None,
    **kwargs,
) -> Starlette:
    """
    Returns a Starlette ASGI app suitable for mounting at any path.

    Exposes ``lifespan_context(app)`` for composition with other plugin lifespans::

        @asynccontextmanager
        async def lifespan(app):
            async with cms.lifespan_context(app):
                async with media.lifespan_context(app):
                    yield

    .. deprecated::
        Prefer ``MediaKit(config=MediakitConfig(...))`` directly.
    """
    config = MediakitConfig(
        bucket=bucket,
        endpoint_url=endpoint_url,
        catalog_path=catalog_path,
        auth=auth,
        **kwargs,
    )
    mk = MediaKit(config=config)
    # Expose lifespan_context at the app level for external composition
    app = mk.app
    app.lifespan_context = mk.lifespan_context  # type: ignore[attr-defined]
    return app
