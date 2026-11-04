"""
Fetch a remote image for the ``upload_asset`` MCP tool.

The MCP server often runs inside a cluster, so fetching an arbitrary URL is an
SSRF risk: ``http://internal-service:8000/`` or a cloud metadata address would
otherwise be reachable by whoever can call the tool.  This module is the guard:

- https only
- the host must resolve to public addresses only (no loopback, private,
  link-local, or metadata ranges)
- the request connects to the address that was checked, so a second DNS answer
  cannot swap in a private one (TLS still verifies the original hostname)
- redirects are followed by hand, each hop checked the same way
- the body is capped and must be an ``image/*`` content type
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

HTTPS_PORT = 443
MAX_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 20.0
# Many image hosts refuse the default httpx agent outright.
USER_AGENT = "astraeus-media-mcp (+https://github.com/ASneakyToast/astraeus)"


class FetchError(Exception):
    """The URL was refused or could not be fetched; the message is safe to show."""


async def _resolve_public_ip(host: str, port: int) -> str:
    """Return one address for *host*, raising unless every address is public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"Cannot resolve {host!r}") from exc

    addresses = [str(info[4][0]) for info in infos]
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise FetchError(f"{host!r} does not resolve to a public address")
    return addresses[0]


async def _read_image(response: httpx.Response, max_bytes: int) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for a 200 image response within the cap."""
    if response.status_code != httpx.codes.OK:
        raise FetchError(f"Source answered HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise FetchError(f"Not an image: {content_type or 'no content type'}")

    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"Image is larger than {max_bytes} bytes")

    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise FetchError(f"Image is larger than {max_bytes} bytes")
    return bytes(body), content_type


async def fetch_image(url: str, max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """
    Download an image from a public https URL.

    :param url: The https URL to fetch.
    :param max_bytes: Reject bodies larger than this (default 25 MiB).
    :returns: ``(image_bytes, content_type)``.
    :raises FetchError: If the URL is refused, unreachable, not an image, or too big.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise FetchError("Only https URLs are accepted")

            port = parts.port or HTTPS_PORT
            ip = await _resolve_public_ip(parts.hostname, port)
            netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            host_header = parts.hostname if port == HTTPS_PORT else f"{parts.hostname}:{port}"

            request = client.build_request(
                "GET",
                parts._replace(netloc=netloc).geturl(),
                headers={"Host": host_header, "User-Agent": USER_AGENT},
                extensions={"sni_hostname": parts.hostname},
            )
            try:
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise FetchError(f"Could not fetch the image: {type(exc).__name__}") from exc

            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without a Location header")
                    url = urljoin(url, location)
                    continue
                return await _read_image(response, max_bytes)
            finally:
                await response.aclose()

    raise FetchError("Too many redirects")
