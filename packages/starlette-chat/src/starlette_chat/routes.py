"""
Route handlers for starlette-chat.

Three routes:
- POST /api/chat/sessions             — create a new chat session
- GET  /api/chat/sessions/{session_id} — fetch session + recent messages
- WS   /api/chat/sessions/{session_id}/ws — streaming conversation turn
"""

from __future__ import annotations

import ast
import json
from typing import TYPE_CHECKING, Any

import httpx
from langchain_core.messages import HumanMessage
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from .graph import build_graph
from .providers.base import DEFAULT_MODEL
from .session import generate_session_slug
from .tools import ToolDispatcher, make_tools

if TYPE_CHECKING:
    from .app import ChatAPI


def _check_chat_auth(request: Request, chat: ChatAPI) -> bool:
    """Return True if the request is authorised to use the chat API.

    Accepts either:
    - ``Authorization: Bearer <api_key>`` header, or
    - ``?api_key=<api_key>`` query param (WebSocket upgrades can't set headers), or
    - A valid ``cms_session`` cookie (browser-based auth — same cookie the
      editor embed uses; requires ``session_secret`` to be set on ChatAPI).
    """
    # Bearer header (HTTP routes)
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[len("Bearer "):] == chat._cms_api_key:
        return True

    # Query param (WebSocket upgrades)
    if request.query_params.get("api_key") == chat._cms_api_key:
        return True

    # Session cookie (browser embed — no credentials exposed in HTML)
    if chat._session_secret is not None:
        from starlette_cms.session import validate_session_token
        token = request.cookies.get("cms_session", "")
        if token and validate_session_token(token, chat._session_secret):
            return True

    return False


def _cms_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _cms_to_ws_base(cms_base: str) -> str:
    """Convert an HTTP CMS base URL to a WebSocket base URL."""
    if cms_base.startswith("https://"):
        return "wss://" + cms_base[len("https://"):]
    if cms_base.startswith("http://"):
        return "ws://" + cms_base[len("http://"):]
    return cms_base


def make_routes(chat: ChatAPI) -> list:
    """Return the route list for the ChatAPI Starlette app."""

    # ------------------------------------------------------------------
    # POST /api/chat/sessions
    # ------------------------------------------------------------------

    async def create_session(request: Request) -> JSONResponse:
        if not _check_chat_auth(request, chat):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        persona: str = data.get("persona", "default")
        doc_id: str | None = data.get("doc_id")

        headers = _cms_headers(chat._cms_api_key)

        # Always fetch model config and system prompt from the CMS —
        # they are editorial content that lives there regardless of
        # whether a separate session DB is configured.
        model_config_body: dict[str, Any] = {}
        system_prompt_body: dict[str, Any] = {}
        model_config_doc_id: str | None = None
        prompt_doc_id: str | None = None

        async with httpx.AsyncClient() as client:
            # --- Find ModelConfig for this persona ---
            mc_resp = await client.get(
                f"{chat._cms_base}/api/documents",
                params={"type": "model_config", "published": "true"},
                headers=headers,
            )
            if mc_resp.status_code == 200:
                mc_items = mc_resp.json()
                # Support both list and paginated responses
                if isinstance(mc_items, list):
                    docs = mc_items
                else:
                    docs = mc_items.get("items", mc_items.get("documents", []))
                for doc in docs:
                    body = doc.get("body") or {}
                    if isinstance(body, str):
                        try:
                            body = json.loads(body)
                        except Exception:
                            body = {}
                    if body.get("persona") == persona:
                        model_config_doc_id = doc.get("id")
                        model_config_body = body
                        break
                if model_config_doc_id is None and docs:
                    # Fall back to first doc (may have persona=="default")
                    model_config_doc_id = docs[0].get("id")
                    raw = docs[0].get("body") or {}
                    model_config_body = json.loads(raw) if isinstance(raw, str) else raw

            # --- Find SystemPrompt for this persona ---
            sp_resp = await client.get(
                f"{chat._cms_base}/api/documents",
                params={"type": "system_prompt", "published": "true"},
                headers=headers,
            )
            if sp_resp.status_code == 200:
                sp_items = sp_resp.json()
                if isinstance(sp_items, list):
                    docs = sp_items
                else:
                    docs = sp_items.get("items", sp_items.get("documents", []))
                for doc in docs:
                    body = doc.get("body") or {}
                    if isinstance(body, str):
                        try:
                            body = json.loads(body)
                        except Exception:
                            body = {}
                    if body.get("persona") == persona:
                        prompt_doc_id = doc.get("id")
                        system_prompt_body = body
                        break
                if prompt_doc_id is None and docs:
                    prompt_doc_id = docs[0].get("id")
                    raw = docs[0].get("body") or {}
                    system_prompt_body = json.loads(raw) if isinstance(raw, str) else raw

            if chat._store is not None:
                # --- Separate session DB path: snapshot config values by value ---
                session_id = generate_session_slug()
                await chat._store.create_session(
                    session_id=session_id,
                    persona=persona,
                    doc_ref=doc_id,
                    model_name=model_config_body.get("model_name", DEFAULT_MODEL),
                    temperature=model_config_body.get("temperature", 1.0),
                    max_tokens=model_config_body.get("max_tokens", 4096),
                    system_prompt=system_prompt_body.get("content", ""),
                )
            else:
                # --- CMS path: store refs, let the turn handler resolve them ---
                slug = generate_session_slug()
                session_body: dict[str, Any] = {
                    "persona": persona,
                    "doc_ref": doc_id,
                    "model_config_ref": model_config_doc_id,
                    "prompt_ref": prompt_doc_id,
                    "turn_count": 0,
                }
                create_resp = await client.post(
                    f"{chat._cms_base}/api/documents",
                    json={"doc_type": "chat_session", "body": session_body, "slug": slug},
                    headers=headers,
                )
                if create_resp.status_code not in (200, 201):
                    return JSONResponse(
                        {"error": "Failed to create session", "detail": create_resp.text},
                        status_code=502,
                    )
                session_doc = create_resp.json()
                session_id = session_doc["id"]

                # --- Publish the session doc ---
                await client.post(
                    f"{chat._cms_base}/api/documents/{session_id}/publish",
                    headers=headers,
                )

        return JSONResponse(
            {"session_id": session_id, "doc_id": doc_id},
            status_code=201,
        )

    # ------------------------------------------------------------------
    # GET /api/chat/sessions/{session_id}
    # ------------------------------------------------------------------

    async def get_session(request: Request) -> JSONResponse:
        if not _check_chat_auth(request, chat):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        session_id: str = request.path_params["session_id"]

        if chat._store is not None:
            session = await chat._store.get_session(session_id)
            if session is None:
                return JSONResponse({"error": "Session not found"}, status_code=404)
            messages = await chat._store.list_messages(session_id)
            return JSONResponse({"session": session, "messages": messages})

        headers = _cms_headers(chat._cms_api_key)

        async with httpx.AsyncClient() as client:
            session_resp = await client.get(
                f"{chat._cms_base}/api/documents/{session_id}",
                headers=headers,
            )
            if session_resp.status_code == 404:
                return JSONResponse({"error": "Session not found"}, status_code=404)
            if session_resp.status_code != 200:
                return JSONResponse(
                    {"error": "Failed to load session"}, status_code=502
                )

            msgs_resp = await client.get(
                f"{chat._cms_base}/api/documents",
                params={
                    "type": "chat_message",
                    "filter[session_ref]": session_id,
                    "limit": "50",
                },
                headers=headers,
            )
            messages: list[Any] = []
            if msgs_resp.status_code == 200:
                raw = msgs_resp.json()
                if isinstance(raw, list):
                    messages = raw
                else:
                    messages = raw.get("items", raw.get("documents", []))

        return JSONResponse(
            {
                "session": session_resp.json(),
                "messages": messages,
            }
        )

    # ------------------------------------------------------------------
    # WS /api/chat/sessions/{session_id}/ws
    # ------------------------------------------------------------------

    async def chat_ws(websocket: WebSocket) -> None:
        """Handle a streaming chat WebSocket connection."""
        session_id: str = websocket.path_params["session_id"]

        # Auth: session cookie (browser embed) or ?api_key= query param (server / testing).
        # WebSocket upgrade requests carry cookies automatically; they cannot set headers.
        if not _check_chat_auth(websocket, chat):
            await websocket.close(code=4403)
            return

        await websocket.accept()
        await websocket.send_json(
            {"type": "connected", "session_id": session_id}
        )

        headers = _cms_headers(chat._cms_api_key)
        collab_ws_base = _cms_to_ws_base(chat._cms_base)
        dispatcher = ToolDispatcher(
            cms_base=chat._cms_base,
            api_key=chat._cms_api_key,
            collab_ws_base=collab_ws_base,
        )

        try:
            while True:
                try:
                    msg = await websocket.receive_json()
                except Exception:
                    break

                if msg.get("type") != "message":
                    continue

                # Extract user content and context
                content: str = msg.get("content", "")
                context: dict[str, Any] = msg.get("context", {})

                await _handle_turn(
                    websocket=websocket,
                    session_id=session_id,
                    content=content,
                    context=context,
                    chat=chat,
                    headers=headers,
                    dispatcher=dispatcher,
                )

        finally:
            pass  # WebSocket cleaned up by Starlette on exit

    return [
        Route("/api/chat/sessions", endpoint=create_session, methods=["POST"]),
        Route(
            "/api/chat/sessions/{session_id}",
            endpoint=get_session,
            methods=["GET"],
        ),
        WebSocketRoute(
            "/api/chat/sessions/{session_id}/ws",
            endpoint=chat_ws,
        ),
    ]


# ------------------------------------------------------------------
# Turn handler — extracted for testability
# ------------------------------------------------------------------


async def _handle_turn(
    websocket: WebSocket,
    session_id: str,
    content: str,
    context: dict[str, Any],
    chat: Any,
    headers: dict[str, str],
    dispatcher: ToolDispatcher,
) -> None:
    """Process a single conversation turn via the LangGraph ReAct loop."""
    system_prompt = ""
    model_name = DEFAULT_MODEL
    turn_index = 0

    if chat._store is not None:
        # --- Separate session DB path ---
        turn_ctx = await chat._store.get_turn_context(session_id)
        if turn_ctx is None:
            await websocket.send_json({"type": "error", "message": "Session not found"})
            return

        system_prompt = turn_ctx.system_prompt
        model_name = turn_ctx.model_name
        turn_index = turn_ctx.turn_index

        # Signal thinking to browser
        await websocket.send_json({"type": "thinking"})

        await chat._store.append_message(
            session_id, "user", content, turn_index
        )
        await chat._store.update_turn_count(session_id, turn_index + 1)
    else:
        # --- CMS path ---
        async with httpx.AsyncClient() as client:
            # Load session document
            session_resp = await client.get(
                f"{chat._cms_base}/api/documents/{session_id}",
                headers=headers,
            )
            if session_resp.status_code != 200:
                await websocket.send_json(
                    {"type": "error", "message": "Session not found"}
                )
                return

            session_doc = session_resp.json()
            session_body = session_doc.get("body") or {}
            if isinstance(session_body, str):
                try:
                    session_body = json.loads(session_body)
                except Exception:
                    session_body = {}

            # Load system prompt
            prompt_ref = session_body.get("prompt_ref")
            if prompt_ref:
                sp_resp = await client.get(
                    f"{chat._cms_base}/api/documents/{prompt_ref}",
                    headers=headers,
                )
                if sp_resp.status_code == 200:
                    sp_body = sp_resp.json().get("body") or {}
                    if isinstance(sp_body, str):
                        try:
                            sp_body = json.loads(sp_body)
                        except Exception:
                            sp_body = {}
                    system_prompt = sp_body.get("content", "")

            # Load model config (model_name only — temperature/max_tokens handled by LangGraph)
            mc_ref = session_body.get("model_config_ref")
            if mc_ref:
                mc_resp = await client.get(
                    f"{chat._cms_base}/api/documents/{mc_ref}",
                    headers=headers,
                )
                if mc_resp.status_code == 200:
                    mc_body = mc_resp.json().get("body") or {}
                    if isinstance(mc_body, str):
                        try:
                            mc_body = json.loads(mc_body)
                        except Exception:
                            mc_body = {}
                    model_name = mc_body.get("model_name", model_name)

            turn_index = session_body.get("turn_count", 0)

            # Signal thinking to browser
            await websocket.send_json({"type": "thinking"})

            # Persist user ChatMessage
            await client.post(
                f"{chat._cms_base}/api/documents",
                json={
                    "doc_type": "chat_message",
                    "body": {
                        "session_ref": session_id,
                        "role": "user",
                        "content": content,
                        "turn_index": turn_index,
                    },
                },
                headers=headers,
            )
            # Increment turn count on session
            await client.patch(
                f"{chat._cms_base}/api/documents/{session_id}",
                json={"body": {"turn_count": turn_index + 1}},
                headers=headers,
            )

    # Resolve provider
    provider = chat._provider
    if provider is None:
        try:
            from .providers.anthropic import AnthropicProvider

            provider = AnthropicProvider()
        except ImportError:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "No provider configured. Install starlette-chat[anthropic].",
                }
            )
            return

    # Build graph — checkpointer restores prior turn messages automatically.
    tools = make_tools(dispatcher, context)
    lc_model = provider.get_model()
    graph = build_graph(lc_model, tools, checkpointer=chat._checkpointer)
    graph_config = {
        "configurable": {
            "thread_id": session_id,
            "system_prompt": system_prompt,
        }
    }
    initial_input = {
        "messages": [HumanMessage(content=content)],
        "session_id": session_id,
        "context": context,
    }

    # Stream LangGraph events → WebSocket wire format
    assistant_content_parts: list[str] = []
    steps_applied = 0

    async for event in graph.astream_events(initial_input, config=graph_config, version="v2"):
        ws_msg = _langgraph_event_to_ws(event)
        if ws_msg is None:
            continue

        if ws_msg["type"] == "token":
            assistant_content_parts.append(ws_msg.get("delta", ""))
            await websocket.send_json(ws_msg)

        elif ws_msg["type"] == "tool_result":
            # Strip internal sentinel keys before forwarding to browser
            edit_steps = ws_msg.pop("_edit_steps", None)
            edit_doc_id = ws_msg.pop("_edit_doc_id", None)
            await websocket.send_json(ws_msg)
            # Emit applying_edits separately so chat-panel.js can animate it
            if edit_steps is not None:
                steps_applied = edit_steps
                await websocket.send_json({
                    "type": "applying_edits",
                    "doc_id": edit_doc_id or "",
                    "step_count": edit_steps,
                })

        else:
            await websocket.send_json(ws_msg)

    # Persist assistant ChatMessage
    assistant_content = "".join(assistant_content_parts)
    assistant_turn_index = turn_index + 1
    assistant_msg_id: str | None = None

    if chat._store is not None:
        assistant_msg_id = await chat._store.append_message(
            session_id,
            "assistant",
            assistant_content,
            assistant_turn_index,
            model_used=model_name,
            steps_applied=steps_applied or None,
        )
    else:
        async with httpx.AsyncClient() as client:
            assistant_msg_resp = await client.post(
                f"{chat._cms_base}/api/documents",
                json={
                    "doc_type": "chat_message",
                    "body": {
                        "session_ref": session_id,
                        "role": "assistant",
                        "content": assistant_content,
                        "turn_index": assistant_turn_index,
                        "model_used": model_name,
                        "steps_applied": steps_applied,
                    },
                },
                headers=headers,
            )
            if assistant_msg_resp.status_code in (200, 201):
                assistant_msg_id = assistant_msg_resp.json().get("id")

    await websocket.send_json({"type": "done", "message_id": assistant_msg_id})


def _langgraph_event_to_ws(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map a LangGraph astream_events v2 event to a WebSocket wire message.

    Returns ``None`` for events that don't produce a WebSocket message.
    The returned dict is sent directly via ``websocket.send_json()``.
    """
    kind = event.get("event", "")
    name = event.get("name", "")

    # Streaming text tokens from the LLM
    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        if chunk is None:
            return None
        # AIMessageChunk — extract text delta
        delta = ""
        if hasattr(chunk, "content"):
            c = chunk.content
            if isinstance(c, str):
                delta = c
            elif isinstance(c, list):
                # Anthropic-style content blocks: [{"type": "text", "text": "..."}]
                for block in c:
                    if isinstance(block, dict) and block.get("type") == "text":
                        delta += block.get("text", "")
        if not delta:
            return None
        return {"type": "token", "delta": delta}

    # Tool invocation starting
    if kind == "on_tool_start":
        tool_input = event.get("data", {}).get("input", {})
        return {"type": "tool_use", "tool": name, "input": tool_input}

    # Tool invocation completed
    if kind == "on_tool_end":
        output = event.get("data", {}).get("output", {})
        # output may be a ToolMessage or a plain dict
        result: dict[str, Any] = {}
        if hasattr(output, "content"):
            content = output.content
            if isinstance(content, str):
                # Tool output is JSON (true/false/null); json.loads handles it.
                # Fall back to ast.literal_eval for Python-repr'd dicts.
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    try:
                        parsed = ast.literal_eval(content)
                    except (ValueError, SyntaxError):
                        parsed = {}
                result = parsed if isinstance(parsed, dict) else {}
        elif isinstance(output, dict):
            result = output

        ws_msg: dict[str, Any] = {
            "type": "tool_result",
            "tool": name,
            "summary": _summarise_tool_result(result),
            "result": result,
        }

        # Extra "applying_edits" event when edit_document ran
        if name == "edit_document" and result.get("status") == "accepted":
            # Caller inspects applying_edits to track steps_applied; we return
            # tool_result here and the caller emits applying_edits separately
            # after this function returns. Use a sentinel key to signal it.
            ws_msg["_edit_steps"] = result.get("step_count", 0)
            ws_msg["_edit_doc_id"] = result.get("doc_id", "")

        return ws_msg

    return None


def _summarise_tool_result(result: dict[str, Any]) -> str:
    """Build a human-readable one-line summary of a tool result."""
    status = result.get("status", "")
    if status == "accepted":
        n = result.get("step_count", 0)
        rationale = result.get("edit_rationale", "")
        return f"Applied {n} edit step(s): {rationale}"
    if status == "no_change":
        return result.get("message", "No changes")
    if status == "conflict":
        return result.get("message", "Version conflict")
    if status == "error":
        return result.get("message", "Error")
    if status == "published":
        return f"Published {result.get('doc_id', '')}"
    if status == "created":
        doc = result.get("document", {})
        # Prefer a human-readable name (body.title or slug) over the raw id.
        label = doc.get("body", {}).get("title") or doc.get("slug") or doc.get("id", "")
        return f'Created "{label}"' if label else "Created document"
    if status == "ok":
        return "OK"
    return str(result)
