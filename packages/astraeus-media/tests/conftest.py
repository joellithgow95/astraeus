"""Shared fixtures for astraeus-media tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from astraeus_media.app import MediaKit
from astraeus_media.catalog.catalog import Catalog
from astraeus_media.config import MediakitConfig
from astraeus_media.storage.backend import StorageBackend
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# FakeStorage — in-memory StorageBackend for HTTP endpoint tests
# ---------------------------------------------------------------------------


class FakeStorage:
    """In-memory StorageBackend that satisfies the protocol without S3.

    All keys are assumed to "exist" unless explicitly removed via
    ``remove(key)``.  ``prepare_upload`` returns predictable fake URLs.
    """

    def __init__(self, config: MediakitConfig) -> None:
        self.config = config
        self._deleted: set[str] = set()

    async def prepare_upload(self, key: str, content_type: str, expires_in: int = 900) -> dict:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
        return {
            "upload_url": f"https://fake-bucket.example.com/{key}?presigned=1",
            "key": key,
            "expires_at": expires_at,
        }

    async def confirm_exists(self, key: str) -> bool:
        return key not in self._deleted

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://fake-bucket.example.com/{key}"

    async def delete(self, key: str) -> None:
        self._deleted.add(key)

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        return []

    def remove(self, key: str) -> None:
        """Mark a key as absent (makes confirm_exists return False)."""
        self._deleted.add(key)


assert isinstance(FakeStorage(MediakitConfig(bucket="x")), StorageBackend), (
    "FakeStorage must satisfy StorageBackend protocol"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    return str(tmp_path / "test_media.db")


@pytest.fixture
def mk_config(tmp_db_path: str) -> MediakitConfig:
    return MediakitConfig(
        bucket="test-bucket",
        catalog_path=tmp_db_path,
        api_key="test-secret",
        auth="apikey",
    )


@pytest_asyncio.fixture
async def catalog(tmp_db_path: str) -> AsyncGenerator[Catalog, None]:
    """Initialized Catalog backed by a temp SQLite file."""
    cat = Catalog(tmp_db_path)
    await cat.initialize()
    yield cat
    await cat.close()


@pytest_asyncio.fixture
async def mk(mk_config: MediakitConfig) -> AsyncGenerator[MediaKit, None]:
    """Full MediaKit instance with FakeStorage injected."""
    instance = MediaKit(config=mk_config)
    # Initialise catalog manually (no need for a running Starlette server)
    instance._catalog = Catalog(mk_config.catalog_path)
    await instance._catalog.initialize()
    # Inject fake storage
    instance._storage = FakeStorage(mk_config)  # type: ignore[assignment]
    yield instance
    await instance._catalog.close()


@pytest_asyncio.fixture
async def client(mk: MediaKit) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient wired to the MediaKit ASGI app (no lifespan — state injected by mk)."""
    # Override the app's lifespan so our already-initialised mk is used
    app = mk._build_app()
    # Patch storage/catalog directly into routes by using the mk instance that
    # already has _catalog and _storage set (from the mk fixture above).
    # The route handlers call mk.catalog / mk.storage which use the injected instances.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c
