# starlette-chat

Governed AI chat collaborator for the Astraeus content platform.

`starlette-chat` is a mountable Starlette sub-application that adds a real-time AI chat
collaborator to any Astraeus editing environment. The AI participates in the same ProseMirror
WebSocket collab layer as human editors — its edits appear live in every connected browser, with
cursor attribution (`◆Claude`), undo support, and full step history.

## Features

- Chat sessions and messages are stored as governed CMS documents (versioned, auditable)
- AI edits flow through the ProseMirror step authority — not REST PATCH
- `SystemPrompt` and `ModelConfig` are CMS documents (version history, change rationale)
- Curated tool suite scoped to the editing session
- Provider-agnostic: `AnthropicProvider` included, `BaseProvider` ABC for others

## Installation

```bash
pip install starlette-chat
# With Anthropic provider:
pip install "starlette-chat[anthropic]"
```

## Quickstart

```python
from starlette_chat import ChatAPI, register_blocks
from starlette_chat.providers.anthropic import AnthropicProvider

# Register block types with your CMS instance (before cms.app is accessed)
register_blocks(cms)

chat = ChatAPI(
    cms_base_url="http://localhost:8000/cms",
    cms_api_key="your-api-key",
    provider=AnthropicProvider(api_key="your-anthropic-key"),
)

# Mount alongside CMS and astraeus-media
app.mount("/cms",   app=cms.app)
app.mount("/chat",  app=chat.app)
app.mount("/media", app=media.app)
```

## Architecture

See [ADR 019](../../docs/decisions/019-collaborative-ai-chat.md) for full design rationale.
