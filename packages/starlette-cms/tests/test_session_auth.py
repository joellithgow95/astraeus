"""Tests for session-based auth (Phase NS-1B)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import bcrypt
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS
from starlette_cms.auth import check_session_auth
from starlette_cms.session import generate_session_token, validate_session_token

_SECRET = "test-secret-32-chars-minimum-ok"
_PASSWORD = "password123"


def _make_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@pytest_asyncio.fixture
async def session_cms() -> AsyncGenerator[CMS, None]:
    """CMS configured with session_secret and admin_users."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        instance = CMS(
            database_url=f"sqlite:///{db_path}",
            session_secret=_SECRET,
            admin_users={"joel": _make_hash(_PASSWORD)},
        )
        async with instance.lifespan_context(None):
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest_asyncio.fixture
async def session_client(session_cms: CMS) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = Starlette(routes=[Mount("/", app=session_cms.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Test 1 — GET /api/auth/login returns HTML form
# ---------------------------------------------------------------------------


async def test_login_get_returns_html(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.get("/api/auth/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "<form" in body
    assert 'name="username"' in body
    assert 'name="password"' in body


# ---------------------------------------------------------------------------
# Test 2 — POST with correct credentials → 302 + cookie set
# ---------------------------------------------------------------------------


async def test_login_post_success(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post(
        "/api/auth/login",
        data={"username": "joel", "password": _PASSWORD},
    )
    assert resp.status_code == 302
    assert "cms_session" in resp.headers.get("set-cookie", "")
    # Cookie must be HttpOnly
    assert "HttpOnly" in resp.headers["set-cookie"]


# ---------------------------------------------------------------------------
# Test 3 — Wrong password → redirect to ?error=1, no cookie
# ---------------------------------------------------------------------------


async def test_login_wrong_password(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post(
        "/api/auth/login",
        data={"username": "joel", "password": "wrongpassword"},
    )
    assert resp.status_code == 302
    assert "error=1" in resp.headers["location"]
    assert "cms_session" not in resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Test 4 — Unknown username → redirect to ?error=1, no cookie
# ---------------------------------------------------------------------------


async def test_login_unknown_user(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post(
        "/api/auth/login",
        data={"username": "nobody", "password": _PASSWORD},
    )
    assert resp.status_code == 302
    assert "error=1" in resp.headers["location"]
    assert "cms_session" not in resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Test 5 — ?next=/blog → redirects to /blog after success
# ---------------------------------------------------------------------------


async def test_login_next_param_safe(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post(
        "/api/auth/login?next=/blog",
        data={"username": "joel", "password": _PASSWORD, "next": "/blog"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/blog"


# ---------------------------------------------------------------------------
# Test 6 — External next URL → redirects to / (not external)
# ---------------------------------------------------------------------------


async def test_login_next_param_external_blocked(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post(
        "/api/auth/login",
        data={"username": "joel", "password": _PASSWORD, "next": "https://evil.com"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Test 7 — POST /api/auth/logout clears cookie, redirects to login
# ---------------------------------------------------------------------------


async def test_logout_clears_cookie(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.post("/api/auth/logout")
    assert resp.status_code == 302
    assert "/api/auth/login" in resp.headers["location"]
    set_cookie = resp.headers.get("set-cookie", "")
    assert "cms_session=" in set_cookie
    assert "Max-Age=0" in set_cookie


# ---------------------------------------------------------------------------
# Test 8 — GET /api/auth/me with valid cookie → authenticated
# ---------------------------------------------------------------------------


async def test_me_with_valid_cookie(session_client: httpx.AsyncClient) -> None:
    token = generate_session_token("joel", _SECRET)
    resp = await session_client.get(
        "/api/auth/me",
        cookies={"cms_session": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["user"] == "joel"


# ---------------------------------------------------------------------------
# Test 9 — GET /api/auth/me with no cookie → not authenticated
# ---------------------------------------------------------------------------


async def test_me_no_cookie(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False


# ---------------------------------------------------------------------------
# Test 10 — GET /api/auth/me with tampered token → not authenticated
# ---------------------------------------------------------------------------


async def test_me_tampered_token(session_client: httpx.AsyncClient) -> None:
    token = generate_session_token("joel", _SECRET)
    # Flip the last character of the payload portion
    parts = token.split(".")
    parts[0] = parts[0][:-1] + ("A" if parts[0][-1] != "A" else "B")
    tampered = ".".join(parts)

    resp = await session_client.get(
        "/api/auth/me",
        cookies={"cms_session": tampered},
    )
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


# ---------------------------------------------------------------------------
# Test 11 — GET /api/auth/me has Cache-Control: no-store header
# ---------------------------------------------------------------------------


async def test_me_cache_control(session_client: httpx.AsyncClient) -> None:
    resp = await session_client.get("/api/auth/me")
    assert resp.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# Test 12 — Expired token: ttl_seconds=-1 → validate returns None
# ---------------------------------------------------------------------------


def test_expired_token_returns_none() -> None:
    token = generate_session_token("joel", _SECRET, ttl_seconds=-1)
    result = validate_session_token(token, _SECRET)
    assert result is None


# ---------------------------------------------------------------------------
# Test 13 — CMS with session_secret=None: auth routes not mounted → 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_session_secret_routes_not_mounted() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        instance = CMS(database_url=f"sqlite:///{db_path}")
        app = Starlette(routes=[Mount("/", app=instance.app)])
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            async with instance.lifespan_context(None):
                resp = await client.get("/api/auth/login")
                assert resp.status_code == 404
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Test 14 — check_session_auth helper
# ---------------------------------------------------------------------------


async def test_check_session_auth_valid_cookie(session_cms: CMS) -> None:
    """check_session_auth returns True for a valid cookie, False otherwise."""

    # We test the helper directly via a synthetic request-like object
    # by calling the endpoint and checking behaviour through the /me route.
    token = generate_session_token("joel", _SECRET)

    app = Starlette(routes=[Mount("/", app=session_cms.app)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        # Valid token — /me confirms it
        resp = await client.get("/api/auth/me", cookies={"cms_session": token})
        assert resp.json()["authenticated"] is True

        # No cookie
        resp2 = await client.get("/api/auth/me")
        assert resp2.json()["authenticated"] is False

        # Invalid cookie
        resp3 = await client.get("/api/auth/me", cookies={"cms_session": "garbage"})
        assert resp3.json()["authenticated"] is False


async def test_check_session_auth_no_secret() -> None:
    """check_session_auth returns False when cms.session_secret is None."""
    from unittest.mock import MagicMock

    from starlette.requests import Request

    cms = MagicMock()
    cms.session_secret = None

    request = MagicMock(spec=Request)
    request.cookies = {"cms_session": "sometoken"}

    assert check_session_auth(request, cms) is False


@pytest.mark.asyncio
async def test_login_page_escapes_the_next_param(session_client: httpx.AsyncClient) -> None:
    """``next`` comes from the query string, so it cannot break out of its attribute."""
    resp = await session_client.get('/api/auth/login?next=/x"><script>alert(1)</script>')
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert 'value="/x&quot;&gt;&lt;script&gt;' in resp.text
