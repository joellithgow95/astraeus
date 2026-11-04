"""
Tests for the SSRF-guarded image fetch behind the upload_asset MCP tool
(astraeus_media/mcp/fetch.py).

Literal IP hosts resolve without any network, so the refusal cases run for real.
Where a fetch has to succeed, the resolver and ``httpx.AsyncClient.send`` are
patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from astraeus_media.mcp import fetch
from astraeus_media.mcp.fetch import FetchError, fetch_image

pytest_plugins = ["anyio"]

PUBLIC_IP = "93.184.216.34"
PUBLIC_HOST = "img.example.com"
REAL_RESOLVE = fetch._resolve_public_ip


async def _resolve_public_host_only(host: str, port: int) -> str:
    """Pretend PUBLIC_HOST resolves publicly; everything else takes the real check."""
    if host == PUBLIC_HOST:
        return PUBLIC_IP
    return await REAL_RESOLVE(host, port)


def _response(
    status: int = 200,
    content: bytes = b"\x89PNG",
    content_type: str | None = "image/png",
    **headers: str,
) -> httpx.Response:
    if content_type is not None:
        headers["content-type"] = content_type
    return httpx.Response(status, content=content, headers=headers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://img.example.com/a.png",
        "https://127.0.0.1/a.png",
        "https://10.0.0.5/a.png",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/a.png",
        "file:///etc/passwd",
    ],
)
async def test_refuses_non_https_and_non_public_hosts(url: str) -> None:
    with pytest.raises(FetchError):
        await fetch_image(url)


@pytest.mark.anyio
async def test_returns_bytes_and_content_type() -> None:
    send = AsyncMock(return_value=_response(content_type="image/jpeg; charset=binary"))

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
    ):
        data, content_type = await fetch_image(f"https://{PUBLIC_HOST}/a.jpg")

    assert data == b"\x89PNG"
    assert content_type == "image/jpeg"


@pytest.mark.anyio
async def test_connects_to_the_checked_address_but_verifies_the_hostname() -> None:
    send = AsyncMock(return_value=_response())

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/a.png")

    request = send.call_args.args[0]
    assert request.url.host == PUBLIC_IP
    assert request.headers["Host"] == PUBLIC_HOST
    assert request.extensions["sni_hostname"] == PUBLIC_HOST


@pytest.mark.anyio
async def test_refuses_a_redirect_to_a_private_address() -> None:
    redirect = _response(302, content=b"", content_type=None, location="https://127.0.0.1/a.png")
    send = AsyncMock(return_value=redirect)

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
        pytest.raises(FetchError, match="public address"),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/a.png")


@pytest.mark.anyio
async def test_follows_a_redirect_to_another_public_url() -> None:
    redirect = _response(
        302, content=b"", content_type=None, location=f"https://{PUBLIC_HOST}/b.png"
    )
    send = AsyncMock(side_effect=[redirect, _response(content=b"final")])

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
    ):
        data, _ = await fetch_image(f"https://{PUBLIC_HOST}/a.png")

    assert data == b"final"


@pytest.mark.anyio
async def test_gives_up_after_too_many_redirects() -> None:
    loop = _response(302, content=b"", content_type=None, location=f"https://{PUBLIC_HOST}/a.png")
    send = AsyncMock(return_value=loop)

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
        pytest.raises(FetchError, match="redirects"),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/a.png")


@pytest.mark.anyio
async def test_refuses_something_that_is_not_an_image() -> None:
    send = AsyncMock(return_value=_response(content_type="text/html"))

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
        pytest.raises(FetchError, match="Not an image"),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/page")


@pytest.mark.anyio
async def test_refuses_an_image_over_the_cap() -> None:
    send = AsyncMock(return_value=_response(content=b"x" * 11))

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
        pytest.raises(FetchError, match="larger than"),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/big.png", max_bytes=10)


@pytest.mark.anyio
async def test_refuses_a_non_200_answer() -> None:
    send = AsyncMock(return_value=_response(404, content_type=None))

    with (
        patch.object(fetch, "_resolve_public_ip", _resolve_public_host_only),
        patch("httpx.AsyncClient.send", send),
        pytest.raises(FetchError, match="HTTP 404"),
    ):
        await fetch_image(f"https://{PUBLIC_HOST}/gone.png")
