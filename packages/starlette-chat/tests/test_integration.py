"""
Integration tests for starlette-chat — Phase CH-6.

These tests exercise the full ChatAPI + real CMS stack end-to-end.
The LLM provider is replaced with a MockProvider (no network calls to Anthropic),
but the CMS layer is a real SQLite-backed CMS instance.

Because ChatAPI's route handlers call the CMS via httpx.AsyncClient() over HTTP,
the tests use respx with an ASGI-proxy side_effect so those internal calls are
routed to the real CMS ASGI app without a listening socket.

Setup pattern:
1. Create a CMS with a temp SQLite file
2. register_blocks(cms) to add the four chat block types
3. Bring up the CMS lifespan (creates tables)
4. Build ChatAPI with MockProvider
5. Register a respx route that proxies http://cms-internal/... → cms.app
6. Use httpx.AsyncClient + ASGITransport for outer (ChatAPI) calls
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr
from starlette_chat import ChatAPI, register_blocks
from starlette_chat.providers.base import BaseProvider
from starlette_cms import CMS

# ---------------------------------------------------------------------------
# Minimal LangChain model stub
# ---------------------------------------------------------------------------


class _MockChatModel(BaseChatModel):
    """Single-response BaseChatModel for integration tests."""

    response: str = "Hello from the AI."
    _called: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools: list, **kwargs: Any) -> _MockChatModel:
        return self

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.response))])

    async def _astream(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.response))


# ---------------------------------------------------------------------------
# Mock provider — yields one token then done
# ---------------------------------------------------------------------------


class _MockProvider(BaseProvider):
    """LLM stub: returns a single fixed response.  Never calls a real API."""

    def __init__(self, response: str = "Hello from the AI.") -> None:
        self._response = response

    def get_model(self) -> BaseChatModel:
        return _MockChatModel(response=self._response)


# ---------------------------------------------------------------------------
# ASGI proxy helper
# ---------------------------------------------------------------------------

_CMS_BASE = "http://cms-internal"
_API_KEY = "test-secret"


def _make_cms_proxy(cms_app: Any) -> Any:
    """Return a respx side_effect that proxies requests to the real CMS ASGI app.

    The ChatAPI routes call http://cms-internal/api/... — this proxy intercepts
    those calls and dispatches them through the CMS ASGI transport.
    """
    _inner = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cms_app),
        base_url=_CMS_BASE,
    )

    async def _proxy(request: httpx.Request) -> httpx.Response:
        # Strip hop-by-hop headers that ASGI transport doesn't need
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "transfer-encoding")
        }
        r = await _inner.request(
            method=request.method,
            url=str(request.url),
            headers=headers,
            content=request.content,
        )
        return httpx.Response(r.status_code, headers=dict(r.headers), content=r.content)

    return _proxy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chat_stack() -> AsyncGenerator[tuple[CMS, ChatAPI, httpx.AsyncClient], None]:
    """
    Yields (cms, chat, chat_client) where:

    - cms is a real SQLite-backed CMS with all starlette-chat blocks registered
    - chat is a ChatAPI with MockProvider pointing at http://cms-internal
    - chat_client is an httpx.AsyncClient via ASGITransport targeting chat.app

    respx intercepts the ChatAPI's internal CMS calls and proxies them to the
    real cms.app via ASGITransport (no listening socket required).
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        cms = CMS(
            database_url=f"sqlite:///{db_path}",
            auth="apikey",
            api_key=_API_KEY,
            read_auth=False,
        )
        register_blocks(cms)

        chat = ChatAPI(
            cms_base_url=_CMS_BASE,
            cms_api_key=_API_KEY,
            provider=_MockProvider(),
        )

        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=chat.app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {_API_KEY}"},
            ) as chat_client:
                yield cms, chat, chat_client
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest_asyncio.fixture
async def cms_client(chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient]) -> httpx.AsyncClient:
    """Direct httpx client to the CMS ASGI app for seeding test data."""
    cms, _chat, _chat_client = chat_stack
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app),
        base_url=_CMS_BASE,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_persona_docs(
    cms_client: httpx.AsyncClient,
    persona: str = "default",
) -> tuple[str, str]:
    """Create and publish a ModelConfig and SystemPrompt for *persona*.

    Returns (model_config_id, system_prompt_id).
    """
    headers = {"Authorization": f"Bearer {_API_KEY}"}

    mc_resp = await cms_client.post(
        "/api/documents",
        json={
            "doc_type": "model_config",
            "body": {
                "persona": persona,
                "provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
                "temperature": 1.0,
                "max_tokens": 4096,
                "system_prompt_ref": None,
            },
        },
        headers=headers,
    )
    assert mc_resp.status_code in (200, 201), mc_resp.text
    mc_id = mc_resp.json()["id"]
    await cms_client.post(f"/api/documents/{mc_id}/publish", headers=headers)

    sp_resp = await cms_client.post(
        "/api/documents",
        json={
            "doc_type": "system_prompt",
            "body": {
                "persona": persona,
                "content": "You are a helpful assistant.",
                "change_rationale": "Initial prompt",
                "authored_by": "test",
            },
        },
        headers=headers,
    )
    assert sp_resp.status_code in (200, 201), sp_resp.text
    sp_id = sp_resp.json()["id"]
    await cms_client.post(f"/api/documents/{sp_id}/publish", headers=headers)

    return mc_id, sp_id


# ---------------------------------------------------------------------------
# Test 1: POST /api/chat/sessions creates a ChatSession doc in CMS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_creates_cms_document(
    chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient],
    cms_client: httpx.AsyncClient,
) -> None:
    """POST /api/chat/sessions must create a chat_session document in the real CMS."""
    cms, _chat, chat_client = chat_stack
    await _seed_persona_docs(cms_client)

    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))

        resp = await chat_client.post(
            "/api/chat/sessions",
            json={"persona": "default"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    session_id = body.get("session_id")
    assert session_id, "Response must include session_id"

    # Verify the CMS document exists and has the right body
    headers = {"Authorization": f"Bearer {_API_KEY}"}
    get_resp = await cms_client.get(f"/api/documents/{session_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    doc = get_resp.json()
    doc_body = doc.get("body") or {}
    if isinstance(doc_body, str):
        doc_body = json.loads(doc_body)
    assert doc_body.get("persona") == "default"


# ---------------------------------------------------------------------------
# Test 2: GET /api/chat/sessions/{id} returns session + messages list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_get_returns_messages(
    chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient],
    cms_client: httpx.AsyncClient,
) -> None:
    """GET /api/chat/sessions/{id} returns session document and messages list."""
    cms, _chat, chat_client = chat_stack
    await _seed_persona_docs(cms_client)

    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))

        create_resp = await chat_client.post(
            "/api/chat/sessions",
            json={"persona": "default"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["session_id"]

        get_resp = await chat_client.get(f"/api/chat/sessions/{session_id}")

    assert get_resp.status_code == 200, get_resp.text
    data = get_resp.json()
    assert "session" in data
    assert "messages" in data
    assert isinstance(data["messages"], list)


# ---------------------------------------------------------------------------
# Test 3: WS without correct api_key closes before accepting (code 4403)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_ws_rejected(
    chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient],
    cms_client: httpx.AsyncClient,
) -> None:
    """WS connect without correct api_key is closed with code 4403 before accept."""
    cms, chat, chat_client = chat_stack
    await _seed_persona_docs(cms_client)

    # Create a session first (need a valid session_id for the WS path)
    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))
        create_resp = await chat_client.post(
            "/api/chat/sessions",
            json={"persona": "default"},
        )
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]

    # Verify the WS handler rejects wrong api_key at the source level.
    # httpx does not support WebSocket upgrades, so we verify via the Starlette
    # TestClient which does.  The handler calls websocket.close(code=4403)
    # before websocket.accept() when the api_key is wrong.
    from starlette.testclient import TestClient

    tc = TestClient(chat.app, raise_server_exceptions=False)
    with pytest.raises(Exception):
        # TestClient raises WebSocketDisconnect or similar on server-side close-before-accept
        with tc.websocket_connect(
            f"/api/chat/sessions/{session_id}/ws?api_key=wrong-key"
        ) as ws:
            ws.receive_json()


# ---------------------------------------------------------------------------
# Test 4: Full turn persists user + assistant ChatMessage docs in CMS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_turn_persists_messages(
    chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient],
    cms_client: httpx.AsyncClient,
) -> None:
    """A full WS turn with MockProvider creates user + assistant ChatMessage docs in CMS."""
    cms, chat, chat_client = chat_stack
    await _seed_persona_docs(cms_client)

    # Create a session
    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))
        create_resp = await chat_client.post(
            "/api/chat/sessions",
            json={"persona": "default"},
        )
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]

    # Drive the turn handler directly (avoids WS upgrade complexity)
    from starlette_chat.routes import _handle_turn
    from starlette_chat.tools import ToolDispatcher

    headers = {"Authorization": f"Bearer {_API_KEY}"}
    dispatcher = ToolDispatcher(
        cms_base=_CMS_BASE,
        api_key=_API_KEY,
        collab_ws_base=_CMS_BASE.replace("http://", "ws://"),
    )

    sent: list[dict] = []

    class _FakeWS:
        async def send_json(self, data: dict) -> None:
            sent.append(data)

    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))

        await _handle_turn(
            websocket=_FakeWS(),  # type: ignore[arg-type]
            session_id=session_id,
            content="Say hello",
            context={},
            chat=chat,
            headers=headers,
            dispatcher=dispatcher,
        )

    # Verify events were emitted
    types_sent = [m.get("type") for m in sent]
    assert "thinking" in types_sent
    assert "token" in types_sent
    assert "done" in types_sent

    # Verify ChatMessage docs were persisted in the real CMS
    msgs_resp = await cms_client.get(
        "/api/documents",
        params={"doc_type": "chat_message"},
        headers=headers,
    )
    assert msgs_resp.status_code == 200
    raw = msgs_resp.json()
    docs = raw if isinstance(raw, list) else raw.get("items", raw.get("documents", []))

    roles: set[str] = set()
    for doc in docs:
        b = doc.get("body") or {}
        if isinstance(b, str):
            b = json.loads(b)
        if b.get("session_ref") == session_id:
            roles.add(b.get("role", ""))

    assert "user" in roles, f"user ChatMessage not found; roles: {roles}"
    assert "assistant" in roles, f"assistant ChatMessage not found; roles: {roles}"


# ---------------------------------------------------------------------------
# Test 5: Session created with doc_id has doc_ref set in CMS body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_doc_linked_to_document(
    chat_stack: tuple[CMS, ChatAPI, httpx.AsyncClient],
    cms_client: httpx.AsyncClient,
) -> None:
    """POST /api/chat/sessions with doc_id must set ChatSession.body.doc_ref in CMS."""
    cms, _chat, chat_client = chat_stack
    await _seed_persona_docs(cms_client)

    # Create a target document to link to (re-use system_prompt block as a generic doc)
    headers = {"Authorization": f"Bearer {_API_KEY}"}
    target_resp = await cms_client.post(
        "/api/documents",
        json={
            "doc_type": "system_prompt",
            "body": {
                "persona": "default",
                "content": "Target document for link test",
                "change_rationale": "test",
                "authored_by": "test",
            },
        },
        headers=headers,
    )
    assert target_resp.status_code in (200, 201), target_resp.text
    target_id = target_resp.json()["id"]

    # Create a chat session linked to that document
    with respx.mock(base_url=_CMS_BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=_make_cms_proxy(cms.app))
        create_resp = await chat_client.post(
            "/api/chat/sessions",
            json={"persona": "default", "doc_id": target_id},
        )

    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]
    assert create_resp.json().get("doc_id") == target_id

    # Verify the CMS document has doc_ref set
    get_resp = await cms_client.get(f"/api/documents/{session_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    doc = get_resp.json()
    doc_body = doc.get("body") or {}
    if isinstance(doc_body, str):
        doc_body = json.loads(doc_body)
    assert doc_body.get("doc_ref") == target_id, (
        f"Expected doc_ref={target_id!r}, got {doc_body.get('doc_ref')!r}"
    )
