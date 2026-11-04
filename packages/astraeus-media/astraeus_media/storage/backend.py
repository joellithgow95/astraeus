"""StorageBackend protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    async def prepare_upload(
        self,
        key: str,
        content_type: str,
        expires_in: int = 900,
    ) -> dict:
        """Returns { "upload_url": str, "key": str, "expires_at": str }"""
        ...

    async def confirm_exists(self, key: str) -> bool:
        """Returns True if the object exists in the bucket."""
        ...

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Returns a presigned or public URL for the given key."""
        ...

    async def delete(self, key: str) -> None:
        """Deletes the object from the bucket."""
        ...

    async def list_objects(
        self,
        prefix: str = "",
        max_keys: int = 1000,
    ) -> list[dict]:
        """Returns [{ "key": str, "size": int, "last_modified": str }, ...]"""
        ...
