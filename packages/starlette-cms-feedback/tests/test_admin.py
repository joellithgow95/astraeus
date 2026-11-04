"""FeedbackAdmin — the shell page and its auth redirect."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS
from starlette_cms_feedback import FeedbackAdmin

pytestmark = pytest.mark.asyncio


async def test_shell_serves_html_with_config(feedback_app):
    resp = await feedback_app["client"].get("/feedback/shell")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "/cms/static/tokens.css" in resp.text

    match = re.search(r"var CONFIG = (\{.*?\});", resp.text, re.S)
    assert match is not None
    config = json.loads(match.group(1))
    assert config["cmsBase"] == "/cms"
    assert config["kinds"] == ["bug", "feedback", "idea"]
    assert config["statuses"][0] == "new"
    assert "starlette-editor" in config["components"]


async def test_shell_escapes_script_breakout_in_components(tmp_path: Path):
    cms = CMS(
        database_url=f"sqlite:///{tmp_path / 'content.db'}",
        auth="apikey",
        api_key="k",
        mount_path="/cms",
    )
    admin = FeedbackAdmin(
        cms=cms, components=["</script><script>alert(1)</script>"], db_path=tmp_path / "f.db"
    )
    root = Starlette(routes=[Mount("/cms", app=cms.app), Mount("/feedback", app=admin.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=root), base_url="http://testserver"
    ) as client:
        resp = await client.get("/feedback/shell")

    assert "</script><script>alert(1)" not in resp.text


async def test_shell_redirects_when_auth_fails(tmp_path: Path):
    cms = CMS(
        database_url=f"sqlite:///{tmp_path / 'content.db'}",
        auth="apikey",
        api_key="k",
        mount_path="/cms",
    )
    admin = FeedbackAdmin(cms=cms, auth=lambda request: False, db_path=tmp_path / "f.db")
    root = Starlette(routes=[Mount("/cms", app=cms.app), Mount("/feedback", app=admin.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=root), base_url="http://testserver"
    ) as client:
        resp = await client.get("/feedback/shell")

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/api/auth/login?next=")


async def test_shell_is_also_at_the_mount_root(feedback_app):
    resp = await feedback_app["client"].get("/feedback/")
    assert resp.status_code == 200
    assert "var CONFIG" in resp.text


async def test_shell_loads_the_app_bar_only_when_given_a_portal(feedback_app):
    resp = await feedback_app["client"].get("/feedback/shell")
    assert "app-bar.js" not in resp.text

    feedback_app["admin"].portal_url = "/"
    resp = await feedback_app["client"].get("/feedback/shell")
    assert '<script src="/app-bar.js" defer></script>' in resp.text
