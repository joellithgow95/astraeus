"""
MediaBackend protocol for starlette-cms.

Defines the interface that a media storage backend (e.g. astraeus-media) must
implement if ``ImageField`` key validation is desired at document save time.

Usage::

    from starlette_cms import CMS
    from starlette_cms.media import MediaBackend

    class MyBackend:
        async def confirm_exists(self, key: str) -> bool:
            # Check your media catalog / S3 bucket / etc.
            return True

    cms = CMS(
        database_url="sqlite:///content.db",
        auth="apikey",
        api_key="secret",
        media_backend=MyBackend(),
    )
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MediaBackend(Protocol):
    """
    Protocol that any media storage backend must satisfy.

    Implement ``confirm_exists`` to validate image keys before they are
    persisted by the CMS document API.
    """

    async def confirm_exists(self, key: str) -> bool:
        """
        Return ``True`` if *key* exists in the media catalog.

        :param key: The image/media key to check (e.g. a astraeus-media asset key
            or a plain URL string, depending on the backend).
        """
        ...
