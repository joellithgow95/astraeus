"""Tests for the shared design-token stylesheet served by starlette-cms.

Every admin surface consumes this file (ADR 020 §4), so it has to be reachable
from the CMS mount rather than copied per package.
"""

from __future__ import annotations

import re

import httpx
import pytest
from httpx import ASGITransport
from starlette_cms import CMS


@pytest.fixture
def cms() -> CMS:
    return CMS(database_url="sqlite:///:memory:", auth=None, read_auth=False)


@pytest.mark.anyio
async def test_tokens_stylesheet_is_served(cms: CMS) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        res = await client.get("/static/tokens.css")

    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]


@pytest.mark.anyio
async def test_tokens_define_the_shared_custom_properties(cms: CMS) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        body = (await client.get("/static/tokens.css")).text

    # The floor that stops iOS zooming on focus, and the touch target size —
    # both load-bearing for ADR 022 rather than cosmetic.
    assert "--font-size-input: 16px" in body
    assert "--target-min:          44px" in body
    assert "@media (hover: hover) and (pointer: fine)" in body


@pytest.mark.anyio
async def test_unknown_static_file_is_not_found(cms: CMS) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        res = await client.get("/static/nope.css")

    assert res.status_code == 404


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance for an sRGB hex colour."""
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    high, low = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.anyio
async def test_text_tokens_are_readable_on_the_base_surface(cms: CMS) -> None:
    """Every text token clears WCAG AA against the background it sits on.

    The palette was originally extracted verbatim from a stylesheet tuned for a
    desktop in a dark room: --text-muted sat at 2.6:1 and was illegible on a
    phone outdoors. These are the values that complaint produced, pinned so the
    next palette change has to stay legible.
    """
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        body = (await client.get("/static/tokens.css")).text

    def token(name: str) -> str:
        match = re.search(rf"^\s*{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}});", body, re.M)
        assert match is not None, f"{name} not found"
        return match.group(1)

    background = token("--bg-base")
    for name, floor in [
        ("--text-primary", 4.5),
        ("--text-secondary", 4.5),
        # Placeholders, dates and empty-state copy are content, not decoration.
        ("--text-muted", 4.5),
        # Carries words ("27 unpublished"), so it needs the body-text floor too.
        ("--pending", 4.5),
        # Control outlines only need WCAG 1.4.11 non-text contrast.
        ("--border-default", 3.0),
    ]:
        ratio = _contrast(token(name), background)
        assert ratio >= floor, f"{name} is {ratio:.2f}:1 against --bg-base, needs {floor}:1"


def _light_blocks(body: str) -> tuple[str, str]:
    """The two copies of the light ramp: under prefers-color-scheme, and pinned."""
    media = re.search(
        r"@media \(prefers-color-scheme: light\) \{\s*"
        r':root:not\(\[data-astraeus-theme="dark"\]\) \{(.*?)\}\s*\}',
        body,
        re.S,
    )
    pinned = re.search(r'^\[data-astraeus-theme="light"\] \{(.*?)^\}', body, re.S | re.M)
    assert media is not None and pinned is not None
    return media.group(1), pinned.group(1)


def _declarations(block: str) -> list[str]:
    return [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith(("--", "color-scheme"))
    ]


@pytest.mark.anyio
async def test_light_ramp_follows_the_system_and_can_be_pinned(cms: CMS) -> None:
    """The system-preference copy and the pinned copy of the light ramp match.

    CSS cannot share one declaration list between a media query and an
    attribute selector, so the ramp is written twice; this keeps the copies
    from drifting apart.
    """
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        body = (await client.get("/static/tokens.css")).text

    media, pinned = _light_blocks(body)
    assert _declarations(media) == _declarations(pinned)
    assert "color-scheme: light;" in _declarations(pinned)


@pytest.mark.anyio
async def test_light_text_tokens_are_readable(cms: CMS) -> None:
    """The light ramp clears the same floors as the dark one."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        body = (await client.get("/static/tokens.css")).text

    _, pinned = _light_blocks(body)

    def token(name: str) -> str:
        match = re.search(rf"^\s*{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}});", pinned, re.M)
        assert match is not None, f"{name} not found in the light ramp"
        return match.group(1)

    background = token("--bg-base")
    for name, floor in [
        ("--text-primary", 4.5),
        ("--text-secondary", 4.5),
        ("--text-muted", 4.5),
        ("--pending", 4.5),
        ("--border-default", 3.0),
    ]:
        for ground in (background, token("--bg-elevated")):
            ratio = _contrast(token(name), ground)
            assert ratio >= floor, (
                f"light {name} is {ratio:.2f}:1 against {ground}, needs {floor}:1"
            )


@pytest.mark.anyio
async def test_theme_bootstrap_script_is_served(cms: CMS) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=cms.app), base_url="http://test"
    ) as client:
        res = await client.get("/static/theme.js")

    assert res.status_code == 200
    assert "astraeus-theme" in res.text
