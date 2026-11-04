"""
Tests for starlette-chat block registration (Phase CH-1).

Verification matrix:
- All four block types register successfully on a fresh CMS instance
- ChatMessageBlock is append_only — PATCH returns 405
- ChatSessionBlock is NOT append_only — PATCH returns 200
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_chat import register_blocks
from starlette_chat.blocks import (
    ChatMessageBlock,
    ChatSessionBlock,
    ModelConfigBlock,
    SystemPromptBlock,
)
from starlette_cms import CMS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chat_cms() -> AsyncGenerator[CMS, None]:
    """CMS instance with all four starlette-chat block types registered."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        instance = CMS(
            database_url=f"sqlite:///{db_path}",
            auth="none",
            read_auth=False,
        )
        register_blocks(instance)

        async with instance.lifespan_context(None):
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest_asyncio.fixture
async def chat_client(chat_cms: CMS) -> AsyncGenerator[httpx.AsyncClient, None]:
    """httpx.AsyncClient targeting chat_cms."""
    app = Starlette(routes=[Mount("/", app=chat_cms.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Block registration
# ---------------------------------------------------------------------------


def test_all_blocks_register(chat_cms: CMS) -> None:
    """All four block types should appear in the CMS registry after register_blocks()."""
    names = chat_cms.registry.names()
    assert "chat_session" in names, f"chat_session missing from registry; got {names}"
    assert "chat_message" in names, f"chat_message missing from registry; got {names}"
    assert "system_prompt" in names, f"system_prompt missing from registry; got {names}"
    assert "model_config" in names, f"model_config missing from registry; got {names}"


def test_chat_message_append_only_flag(chat_cms: CMS) -> None:
    """ChatMessageBlock must be registered with append_only=True."""
    assert chat_cms.registry.is_append_only("chat_message") is True


def test_chat_session_not_append_only_flag(chat_cms: CMS) -> None:
    """ChatSessionBlock must NOT be registered as append_only."""
    assert chat_cms.registry.is_append_only("chat_session") is False


def test_system_prompt_not_append_only_flag(chat_cms: CMS) -> None:
    assert chat_cms.registry.is_append_only("system_prompt") is False


def test_model_config_not_append_only_flag(chat_cms: CMS) -> None:
    assert chat_cms.registry.is_append_only("model_config") is False


# ---------------------------------------------------------------------------
# HTTP: ChatMessage is append_only — PATCH returns 405
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_message_is_append_only(chat_client: httpx.AsyncClient) -> None:
    """Create a ChatMessage, then PATCH must return 405."""
    create_resp = await chat_client.post(
        "/api/documents",
        json={
            "doc_type": "chat_message",
            "body": {
                "content": "Hello from the user",
                "turn_index": 0,
                "role": "user",
            },
        },
    )
    assert create_resp.status_code == 201, (
        f"Expected 201 creating chat_message, got {create_resp.status_code}: "
        f"{create_resp.text}"
    )
    doc_id = create_resp.json()["id"]

    patch_resp = await chat_client.patch(
        f"/api/documents/{doc_id}",
        json={"body": {"content": "Trying to mutate an immutable message"}},
    )
    assert patch_resp.status_code == 405, (
        f"Expected 405 patching append_only chat_message, got {patch_resp.status_code}: "
        f"{patch_resp.text}"
    )


# ---------------------------------------------------------------------------
# HTTP: ChatSession is NOT append_only — PATCH returns 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_session_is_not_append_only(chat_client: httpx.AsyncClient) -> None:
    """Create a ChatSession, then PATCH must return 200."""
    create_resp = await chat_client.post(
        "/api/documents",
        json={
            "doc_type": "chat_session",
            "body": {
                "persona": "default",
            },
        },
    )
    assert create_resp.status_code == 201, (
        f"Expected 201 creating chat_session, got {create_resp.status_code}: "
        f"{create_resp.text}"
    )
    doc_id = create_resp.json()["id"]

    patch_resp = await chat_client.patch(
        f"/api/documents/{doc_id}",
        json={"body": {"title": "Updated session title"}},
    )
    assert patch_resp.status_code == 200, (
        f"Expected 200 patching chat_session, got {patch_resp.status_code}: "
        f"{patch_resp.text}"
    )
    assert patch_resp.json()["body"]["title"] == "Updated session title"
