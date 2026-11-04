"""
Backend tests for Phase CH-3.

Tests:
1. test_create_session             — POST /api/chat/sessions returns 201 + session_id
2. test_create_session_no_config   — works even when no ModelConfig / SystemPrompt docs exist
3. test_ws_streams_tokens          — WS turn emits thinking, token, done in order
4. test_ws_edit_document_tool      — provider emits tool_use(edit_document), applying_edits sent
5. test_tool_search_documents      — dispatch search_documents hits GET /api/documents
6. test_tool_no_change             — diff_docs returns [] → no_change result, no WS opened
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr
from starlette_chat.app import ChatAPI
from starlette_chat.providers.base import BaseProvider
from starlette_chat.tools import ToolDispatcher

# ---------------------------------------------------------------------------
# Mock provider helpers
# ---------------------------------------------------------------------------


class MockChatModel(BaseChatModel):
    """BaseChatModel returning preset AIMessage responses in call order."""

    responses: list[AIMessage]
    _call_idx: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools: list, **kwargs: Any) -> MockChatModel:
        """No-op tool binding — MockChatModel ignores bound tools."""
        return self

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = self.responses[self._call_idx]
        self._call_idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        """Yield the next preset response as a single chunk for streaming."""
        msg = self.responses[self._call_idx]
        self._call_idx += 1
        chunk = AIMessageChunk(
            content=msg.content if isinstance(msg.content, str) else "",
            tool_calls=getattr(msg, "tool_calls", []),
        )
        yield ChatGenerationChunk(message=chunk)


class MockProvider(BaseProvider):
    """Provider wrapping a ``MockChatModel`` for use in tests.

    ``responses`` is passed through to ``MockChatModel`` so that tests can
    control what the model returns turn by turn.
    """

    def __init__(self, responses: list[AIMessage] | None = None) -> None:
        self._responses = responses or [AIMessage(content="")]

    def get_model(self) -> BaseChatModel:
        return MockChatModel(responses=self._responses)


# ---------------------------------------------------------------------------
# CMS mock helpers
# ---------------------------------------------------------------------------

CMS_BASE = "http://cms.local"
API_KEY = "test-api-key"


def _mock_empty_list() -> dict:
    return {"items": []}


def _session_doc(session_id: str, persona: str = "default") -> dict:
    return {
        "id": session_id,
        "doc_type": "chat_session",
        "body": {
            "persona": persona,
            "doc_ref": None,
            "model_config_ref": None,
            "prompt_ref": None,
            "turn_count": 0,
        },
    }


def _chat_message_doc(msg_id: str, role: str, content: str, turn_index: int) -> dict:
    return {
        "id": msg_id,
        "doc_type": "chat_message",
        "body": {
            "session_ref": "sess-1",
            "role": role,
            "content": content,
            "turn_index": turn_index,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_api() -> ChatAPI:
    return ChatAPI(
        cms_base_url=CMS_BASE,
        cms_api_key=API_KEY,
        provider=MockProvider(),
    )


@pytest_asyncio.fixture
async def chat_client(chat_api: ChatAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=chat_api.app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# 1. test_create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session(chat_api: ChatAPI) -> None:
    """POST /api/chat/sessions returns 201 with session_id; CMS create + publish called."""
    with respx.mock(base_url=CMS_BASE) as mock:
        # No ModelConfig or SystemPrompt
        mock.get("/api/documents").mock(return_value=httpx.Response(200, json=[]))
        # Create session doc
        mock.post("/api/documents").mock(
            return_value=httpx.Response(201, json={"id": "sess-abc", "doc_type": "chat_session"})
        )
        # Publish session
        mock.post("/api/documents/sess-abc/publish").mock(
            return_value=httpx.Response(200, json={"id": "sess-abc"})
        )

        async with httpx.AsyncClient(
            transport=ASGITransport(app=chat_api.app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {API_KEY}"},
        ) as client:
            resp = await client.post(
                "/api/chat/sessions",
                json={"persona": "default"},
            )

    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"] == "sess-abc"
    assert "doc_id" in body


# ---------------------------------------------------------------------------
# 2. test_create_session_no_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_no_config(chat_api: ChatAPI) -> None:
    """Session creation succeeds even when no ModelConfig / SystemPrompt documents exist."""
    with respx.mock(base_url=CMS_BASE) as mock:
        mock.get("/api/documents").mock(return_value=httpx.Response(200, json=[]))
        mock.post("/api/documents").mock(
            return_value=httpx.Response(201, json={"id": "sess-xyz", "doc_type": "chat_session"})
        )
        mock.post("/api/documents/sess-xyz/publish").mock(
            return_value=httpx.Response(200, json={"id": "sess-xyz"})
        )

        async with httpx.AsyncClient(
            transport=ASGITransport(app=chat_api.app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {API_KEY}"},
        ) as client:
            resp = await client.post(
                "/api/chat/sessions",
                json={"persona": "coding", "doc_id": None},
            )

    assert resp.status_code == 201
    assert resp.json()["session_id"] == "sess-xyz"


# ---------------------------------------------------------------------------
# 3. test_ws_streams_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_streams_tokens() -> None:
    """WS: connect, send message, receive thinking + token + done events in order."""
    model = MockChatModel(responses=[AIMessage(content="Hello there!")])
    provider = MockProvider()
    provider.get_model = lambda: model  # type: ignore[method-assign]
    api = ChatAPI(cms_base_url=CMS_BASE, cms_api_key=API_KEY, provider=provider)

    with respx.mock(base_url=CMS_BASE) as mock:
        # Session load
        mock.get("/api/documents/sess-1").mock(
            return_value=httpx.Response(200, json=_session_doc("sess-1"))
        )
        # Persist user + assistant messages
        mock.post("/api/documents").mock(
            return_value=httpx.Response(201, json={"id": "msg-1"})
        )
        # Session update (turn_count patch)
        mock.patch("/api/documents/sess-1").mock(
            return_value=httpx.Response(200, json=_session_doc("sess-1"))
        )

        from starlette.testclient import TestClient

        client = TestClient(api.app, raise_server_exceptions=True)
        with client.websocket_connect(
            f"/api/chat/sessions/sess-1/ws?api_key={API_KEY}"
        ) as ws:
            # First message should be "connected"
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == "sess-1"

            # Send a user message
            ws.send_json({"type": "message", "content": "Hi Claude"})

            events = []
            while True:
                msg = ws.receive_json()
                events.append(msg)
                if msg["type"] == "done":
                    break

    types = [e["type"] for e in events]
    assert "thinking" in types
    assert "token" in types
    assert types[-1] == "done"


# ---------------------------------------------------------------------------
# 4. test_ws_edit_document_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_edit_document_tool() -> None:
    """WS: LangGraph calls edit_document tool → applying_edits event sent to browser."""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as lc_tool

    # AIMessage with a tool_call so the graph routes to the tools node
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "edit_document",
            "args": {
                "markdown_content": "# Hello\n\nWorld.",
                "edit_rationale": "test edit",
                "scope": "full",
            },
            "id": "tc_001",
            "type": "tool_call",
        }],
    )
    # Second LLM response: plain text after tool result
    final_msg = AIMessage(content="Done.")

    model = MockChatModel(responses=[tool_call_msg, final_msg])
    provider = MockProvider()
    provider.get_model = lambda: model  # type: ignore[method-assign]
    api = ChatAPI(cms_base_url=CMS_BASE, cms_api_key=API_KEY, provider=provider)

    with respx.mock(base_url=CMS_BASE) as mock:
        mock.get("/api/documents/sess-2").mock(
            return_value=httpx.Response(200, json=_session_doc("sess-2"))
        )
        mock.post("/api/documents").mock(
            return_value=httpx.Response(201, json={"id": "msg-2"})
        )
        mock.patch("/api/documents/sess-2").mock(
            return_value=httpx.Response(200, json=_session_doc("sess-2"))
        )

        # Patch the ToolDispatcher._edit_document to avoid actual WS call
        with patch.object(
            ToolDispatcher,
            "_edit_document",
            new=AsyncMock(
                return_value={"status": "accepted", "step_count": 2, "edit_rationale": "test edit"}
            ),
        ):
            from starlette.testclient import TestClient

            client = TestClient(api.app, raise_server_exceptions=True)
            with client.websocket_connect(
                f"/api/chat/sessions/sess-2/ws?api_key={API_KEY}"
            ) as ws:
                _ = ws.receive_json()  # connected

                ws.send_json({
                    "type": "message",
                    "content": "Edit the doc",
                    "context": {"doc_id": "doc-999"},
                })

                events = []
                while True:
                    msg = ws.receive_json()
                    events.append(msg)
                    if msg["type"] == "done":
                        break

    types = [e["type"] for e in events]
    assert "applying_edits" in types, f"Expected applying_edits in {types}"
    assert "tool_result" in types


# ---------------------------------------------------------------------------
# 5. test_tool_search_documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_search_documents() -> None:
    """ToolDispatcher.dispatch('search_documents') hits GET /api/documents with params."""
    dispatcher = ToolDispatcher(
        cms_base=CMS_BASE,
        api_key=API_KEY,
        collab_ws_base="ws://cms.local",
    )

    with respx.mock(base_url=CMS_BASE) as mock:
        route = mock.get("/api/documents").mock(
            return_value=httpx.Response(200, json=[{"id": "doc-1"}])
        )
        result = await dispatcher.dispatch(
            "search_documents",
            {"doc_type": "blog_post", "published": True, "limit": 5},
            {},
        )

    assert result["status"] == "ok"
    assert route.called
    # Check that doc_type param was sent
    sent_params = dict(route.calls.last.request.url.params)
    assert sent_params.get("doc_type") == "blog_post"


# ---------------------------------------------------------------------------
# 6. test_tool_no_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_no_change() -> None:
    """When diff_docs returns [] (identical docs), _edit_document returns no_change."""
    dispatcher = ToolDispatcher(
        cms_base=CMS_BASE,
        api_key=API_KEY,
        collab_ws_base="ws://cms.local",
    )

    # Build a PM doc and use it as both current and new
    from starlette_chat.prosemirror import markdown_to_pm

    md = "# Same heading\n\nSame paragraph."
    pm_doc = markdown_to_pm(md)

    with respx.mock(base_url=CMS_BASE) as mock:
        # The document's rich-text field already holds the same doc as new_doc
        mock.get("/api/documents/doc-same").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "doc-same",
                    "doc_type": "blog_post",
                    "body": {"title": "Same", "body_markdown": pm_doc},
                },
            )
        )
        mock.get("/api/schema").mock(return_value=httpx.Response(200, json=_BLOG_SCHEMA))

        # Provide same markdown → diff should be empty
        result = await dispatcher._edit_document(
            doc_id="doc-same",
            markdown_content=md,
            edit_rationale="no change test",
            context={},
        )

    assert result["status"] == "no_change"


# ---------------------------------------------------------------------------
# 6b. edit_document edits one rich-text field over a field-scoped socket
# ---------------------------------------------------------------------------

# Minimal /api/schema payload: a blog post with one rich-text field.
_BLOG_SCHEMA = {
    "blog_post": {
        "block_type": "blog_post",
        "schema": {
            "properties": {
                "title": {"type": "string"},
                "body_markdown": {"cms:field_meta": {"field_type": "rich_text"}},
            }
        },
    },
    "definition": {
        "block_type": "definition",
        "schema": {"properties": {"term": {"type": "string"}}},
    },
}


class _FakeCollabSocket:
    """Scripted stand-in for a ``websockets`` connection: replays *replies* and
    records what the tool sends."""

    def __init__(self, replies: list[dict]) -> None:
        self._replies = [json.dumps(r) for r in replies]
        self.sent: list[dict] = []

    async def __aenter__(self) -> _FakeCollabSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        return self._replies.pop(0)


def _mock_blog_doc(mock: respx.MockRouter, doc_id: str) -> respx.Route:
    route = mock.get(f"/api/documents/{doc_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": doc_id,
                "doc_type": "blog_post",
                "body": {"title": "Keep me", "body_markdown": {"type": "doc", "content": []}},
            },
        )
    )
    mock.get("/api/schema").mock(return_value=httpx.Response(200, json=_BLOG_SCHEMA))
    return route


@pytest.mark.asyncio
async def test_edit_document_scopes_socket_to_rich_text_field() -> None:
    """The tool resolves the doc type's rich-text field, opens the socket with
    ?field=, and reads past the server's interleaved changeset message.

    It used to open the socket with no field (which overwrote the whole body)
    and treat the first reply as the result (which misread ``changeset``).
    """
    dispatcher = ToolDispatcher(cms_base=CMS_BASE, api_key=API_KEY, collab_ws_base="ws://cms.local")
    socket = _FakeCollabSocket(
        [
            {"type": "init", "doc": {"type": "doc", "content": []}, "version": 3, "peers": []},
            {"type": "changeset", "id": "cs-new", "title": "Sep 23"},
            {"type": "steps", "steps": [], "clientIDs": ["claude-assistant"], "version": 4},
        ]
    )
    context: dict[str, Any] = {}

    with (
        respx.mock(base_url=CMS_BASE) as mock,
        patch("websockets.connect", return_value=socket) as connect,
    ):
        doc_route = _mock_blog_doc(mock, "doc-1")
        result = await dispatcher._edit_document(
            doc_id="doc-1",
            markdown_content="A new paragraph.",
            edit_rationale="rewrite",
            context=context,
        )

    assert result["status"] == "accepted"
    assert result["field"] == "body_markdown"
    # The early no-change check must compare against the draft, not the
    # published copy the API returns by default.
    assert doc_route.calls.last.request.url.params["draft"] == "true"
    assert "field=body_markdown" in connect.call_args.args[0]
    steps_message = next(m for m in socket.sent if m["type"] == "steps")
    assert steps_message["version"] == 3
    # The server-created changeset is adopted for the rest of the turn.
    assert context["active_changeset_id"] == "cs-new"


@pytest.mark.asyncio
async def test_edit_document_refuses_doc_without_rich_text_field() -> None:
    """A doc type with no rich-text field gets guidance, not a socket write."""
    dispatcher = ToolDispatcher(cms_base=CMS_BASE, api_key=API_KEY, collab_ws_base="ws://cms.local")

    with respx.mock(base_url=CMS_BASE) as mock, patch("websockets.connect") as connect:
        mock.get("/api/documents/def-1").mock(
            return_value=httpx.Response(
                200, json={"id": "def-1", "doc_type": "definition", "body": {"term": "x"}}
            )
        )
        mock.get("/api/schema").mock(return_value=httpx.Response(200, json=_BLOG_SCHEMA))
        result = await dispatcher._edit_document(
            doc_id="def-1", markdown_content="text", edit_rationale="", context={}
        )

    assert result["status"] == "error"
    assert "update_document" in result["message"]
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_edit_document_without_selected_doc_asks() -> None:
    """On a listing no document is selected; the tool asks instead of guessing."""
    dispatcher = ToolDispatcher(cms_base=CMS_BASE, api_key=API_KEY, collab_ws_base="ws://cms.local")

    result = await dispatcher.dispatch(
        "edit_document", {"markdown_content": "text", "edit_rationale": ""}, {"doc_id": None}
    )

    assert result["status"] == "error"
    assert "which one" in result["message"]
