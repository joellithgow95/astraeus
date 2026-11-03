"""
Integration tests for starlette-editor: schema endpoint, shell route, config injection.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount

pytest_plugins = ["anyio"]


def _build_cms_and_editor():
    """Return a (cms, editor) pair for use in tests."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import RichTextField, TextField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("post")
    class Post:
        title: str = TextField(required=True, label="Title")
        body: dict = RichTextField(label="Body")

    editor = Editor(cms=cms, mount_path="/editor")
    return cms, editor


def test_editor_registers_extension_route():
    """Editor.__init__ must register /api/editor-schema on the CMS."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    Editor(cms=cms, mount_path="/editor")

    # Extension routes are stored as dicts on cms; find the editor-schema one
    ext_routes = [r for r in cms._extension_routes if "/api/editor-schema" in r["path"]]
    assert len(ext_routes) == 1, "Expected exactly one /api/editor-schema extension route"


@pytest.mark.anyio
async def test_editor_schema_endpoint_reachable():
    """GET /api/editor-schema returns 200 with a nodes key."""
    cms, _ = _build_cms_and_editor()
    app = Starlette(routes=[Mount("/", app=cms.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "blockTypes" in data


@pytest.mark.anyio
async def test_shell_serves_html():
    """GET /shell returns 200 with a DOCTYPE html document."""
    _, editor = _build_cms_and_editor()
    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 200
    assert "<!DOCTYPE html>" in resp.text


@pytest.mark.anyio
async def test_shell_injects_config():
    """The shell HTML contains the __EDITOR_CONFIG__ bootstrap object."""
    _, editor = _build_cms_and_editor()
    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert "__EDITOR_CONFIG__" in resp.text


@pytest.mark.anyio
async def test_shell_applies_the_stored_theme_before_paint():
    """The shell loads the CMS's theme bootstrap right after the tokens, in <head>."""
    _, editor = _build_cms_and_editor()
    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    head = resp.text.split("</head>")[0]
    assert head.index("/static/tokens.css") < head.index("/static/theme.js")


@pytest.mark.anyio
async def test_shell_injects_media_base_null():
    """Shell HTML contains mediaBase: null when Editor has no media_base."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor")  # no media_base

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 200
    assert "mediaBase: null" in resp.text


@pytest.mark.anyio
async def test_shell_injects_media_base():
    """Shell HTML contains the configured media_base path."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor", media_base="/media")

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 200
    assert '"/media"' in resp.text


# ---------------------------------------------------------------------------
# Auth path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shell_requires_auth_when_set():
    """auth=lambda r: False → GET /shell redirects to the login page."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor", auth=lambda r: False)

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    # A browser hitting the shell wants somewhere to go, not a bare 401, and
    # ?next= returns it to the shell once signed in.
    assert resp.status_code == 302
    assert resp.headers["location"] == "/api/auth/login?next=/editor/shell"


@pytest.mark.anyio
async def test_shell_allows_when_auth_passes():
    """auth=lambda r: True → GET /shell returns 200."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor", auth=lambda r: True)

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_shell_async_auth():
    """Async auth callable is awaited correctly."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    async def allow(request):
        return True

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor", auth=allow)

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_shell_async_auth_deny():
    """Async auth callable returning False → redirect to the login page."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    async def deny(request):
        return False

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    editor = Editor(cms=cms, mount_path="/editor", auth=deny)

    app = Starlette(routes=[Mount("/editor", app=editor.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/editor/shell")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/api/auth/login?next=/editor/shell"


# ---------------------------------------------------------------------------
# ProseMirrorBridge field type tests
# ---------------------------------------------------------------------------


def _schema_for_block(block_class, block_name="item"):
    """Build a CMS + Editor, return GET /api/editor-schema data for the given block name."""
    import asyncio

    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    cms.block(block_name)(block_class)
    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async def _fetch():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/api/editor-schema")

    return asyncio.get_event_loop().run_until_complete(_fetch()).json()


@pytest.mark.anyio
async def test_bridge_rich_text_field():
    """RichTextField → fields[name].field_type is 'rich_text'."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import RichTextField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        content: dict = RichTextField(label="Content")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    fields = data["blockTypes"]["article"]["fields"]
    assert fields["content"]["field_type"] == "rich_text"
    assert fields["content"]["label"] == "Content"


@pytest.mark.anyio
async def test_bridge_image_field():
    """ImageField → fields[name].field_type is 'image'."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import ImageField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        cover: str = ImageField(label="Cover Image")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    assert data["blockTypes"]["article"]["fields"]["cover"]["field_type"] == "image"


@pytest.mark.anyio
async def test_bridge_select_field():
    """SelectField → fields[name] includes choices list."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import SelectField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        status: str = SelectField(choices=["draft", "published", "archived"])

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    meta = data["blockTypes"]["article"]["fields"]["status"]
    assert meta["choices"] == ["draft", "published", "archived"]


@pytest.mark.anyio
async def test_bridge_number_field():
    """NumberField → fields[name] includes min_value and max_value."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import NumberField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("product")
    class Product:
        price: float = NumberField(min_value=0.0, max_value=9999.99, label="Price")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    meta = data["blockTypes"]["product"]["fields"]["price"]
    assert meta["min_value"] == 0.0
    assert meta["max_value"] == 9999.99


@pytest.mark.anyio
async def test_bridge_bool_field():
    """BoolField → fields[name] includes the default value."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import BoolField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        featured: bool = BoolField(default=True, label="Featured")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    meta = data["blockTypes"]["article"]["fields"]["featured"]
    assert meta["default"] is True


@pytest.mark.anyio
async def test_bridge_document_ref_field():
    """DocumentRef → fields[name].field_type is 'document_ref' with ref_block_type."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import DocumentRef
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("comment")
    class Comment:
        post_ref: str = DocumentRef(block_type="post", label="Post")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    meta = data["blockTypes"]["comment"]["fields"]["post_ref"]
    assert meta["field_type"] == "document_ref"
    assert meta["ref_block_type"] == "post"


@pytest.mark.anyio
async def test_bridge_empty_registry():
    """Empty registry → response has nodes, marks, blockTypes: {}."""
    from starlette_cms.app import CMS
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)
    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "marks" in data
    assert data["blockTypes"] == {}


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schema_endpoint_returns_display_order():
    """display_order metadata is propagated through to the schema response."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import TextField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        subtitle: str = TextField(display_order=2)
        title: str = TextField(display_order=1)

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    fields = data["blockTypes"]["article"]["fields"]
    assert fields["title"]["display_order"] == 1
    assert fields["subtitle"]["display_order"] == 2


@pytest.mark.anyio
async def test_schema_endpoint_returns_labels():
    """label metadata is propagated through to the schema response."""
    from starlette_cms.app import CMS
    from starlette_cms.fields import TextField
    from starlette_editor.app import Editor

    cms = CMS(database_url="sqlite:///:memory:", auth="none", read_auth=False)

    @cms.block("article")
    class Article:
        headline: str = TextField(label="Article Headline")

    Editor(cms=cms, mount_path="/editor")
    app = Starlette(routes=[Mount("/", app=cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/editor-schema")

    data = resp.json()
    fields = data["blockTypes"]["article"]["fields"]
    assert fields["headline"]["label"] == "Article Headline"
