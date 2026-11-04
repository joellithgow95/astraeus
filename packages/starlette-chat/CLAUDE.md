# starlette-chat — Agent Instructions

Read this file before writing any code in this package.

---

## What this package is

**starlette-chat** is a mountable Starlette sub-application that adds a governed AI chat
collaborator to any Astraeus stack.  The AI participates in live ProseMirror editing sessions
as a **first-class collab peer** — not a sidecar that writes via REST.

Key design decision (ADR 019): the AI submits ProseMirror steps through the same
`/api/documents/{id}/collab` WebSocket endpoint that human editors use.  The
`client_id` in step history is `"claude-assistant"`, which means:

- Connected human editors see AI edits in real time via the existing collab broadcast
- The full edit history is auditable alongside human edits
- REST PATCH was explicitly rejected because it would not propagate to connected editors

The package stores conversation state entirely in the CMS — every ChatSession, ChatMessage,
SystemPrompt, and ModelConfig is a governed CMS document with version history and audit trail.

---

## Python ProseMirror builder scope

`starlette_chat/prosemirror.py` implements `markdown_to_pm(text)` and `pm_to_text(doc)`.
The builder is a pure-Python mistune-based converter with **no Node.js dependency**.

Supported node types (outbound from markdown_to_pm):

| Markdown          | PM node type      |
|-------------------|-------------------|
| paragraph         | `paragraph`       |
| heading `#`–`######` | `heading` (attrs.level 1–6) |
| fenced code block | `code_block` (attrs.params = info string) |
| blockquote        | `blockquote`      |
| bullet list       | `bullet_list` + `list_item` |
| ordered list      | `ordered_list` + `list_item` |
| `**bold**`        | `text` with `strong` mark |
| `_italic_`        | `text` with `em` mark |
| `` `code` ``      | `text` with `code` mark |
| `![alt](src)`     | `image` (attrs: src, alt, title) |
| line break        | `hard_break`      |

**Unknown block nodes fall back to `paragraph`** — raw text is wrapped, never silently dropped.
Unknown inline nodes emit their raw text, or nothing if raw is empty.

---

## The collab WS client pattern — why steps, not REST PATCH

`ToolDispatcher._edit_document()` in `tools.py` implements this flow:

1. Fetch the current draft body for the document (via CMS HTTP).
2. Convert the AI's markdown output to a PM doc via `markdown_to_pm()`.
3. Compute replace steps via `diff_docs()` (block-granularity differ in `diff.py`).
4. Open a WebSocket connection to `/api/documents/{doc_id}/collab?api_key=...`.
5. Send a `presence` message identifying as `client_type: "ai"`.
6. Wait for the `init` message to get the current server version.
7. Submit a `steps` message with `clientID: "claude-assistant"`.
8. Wait for confirmation or rejection.  On rejection, re-fetch and retry once.

This is intentional.  REST PATCH goes directly to the DB and bypasses the collab authority —
connected human editors would not see the change until they reconnect.  Steps through the
collab WS propagate immediately to all peers.

---

## Two WebSocket connections in the browser

When a user is editing a document with chat enabled, the browser maintains **two independent
WebSocket connections**:

| Connection | URL pattern | Purpose |
|-----------|-------------|---------|
| Collab WS | `/cms/api/documents/{doc_id}/collab` | ProseMirror step authority — all editors (human + AI) submit and receive steps here |
| Chat WS   | `/chat/api/chat/sessions/{session_id}/ws` | Streaming chat turns — tokens, tool chips, edit notices |

The browser's chat panel (`ChatPanel` in `embed.js`) connects to the Chat WS.
The editor's `CollabManager` in `embed.js` connects to the Collab WS.
These two connections are entirely independent; neither knows about the other at the protocol
level.  Coordination happens when the AI calls `edit_document` — the AI-side collab WS
connection is opened server-side by `ToolDispatcher`, not by the browser.

---

## Tool dispatcher routing

`ToolDispatcher.dispatch()` in `tools.py` routes tool calls to two backends:

**CMS HTTP API** (`{cms_base}/api/...`):
- `search_documents` → `GET /api/documents`
- `publish_document` → `POST /api/documents/{id}/publish`
- `get_available_doc_types` → `GET /api/schema`
- `get_document_history` → `GET /api/documents/{id}/history`
- `create_document` → `POST /api/documents`
- `add_to_changeset` → `POST /api/changesets`

**Collab WS** (`{collab_ws_base}/api/documents/{doc_id}/collab`):
- `edit_document` — builds steps and submits via WS (see collab WS pattern above)

`search_media` calls a astraeus-media HTTP endpoint if configured; it falls back gracefully if the
media endpoint is not reachable.

---

## Provider ABC contract

`BaseProvider` in `starlette_chat/providers/base.py` is the extension point for adding new LLM
providers.  Implement one method:

```python
from starlette_chat.providers.base import BaseProvider, StreamEvent
from collections.abc import AsyncIterator

class MyProvider(BaseProvider):
    async def stream(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        ...
        yield StreamEvent(type="token", data={"delta": "Hello"})
        yield StreamEvent(type="done", data={})
```

`StreamEvent.type` values the chat WS handler understands:

| type          | data keys                         | effect |
|---------------|-----------------------------------|--------|
| `"thinking"`  | (none required)                   | forwarded to browser as thinking indicator |
| `"token"`     | `delta: str`                      | forwarded; accumulated into assistant message |
| `"tool_use"`  | `tool: str`, `input: dict`, `id: str` | dispatcher called; result forwarded to browser |
| `"done"`      | (none required)                   | terminates the turn loop; assistant message persisted |

Pass an instance to `ChatAPI(provider=MyProvider(...))`.  If `provider=None`, the handler
auto-imports `AnthropicProvider` — this requires `starlette-chat[anthropic]` to be installed.

---

## register_blocks(cms) must be called before cms.app

`register_blocks(cms)` registers the four block types (`system_prompt`, `model_config`,
`chat_session`, `chat_message`) with a CMS instance.  It must be called **before**
`cms.app` is first accessed, because `cms.app` is a lazy property that builds the Starlette
sub-app and creates database tables on first access.

Correct order:

```python
from starlette_chat import ChatAPI, register_blocks

cms = CMS(database_url="...", auth="apikey", api_key="secret")
register_blocks(cms)            # ← must come first

chat = ChatAPI(
    cms_base_url="http://localhost:8000/cms",
    cms_api_key="secret",
)

app = Starlette(routes=[
    Mount("/cms",  app=cms.app),   # cms.app built here — tables created
    Mount("/chat", app=chat.app),
])
```

Calling `register_blocks(cms)` after `cms.app` has been accessed raises `RuntimeError`.

---

## Testing patterns

### CMS HTTP mocking — use respx

For unit tests that exercise `ToolDispatcher` in isolation, mock the CMS HTTP layer with
[respx](https://lundberg.github.io/respx/):

```python
import respx, httpx

with respx.mock:
    respx.get("http://cms/api/documents").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    result = await dispatcher.dispatch("search_documents", {}, {})
    assert result["status"] == "ok"
```

### Provider mocking — MockProvider for WS tests

For integration tests that exercise the full chat WS turn handler, inject a mock provider:

```python
from starlette_chat.providers.base import BaseProvider, StreamEvent

class MockProvider(BaseProvider):
    async def stream(self, messages, system_prompt, tools, model, temperature, max_tokens):
        yield StreamEvent(type="token", data={"delta": "Hello"})
        yield StreamEvent(type="done", data={})
```

Pass `provider=MockProvider()` to `ChatAPI(...)` so the test never calls a real LLM.

### End-to-end integration tests

Use `httpx.AsyncClient` with `ASGITransport` against a real CMS instance (SQLite in-memory)
and a real `ChatAPI` instance.  The CMS lifespan must be running — use the
`lifespan_context` async context manager.  See `tests/test_integration.py` for the canonical
pattern.

```python
instance = CMS(database_url="sqlite://:memory:", auth="none")
register_blocks(instance)
async with instance.lifespan_context(None):
    app = Starlette(routes=[Mount("/cms", app=instance.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        ...
```
