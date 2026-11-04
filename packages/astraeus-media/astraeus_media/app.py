"""
MediaKit — the main entry point for astraeus-media.

Usage::

    from astraeus_media import MediaKit, MediakitConfig

    mk = MediaKit(config=MediakitConfig(
        bucket="my-bucket",
        endpoint_url="https://...",
        catalog_path="./media.db",
        api_key="secret",
        auth="apikey",
    ))

    # Mount in your Starlette app:
    app = Starlette(routes=[Mount("/media", app=mk.app)], lifespan=mk.lifespan)

    # Or compose lifespans with other plugins:
    @asynccontextmanager
    async def lifespan(app):
        async with cms.lifespan_context(app):
            async with mk.lifespan_context(app):
                yield
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from starlette.applications import Starlette

from astraeus_media.catalog.catalog import Catalog
from astraeus_media.config import MediakitConfig
from astraeus_media.storage.s3_compatible import S3CompatibleBackend

logger = structlog.get_logger(__name__)


class MediaKit:
    """
    Mountable Starlette MediaKit sub-application.

    :param config: A :class:`~astraeus_media.config.MediakitConfig` instance.
    """

    def __init__(self, *, config: MediakitConfig) -> None:
        self.config = config
        self._app: Starlette | None = None
        self._catalog: Catalog | None = None
        self._storage: S3CompatibleBackend | None = None
        self._derivative_storage: S3CompatibleBackend | None = None

    # ------------------------------------------------------------------
    # ASGI app
    # ------------------------------------------------------------------

    @property
    def app(self) -> Starlette:
        """Lazily build and return the Starlette sub-application."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def _build_app(self) -> Starlette:
        from starlette.routing import BaseRoute

        from astraeus_media.api.assets import make_asset_routes
        from astraeus_media.api.references import make_reference_routes
        from astraeus_media.api.upload import make_upload_routes
        from astraeus_media.routes.iiif import make_iiif_routes

        routes: list[BaseRoute] = [
            *make_upload_routes(self),
            *make_asset_routes(self),
            *make_reference_routes(self),
            *make_iiif_routes(self),
        ]

        try:
            from astraeus_media.admin import make_admin_routes

            routes.extend(make_admin_routes(self))
        except ImportError:
            pass  # jinja2/python-multipart not installed — admin UI not available

        return Starlette(routes=routes, lifespan=self.lifespan)

    # ------------------------------------------------------------------
    # Lifespan
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan_context(self, app):
        """Async context manager — initialise catalog and storage, then tear down.

        Suitable for composition with other plugin lifespans::

            @asynccontextmanager
            async def lifespan(app):
                async with mk.lifespan_context(app):
                    yield
        """
        from astraeus_media import __version__

        logger.info(
            "astraeus_media.startup",
            version=__version__,
            bucket=self.config.bucket,
            catalog_path=str(self.config.catalog_path),
        )
        self._catalog = Catalog(self.config.catalog_path)
        await self._catalog.initialize()
        self._storage = S3CompatibleBackend(self.config)
        try:
            yield
        finally:
            await self._catalog.close()
            logger.info("astraeus_media.shutdown")

    @asynccontextmanager
    async def lifespan(self, app):
        """Starlette-compatible lifespan for use with ``Starlette(lifespan=mk.lifespan)``."""
        async with self.lifespan_context(app):
            yield

    # ------------------------------------------------------------------
    # Convenience accessors (available after lifespan starts)
    # ------------------------------------------------------------------

    @property
    def catalog(self) -> Catalog:
        """The active :class:`~astraeus_media.catalog.catalog.Catalog` instance."""
        if self._catalog is None:
            raise RuntimeError("MediaKit catalog is not initialised — lifespan not started?")
        return self._catalog

    @property
    def storage(self) -> S3CompatibleBackend:
        """The active :class:`~astraeus_media.storage.s3_compatible.S3CompatibleBackend`."""
        if self._storage is None:
            raise RuntimeError("MediaKit storage is not initialised — lifespan not started?")
        return self._storage

    @property
    def derivative_storage(self) -> S3CompatibleBackend:
        """Where IIIF renditions live: ``derivatives_bucket`` if set, else :attr:`storage`."""
        if not self.config.derivatives_bucket:
            return self.storage
        if self._derivative_storage is None:
            config = self.config.model_copy(update={"bucket": self.config.derivatives_bucket})
            self._derivative_storage = S3CompatibleBackend(config)
        return self._derivative_storage

    async def derivative_url(self, key: str) -> str:
        """URL a reader is redirected to for the rendition stored at *key*."""
        if self.config.derivatives_public_url:
            return f"{self.config.derivatives_public_url.rstrip('/')}/{key}"
        return await self.derivative_storage.get_url(key, self.config.presign_expires)
