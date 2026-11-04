"""
Astraeus demo — minimal full-stack integration.

Shows starlette-cms, starlette-editor, starlette-chat, and astraeus-media working together.
Run with: uvicorn app:app --reload
"""

import os
from contextlib import asynccontextmanager

from astraeus_media.adapters.starlette import create_media_mount
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_chat import ChatAPI, register_blocks
from starlette_chat.providers.anthropic import AnthropicProvider
from starlette_cms import CMS, ImageField, ListField, RichTextField, TextField
from starlette_editor import Editor

# --- CMS setup -----------------------------------------------------------

cms = CMS(
    database_url="sqlite:///demo.db",
    auth="apikey",
    api_key="dev-secret",
)


@cms.block("hero")
class HeroBlock:
    title: str = TextField(required=True, label="Headline")
    subtitle: str = TextField(label="Supporting text")
    image: str = ImageField(label="Hero image")


@cms.block("rich_text")
class RichTextBlock:
    body: dict = RichTextField(required=True, label="Content")


@cms.document("page")
class PageDocument:
    title: str = TextField(required=True)
    slug: str = TextField(required=True)
    body: list = ListField(blocks=[HeroBlock, RichTextBlock])


# --- Media setup ---------------------------------------------------------

media = create_media_mount(
    bucket="demo-bucket",
    endpoint_url="https://storage.googleapis.com",  # replace with your bucket
    catalog_path="./demo_media.db",
    auth="none",  # open for demo purposes
)

# --- Chat setup ----------------------------------------------------------
# register_blocks must be called BEFORE cms.app is accessed (before Mounts are built)

register_blocks(cms)

_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
chat = ChatAPI(
    cms_base_url="http://localhost:8000/cms",
    cms_api_key=os.environ.get("CMS_API_KEY", "dev-secret"),
    provider=AnthropicProvider(api_key=_anthropic_key) if _anthropic_key else None,
)

# --- Editor setup --------------------------------------------------------
# Editor extends cms here — registers /api/editor-schema before cms.app is built

editor = Editor(cms=cms, media_base="/media")


# --- Lifespan ------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    async with cms.lifespan_context(app):
        async with media.lifespan_context(app):
            yield


# --- App -----------------------------------------------------------------

app = Starlette(
    routes=[
        Mount("/cms", app=cms.app),
        Mount("/media", app=media),
        Mount("/editor", app=editor.app),
        Mount("/chat", app=chat.app),
    ],
    lifespan=lifespan,
)

# StandardEditor at: http://localhost:8000/editor/shell
# CMS API at:        http://localhost:8000/cms/api/documents
# Media API at:      http://localhost:8000/media/assets
# Chat API at:       http://localhost:8000/chat/api/sessions
