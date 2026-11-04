"""Tests for SQLiteSessionStore."""

from __future__ import annotations

import pytest
from starlette_chat.store import SQLiteSessionStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteSessionStore(str(tmp_path / "chat.db"))
    await s.init_db()
    return s


async def test_create_and_get_session(store):
    await store.create_session(
        session_id="sess-001",
        persona="default",
        doc_ref=None,
        model_name="claude-sonnet-4-5",
        temperature=0.7,
        max_tokens=4096,
        system_prompt="You are helpful.",
    )
    session = await store.get_session("sess-001")
    assert session is not None
    assert session["id"] == "sess-001"
    assert session["model_name"] == "claude-sonnet-4-5"
    assert session["system_prompt"] == "You are helpful."
    assert session["turn_count"] == 0


async def test_get_session_missing_returns_none(store):
    assert await store.get_session("nonexistent") is None


async def test_append_and_list_messages(store):
    await store.create_session(
        session_id="sess-002",
        persona="default",
        doc_ref=None,
        model_name="claude-sonnet-4-5",
        temperature=1.0,
        max_tokens=4096,
        system_prompt="",
    )
    await store.append_message("sess-002", "user", "Hello", 0)
    await store.append_message("sess-002", "assistant", "Hi there!", 1, model_used="claude-sonnet-4-5")

    messages = await store.list_messages("sess-002")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["model_used"] == "claude-sonnet-4-5"


async def test_get_turn_context(store):
    await store.create_session(
        session_id="sess-003",
        persona="support",
        doc_ref=None,
        model_name="gpt-4o",
        temperature=0.5,
        max_tokens=2048,
        system_prompt="Be concise.",
    )
    await store.append_message("sess-003", "user", "Question?", 0)

    ctx = await store.get_turn_context("sess-003")
    assert ctx is not None
    assert ctx.model_name == "gpt-4o"
    assert ctx.system_prompt == "Be concise."
    assert ctx.turn_index == 1
    assert ctx.history == [{"role": "user", "content": "Question?"}]


async def test_get_turn_context_missing_returns_none(store):
    assert await store.get_turn_context("nonexistent") is None


async def test_update_turn_count(store):
    await store.create_session(
        session_id="sess-004",
        persona="default",
        doc_ref=None,
        model_name="claude-sonnet-4-5",
        temperature=1.0,
        max_tokens=4096,
        system_prompt="",
    )
    await store.update_turn_count("sess-004", 5)
    session = await store.get_session("sess-004")
    assert session["turn_count"] == 5


async def test_append_message_returns_id(store):
    await store.create_session(
        session_id="sess-005",
        persona="default",
        doc_ref=None,
        model_name="claude-sonnet-4-5",
        temperature=1.0,
        max_tokens=4096,
        system_prompt="",
    )
    msg_id = await store.append_message("sess-005", "user", "Test", 0)
    assert msg_id.startswith("msg-")
    assert len(msg_id) == 4 + 16  # "msg-" + 16 chars


async def test_init_db_idempotent(tmp_path):
    """Calling init_db() twice should not raise."""
    s = SQLiteSessionStore(str(tmp_path / "idem.db"))
    await s.init_db()
    await s.init_db()  # should be fine — CREATE TABLE IF NOT EXISTS
