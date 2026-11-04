"""Tests for the astraeus-portal package."""

from __future__ import annotations

from astraeus_portal import Portal, PortalApp
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# PortalApp tests
# ---------------------------------------------------------------------------


class TestPortalApp:
    def test_minimal(self) -> None:
        app = PortalApp(name="Test", path="/test")
        assert app.name == "Test"
        assert app.path == "/test"
        assert app.description == ""
        assert app.icon == ""

    def test_full(self) -> None:
        app = PortalApp(
            name="Editor",
            path="/editor/shell",
            description="Edit content",
            icon="✏️",
            tags=["content", "editor"],
        )
        assert app.icon == "✏️"
        assert app.tags == ["content", "editor"]


# ---------------------------------------------------------------------------
# Portal tests
# ---------------------------------------------------------------------------


class TestPortal:
    def test_build_app_no_auth(self) -> None:
        """Portal page renders without auth."""
        portal = Portal(
            apps=[
                PortalApp("Editor", "/editor/shell", "Edit content", "✏️"),
                PortalApp("Media", "/media/admin", "Manage media", "🖼️"),
            ],
            title="Test Portal",
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/html; charset=utf-8"
            assert "Test Portal" in resp.text
            assert "Editor" in resp.text
            assert "Media" in resp.text
            assert "/editor/shell" in resp.text
            assert "/media/admin" in resp.text

    def test_empty_apps_shows_helpful_message(self) -> None:
        """Empty apps list shows the 'no apps registered' empty state."""
        portal = Portal(apps=[])
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "No apps registered" in resp.text
            assert "PortalApp" in resp.text  # hints about usage

    def test_header_links(self) -> None:
        """Header links are rendered."""
        portal = Portal(
            apps=[PortalApp("Test", "/test")],
            header_links={"Docs": "https://docs.example.com", "GitHub": "https://github.com"},
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "docs.example.com" in resp.text
            assert "github.com" in resp.text

    def test_docs_url_in_footer(self) -> None:
        """Docs URL appears in footer."""
        portal = Portal(
            apps=[PortalApp("Test", "/test")],
            docs_url="https://docs.example.com",
            github_url="https://github.com/example/repo",
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "docs.example.com" in resp.text
            assert "github.com/example/repo" in resp.text
            assert "astraeus-portal" in resp.text  # version

    def test_titles_and_descriptions_rendered(self) -> None:
        """App titles and descriptions appear in cards."""
        portal = Portal(
            apps=[
                PortalApp("Editor", "/editor/shell", "Create and edit documents"),
            ],
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "Create and edit documents" in resp.text

    def test_package_info_section(self) -> None:
        """Package info section is rendered when show_package_info is True (default)."""
        portal = Portal(apps=[PortalApp("Test", "/test")])
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            # Should show at least itself
            assert "astraeus-portal" in resp.text

    def test_package_info_disabled(self) -> None:
        """Package info section is hidden when show_package_info is False."""
        portal = Portal(apps=[PortalApp("Test", "/test")], show_package_info=False)
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "Installed Packages" not in resp.text

    def test_tags_rendered(self) -> None:
        """Tags are rendered on the card."""
        portal = Portal(
            apps=[
                PortalApp("Editor", "/editor/shell", tags=["content", "editor"]),
            ],
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "content" in resp.text
            assert "editor" in resp.text


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestPortalAuth:
    def test_auth_blocked(self) -> None:
        """Unauthenticated request redirects to login."""
        portal = Portal(
            apps=[PortalApp("Test", "/test")],
            auth=lambda r: False,
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/", follow_redirects=False)
            assert resp.status_code == 302
            assert "/api/auth/login" in resp.headers["location"]

    def test_auth_allowed(self) -> None:
        """Authenticated request serves the page."""
        portal = Portal(
            apps=[PortalApp("Test", "/test")],
            auth=lambda r: True,
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "Test" in resp.text


# ---------------------------------------------------------------------------
# Keyboard nav JS tests (rendered, not runtime)
# ---------------------------------------------------------------------------


class TestKeyboardNav:
    def test_keyboard_nav_script_present(self) -> None:
        """Keyboard navigation JS snippet is included in the page."""
        portal = Portal(
            apps=[
                PortalApp("A", "/a"),
                PortalApp("B", "/b"),
                PortalApp("C", "/c"),
            ],
        )
        app = Starlette(routes=[Mount("/", app=portal.app)])

        with TestClient(app) as client:
            resp = client.get("/")
            assert "keydown" in resp.text
            assert "cards[n - 1].click()" in resp.text


class TestAppBar:
    def _portal(self, **kwargs) -> Portal:
        return Portal(
            apps=[
                PortalApp("Editor", "/editor/", "Edit content", "✏️"),
                PortalApp("Media", "/media/admin", "Manage media", "🖼️"),
            ],
            title="Joel",
            **kwargs,
        )

    def test_apps_json_lists_the_apps(self) -> None:
        app = Starlette(routes=[Mount("/", app=self._portal().app)])
        with TestClient(app) as client:
            resp = client.get("/apps.json")
        assert resp.status_code == 200
        assert resp.json() == {
            "title": "Joel",
            "apps": [
                {"name": "Editor", "path": "/editor/", "description": "Edit content", "icon": "✏️"},
                {
                    "name": "Media",
                    "path": "/media/admin",
                    "description": "Manage media",
                    "icon": "🖼️",
                },
            ],
        }

    def test_apps_json_takes_the_portal_auth(self) -> None:
        app = Starlette(routes=[Mount("/", app=self._portal(auth=lambda r: False).app)])
        with TestClient(app) as client:
            assert client.get("/apps.json").status_code == 401
            # The script holds no data, so it is public.
            assert client.get("/app-bar.js").status_code == 200

    def test_app_bar_script_is_served(self) -> None:
        app = Starlette(routes=[Mount("/admin", app=self._portal().app)])
        with TestClient(app) as client:
            resp = client.get("/admin/app-bar.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "apps.json" in resp.text

    def test_routes_splice_in_ahead_of_a_catch_all(self) -> None:
        """At the root beside a CMS mounted at "/": the portal answers its own
        three paths and everything else reaches the CMS."""
        from starlette.responses import PlainTextResponse

        cms = Starlette(routes=[Mount("/", app=PlainTextResponse("cms"))])
        portal = self._portal()
        app = Starlette(routes=[*portal.routes, Mount("/", app=cms)])
        with TestClient(app) as client:
            assert "Editor" in client.get("/").text
            assert client.get("/apps.json").json()["title"] == "Joel"
            assert client.get("/api/documents").text == "cms"
