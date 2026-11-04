"""Tests for the compose_app convenience function."""
# Tests live in the same directory as test_portal.py to share pytest config.

from __future__ import annotations

from astraeus_portal import PortalApp
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# compose_app tests — uses Starlette apps as stand-ins for real components
# ---------------------------------------------------------------------------


class TestComposeApp:
    def test_compose_no_components(self) -> None:
        """compose_app with no components returns an app with empty portal."""
        from astraeus_portal.compose import compose_app

        app = compose_app()
        assert app is not None

        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "No apps registered" in resp.text

    def test_compose_with_cms_server_spy(self) -> None:
        """compose_app with a cms-like object mounts it and shows it in the portal."""
        from astraeus_portal.compose import compose_app

        # Build a minimal starlette app to stand in for CMS
        fake_cms_app = _make_minimal_app("cms-root")

        class FakeCMS:
            @property
            def app(self):
                return fake_cms_app

        cms = FakeCMS()

        app = compose_app(cms=cms)
        with TestClient(app) as client:
            # CMS API card is in portal
            resp = client.get("/")
            assert resp.status_code == 200
            assert "CMS API" in resp.text
            assert "/cms/api/documents" in resp.text
            # CMS mount works
            resp2 = client.get("/cms/")
            assert resp2.status_code == 200
            assert "cms-root" in resp2.text

    def test_compose_with_editor(self) -> None:
        """compose_app with editor shows Editor card."""
        from astraeus_portal.compose import compose_app

        fake_editor = _make_minimal_app("editor-root")

        class FakeEditor:
            @property
            def app(self):
                return fake_editor

        app = compose_app(editor=FakeEditor())
        with TestClient(app) as client:
            resp = client.get("/")
            assert "Editor" in resp.text
            assert 'href="/editor/"' in resp.text

    def test_compose_full_stack(self) -> None:
        """All components appear in the portal and are mounted."""
        from astraeus_portal.compose import compose_app

        class FakeComp:
            @property
            def app(self):
                return _make_minimal_app("ok")

        app = compose_app(
            cms=FakeComp(),
            editor=FakeComp(),
            media=FakeComp(),
            chat=FakeComp(),
            gateways=FakeComp(),
            feedback=FakeComp(),
            portal_title="Full Stack",
            docs_url="https://docs.example.com",
        )
        with TestClient(app) as client:
            resp = client.get("/")
            assert "Full Stack" in resp.text
            assert "CMS API" in resp.text
            assert "Editor" in resp.text
            assert "Media" in resp.text
            assert "Gateways" in resp.text
            assert "Feedback" in resp.text
            # Chat has no page of its own, so no card (it is still mounted).
            assert "Chat" not in resp.text
            assert "docs.example.com" in resp.text
            # All mounts work
            for path in ["/cms/", "/editor/", "/media/", "/chat/", "/gateways/", "/feedback/"]:
                r = client.get(path)
                assert r.status_code == 200, f"Mount {path} failed"

    def test_compose_extra_apps(self) -> None:
        """Extra custom apps appear in the portal."""
        from astraeus_portal.compose import compose_app

        app = compose_app(
            extra_apps=[
                PortalApp("Custom", "/custom", "A custom app"),
            ],
        )
        with TestClient(app) as client:
            resp = client.get("/")
            assert "Custom" in resp.text
            assert "/custom" in resp.text

    def test_lifespan_composition(self) -> None:
        """Lifespans from multiple components are composed without error."""
        from contextlib import asynccontextmanager

        from astraeus_portal.compose import compose_app

        @asynccontextmanager
        async def dummy_lifespan_context(app):
            yield

        class FakeWithLife:
            @property
            def app(self):
                return _make_minimal_app("ok")

            @property
            def lifespan_context(self):
                return dummy_lifespan_context

        cms = FakeWithLife()
        media = FakeWithLife()

        app = compose_app(cms=cms, media=media)
        # Startup/shutdown should work without error
        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200

    def test_mount_path_customization(self) -> None:
        """Custom mount paths are respected."""
        from astraeus_portal.compose import compose_app

        class FakeComp:
            @property
            def app(self):
                return _make_minimal_app("ok")

        app = compose_app(
            cms=FakeComp(),
            editor=FakeComp(),
            cms_mount="/admin/cms",
            editor_mount="/admin/editor",
        )
        with TestClient(app) as client:
            resp = client.get("/")
            assert "/admin/cms/api/documents" in resp.text
            assert 'href="/admin/editor/"' in resp.text
            # Custom mounts work
            assert client.get("/admin/cms/").status_code == 200
            assert client.get("/admin/editor/").status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_app(body: str = "ok"):
    """Build a minimal Starlette app that returns *body* on GET /."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    return Starlette(routes=[Route("/", endpoint=lambda r: PlainTextResponse(body))])


class TestComposeLinksToPortal:
    def test_sets_portal_url_on_components_that_take_one(self) -> None:
        """Every admin page gets the app bar: compose_app hands each its portal_url."""
        from types import SimpleNamespace

        from astraeus_portal.compose import compose_app
        from starlette.applications import Starlette

        class Admin:
            def __init__(self, portal_url: str | None = None) -> None:
                self.app = Starlette()
                self.portal_url = portal_url

        editor = Admin()
        gateways = Admin(portal_url="/elsewhere")
        media = SimpleNamespace(app=Starlette(), config=SimpleNamespace(portal_url=None))

        compose_app(editor=editor, gateways=gateways, media=media, portal_mount="/admin")

        assert editor.portal_url == "/admin"
        assert gateways.portal_url == "/elsewhere"  # one it was given is kept
        assert media.config.portal_url == "/admin"
