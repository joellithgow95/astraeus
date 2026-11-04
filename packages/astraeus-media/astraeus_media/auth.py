"""
Authentication helpers for astraeus-media endpoints.

Three auth modes are supported (configured at ``MediakitConfig(auth=...)``):

- ``None``     — all requests pass (default)
- ``"apikey"`` — ``Authorization: Bearer {api_key}`` header required
- callable     — ``await mk.config.auth(request)`` must return ``True``

Usage in endpoint handlers::

    result = await require_auth(request, mk)
    if result is not None:
        return result  # 401 JSONResponse
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from astraeus_media.app import MediaKit


async def check_auth(request: Request, mk: MediaKit) -> bool:
    """
    Return ``True`` if the request is authorised, ``False`` otherwise.

    :param request: The incoming Starlette request.
    :param mk: The MediaKit instance whose ``config.auth`` and ``config.api_key`` apply.
    """
    auth = mk.config.auth

    if auth is None:
        return True

    if auth == "apikey":
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header[len("Bearer ") :]
        return token == mk.config.api_key

    if callable(auth):
        result = auth(request)
        # Support both sync and async callables
        if hasattr(result, "__await__"):
            return bool(await result)
        return bool(result)

    return False


async def require_auth(request: Request, mk: MediaKit) -> JSONResponse | None:
    """
    Check auth and return a 401 ``JSONResponse`` on failure, or ``None`` on success.

    Typical usage in a mutating endpoint::

        if (err := await require_auth(request, mk)) is not None:
            return err

    :param request: The incoming Starlette request.
    :param mk: The MediaKit instance.
    """
    authorised = await check_auth(request, mk)
    if not authorised:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return None
