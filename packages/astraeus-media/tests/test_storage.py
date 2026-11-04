"""Unit tests for S3CompatibleBackend using obstore MemoryStore.

MemoryStore does not support sign_async (signing requires a real S3/GCS store),
so we test confirm_exists, list_objects, and delete via a monkeypatched _get_store.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from astraeus_media.config import MediakitConfig
from astraeus_media.storage.s3_compatible import S3CompatibleBackend


@pytest.fixture
def config() -> MediakitConfig:
    return MediakitConfig(bucket="test-bucket", catalog_path=":memory:")


@pytest_asyncio.fixture
async def backend_with_memory_store(config: MediakitConfig):
    """S3CompatibleBackend with _store replaced by obstore MemoryStore."""
    import obstore.store as obs

    backend = S3CompatibleBackend(config)
    backend._store = obs.MemoryStore()
    return backend


@pytest.mark.asyncio
async def test_confirm_exists_missing(backend_with_memory_store: S3CompatibleBackend) -> None:
    """confirm_exists returns False for a key that has not been uploaded."""
    exists = await backend_with_memory_store.confirm_exists("originals/abc/photo.webp")
    assert exists is False


@pytest.mark.asyncio
async def test_confirm_exists_after_put(backend_with_memory_store: S3CompatibleBackend) -> None:
    """confirm_exists returns True after putting an object into MemoryStore."""
    import obstore

    store = backend_with_memory_store._get_store()
    await obstore.put_async(store, "originals/abc/photo.webp", b"fake bytes")

    exists = await backend_with_memory_store.confirm_exists("originals/abc/photo.webp")
    assert exists is True


@pytest.mark.asyncio
async def test_list_objects_empty(backend_with_memory_store: S3CompatibleBackend) -> None:
    """list_objects returns [] when the store is empty."""
    results = await backend_with_memory_store.list_objects()
    assert results == []


@pytest.mark.asyncio
async def test_list_objects_with_items(backend_with_memory_store: S3CompatibleBackend) -> None:
    """list_objects returns one entry per object in the store."""
    import obstore

    store = backend_with_memory_store._get_store()
    await obstore.put_async(store, "originals/aaa/img1.webp", b"data1")
    await obstore.put_async(store, "originals/bbb/img2.webp", b"data2")

    results = await backend_with_memory_store.list_objects()
    assert len(results) == 2
    keys = {r["key"] for r in results}
    assert "originals/aaa/img1.webp" in keys
    assert "originals/bbb/img2.webp" in keys


@pytest.mark.asyncio
async def test_list_objects_prefix_filter(backend_with_memory_store: S3CompatibleBackend) -> None:
    """list_objects with a prefix only returns matching keys."""
    import obstore

    store = backend_with_memory_store._get_store()
    await obstore.put_async(store, "originals/aaa/img.webp", b"data")
    await obstore.put_async(store, "derivatives/aaa/img_thumb.webp", b"thumb")

    results = await backend_with_memory_store.list_objects(prefix="originals/")
    assert len(results) == 1
    assert results[0]["key"] == "originals/aaa/img.webp"


@pytest.mark.asyncio
async def test_list_objects_max_keys(backend_with_memory_store: S3CompatibleBackend) -> None:
    """list_objects respects max_keys limit."""
    import obstore

    store = backend_with_memory_store._get_store()
    for i in range(5):
        await obstore.put_async(store, f"originals/{i:03d}/img.webp", b"data")

    results = await backend_with_memory_store.list_objects(max_keys=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_delete(backend_with_memory_store: S3CompatibleBackend) -> None:
    """delete removes the object; subsequent confirm_exists returns False."""
    import obstore

    store = backend_with_memory_store._get_store()
    key = "originals/del/photo.webp"
    await obstore.put_async(store, key, b"payload")

    assert await backend_with_memory_store.confirm_exists(key) is True

    await backend_with_memory_store.delete(key)

    assert await backend_with_memory_store.confirm_exists(key) is False


@pytest.mark.asyncio
async def test_get_url_public_read() -> None:
    """get_url returns a plain public URL when public_read=True."""
    config = MediakitConfig(
        bucket="my-bucket",
        public_read=True,
        endpoint_url="https://cdn.example.com",
        catalog_path=":memory:",
    )
    backend = S3CompatibleBackend(config)
    url = await backend.get_url("originals/abc/photo.webp")
    assert url == "https://cdn.example.com/originals/abc/photo.webp"


@pytest.mark.asyncio
async def test_get_url_public_read_no_endpoint() -> None:
    """get_url with public_read=True and no endpoint constructs AWS S3 URL."""
    config = MediakitConfig(
        bucket="my-bucket",
        public_read=True,
        catalog_path=":memory:",
    )
    backend = S3CompatibleBackend(config)
    url = await backend.get_url("originals/abc/photo.webp")
    assert url == "https://my-bucket.s3.amazonaws.com/originals/abc/photo.webp"
