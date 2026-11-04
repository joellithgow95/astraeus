"""IIIF Image API Level 1 routes.

Implements IIIF Image API 2.1 Level 1 compliance:

- Region:   ``full`` | ``square`` | ``x,y,w,h``
- Size:     ``full`` | ``max`` | ``w,`` | ``,h`` | ``w,h`` | ``!w,h``
- Rotation: ``0`` | ``90`` | ``180`` | ``270``
- Quality:  ``default`` | ``color`` | ``gray``
- Format:   ``jpg`` | ``webp`` | ``png``

Derivatives are generated on demand and cached in the catalog.  Subsequent
requests are served via a 302 redirect to the stored derivative URL.

See https://iiif.io/api/image/2.1/ for the full specification.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

import obstore
import structlog
from PIL import Image
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from astraeus_media.app import MediaKit

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class IIIFParams:
    """Parsed IIIF Image API request parameters."""

    region: str  # "full" | "square" | "x,y,w,h"
    size: str  # "full" | "max" | "w," | ",h" | "w,h" | "!w,h"
    rotation: int  # 0 | 90 | 180 | 270
    quality: str  # "default" | "color" | "gray"
    format: str  # "jpg" | "webp" | "png"

    @property
    def canonical_key(self) -> str:
        """Canonical string key used as ``iiif_params`` in the derivatives table.

        Format: ``{region}/{size}/{rotation}/{quality}.{format}``
        """
        return f"{self.region}/{self.size}/{self.rotation}/{self.quality}.{self.format}"

    @property
    def content_type(self) -> str:
        """MIME type for the output format."""
        return {
            "jpg": "image/jpeg",
            "webp": "image/webp",
            "png": "image/png",
        }[self.format]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_VALID_ROTATIONS = {"0", "90", "180", "270"}
_VALID_QUALITIES = {"default", "color", "gray"}
_VALID_FORMATS = {"jpg", "webp", "png"}


def parse_iiif_params(
    region: str,
    size: str,
    rotation: str,
    quality: str,
    fmt: str,
) -> IIIFParams:
    """Parse and validate raw IIIF URL segments into an :class:`IIIFParams`.

    :raises ValueError: if any parameter is not a recognised IIIF value.
    """
    # --- region ---
    if region not in ("full", "square"):
        parts = region.split(",")
        if len(parts) != 4:
            raise ValueError(f"Invalid IIIF region: {region!r}")
        try:
            [int(p) for p in parts]
        except ValueError:
            raise ValueError(f"Invalid IIIF region (non-integer): {region!r}")

    # --- size ---
    if size not in ("full", "max"):
        # Allowed forms: "w,", ",h", "w,h", "!w,h"
        s = size.lstrip("!")
        parts = s.split(",")
        if len(parts) != 2:
            raise ValueError(f"Invalid IIIF size: {size!r}")
        w_str, h_str = parts
        try:
            if w_str:
                int(w_str)
            if h_str:
                int(h_str)
        except ValueError:
            raise ValueError(f"Invalid IIIF size (non-integer): {size!r}")
        if not w_str and not h_str:
            raise ValueError(f"Invalid IIIF size (both w and h empty): {size!r}")

    # --- rotation ---
    if rotation not in _VALID_ROTATIONS:
        raise ValueError(f"Invalid IIIF rotation: {rotation!r} (must be 0, 90, 180, or 270)")

    # --- quality ---
    if quality not in _VALID_QUALITIES:
        raise ValueError(f"Invalid IIIF quality: {quality!r}")

    # --- format ---
    if fmt not in _VALID_FORMATS:
        raise ValueError(f"Invalid IIIF format: {fmt!r}")

    return IIIFParams(
        region=region,
        size=size,
        rotation=int(rotation),
        quality=quality,
        format=fmt,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _sync_render_iiif(image_bytes: bytes, params: IIIFParams) -> tuple[bytes, str]:
    """Synchronous Pillow rendering — called via :func:`asyncio.to_thread`."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))

    # 0. Orient and normalise. Originals are stored untouched, so EXIF rotation is
    # applied here (region and size then refer to the displayed image), and modes
    # the web encoders can't write (CMYK, 16-bit) become RGB.
    img = ImageOps.exif_transpose(img) or img
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGB")

    # 1. Region
    if params.region == "full":
        pass  # no crop
    elif params.region == "square":
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    else:
        x, y, rw, rh = [int(p) for p in params.region.split(",")]
        img_w, img_h = img.size
        # Clamp to image bounds
        x = min(x, img_w)
        y = min(y, img_h)
        rw = min(rw, img_w - x)
        rh = min(rh, img_h - y)
        if rw > 0 and rh > 0:
            img = img.crop((x, y, x + rw, y + rh))

    # 2. Size
    w, h = img.size
    if params.size in ("full", "max"):
        pass  # no resize
    else:
        fit_in = params.size.startswith("!")
        s = params.size.lstrip("!")
        sw_str, sh_str = s.split(",")
        sw = int(sw_str) if sw_str else None
        sh = int(sh_str) if sh_str else None

        if fit_in:
            # Fit inside sw x sh, preserving aspect ratio
            assert sw is not None and sh is not None
            ratio = min(sw / w, sh / h)
            new_w = max(1, round(w * ratio))
            new_h = max(1, round(h * ratio))
        elif sw and sh:
            new_w, new_h = sw, sh
        elif sw:
            ratio = sw / w
            new_w = sw
            new_h = max(1, round(h * ratio))
        else:
            assert sh is not None
            ratio = sh / h
            new_h = sh
            new_w = max(1, round(w * ratio))

        from PIL import Image as _Image

        img = img.resize((new_w, new_h), _Image.Resampling.LANCZOS)

    # 3. Rotation
    if params.rotation != 0:
        # expand=True keeps the full image after rotation
        img = img.rotate(-params.rotation, expand=True)

    # 4. Quality
    if params.quality == "gray":
        img = img.convert("L")

    # 5. Encode
    buf = io.BytesIO()
    if params.format == "jpg":
        # JPEG can't handle alpha or palette modes
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, format="jpeg", quality=85)
        content_type = "image/jpeg"
    elif params.format == "webp":
        img.save(buf, format="webp", quality=85)
        content_type = "image/webp"
    else:  # png
        img.save(buf, format="png")
        content_type = "image/png"

    return buf.getvalue(), content_type


async def render_iiif(image_bytes: bytes, params: IIIFParams) -> tuple[bytes, str]:
    """Apply IIIF transforms to *image_bytes* and return ``(output_bytes, content_type)``.

    Offloads Pillow work to a thread pool.
    """
    import asyncio

    return await asyncio.to_thread(_sync_render_iiif, image_bytes, params)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


# The renditions the admin pages ask for (astraeus_media/admin) and the preview it shows
# straight after an upload (static/admin.js, 64px). Rendered when an upload is
# confirmed so the first view is a cache hit instead of a render.
ADMIN_THUMBNAIL = ("square", "256,")
ADMIN_DETAIL = ("full", "800,")
UPLOAD_PREVIEW = ("square", "64,")
PRERENDERED_RENDITIONS = (ADMIN_THUMBNAIL, ADMIN_DETAIL, UPLOAD_PREVIEW)


async def ensure_derivative(
    mk: MediaKit, key: str, params: IIIFParams, original_bytes: bytes | None = None
) -> str:
    """
    Make sure one rendition of *key* exists in storage and return its storage key.

    A catalog row alone does not mean the file exists: the row is inserted before
    rendering, so a request that arrives mid-render, or a render that failed, sees
    a row with no dimensions. Dimensions are only written once the file is stored,
    so ``width is None`` means "not ready" and the rendition is (re)made. Two
    requests racing do the same work and write the same bytes, which is harmless.

    :param original_bytes: The original, if the caller already has it, so several
        renditions of one upload download it once.
    """
    iiif_key = params.canonical_key
    derivative_key = f"derivatives/{key}/{iiif_key}"

    row, _created = await mk.catalog.get_or_create_derivative(
        key, iiif_key, derivative_key, format=params.format
    )
    if row.get("width") is not None:
        return derivative_key

    if original_bytes is None:
        result = await obstore.get_async(mk.storage._get_store(), key)
        original_bytes = bytes(await result.bytes_async())

    output_bytes, content_type = await render_iiif(original_bytes, params)
    await obstore.put_async(
        mk.derivative_storage._get_store(),
        derivative_key,
        output_bytes,
        attributes={
            "Content-Type": content_type,
            "Cache-Control": mk.config.derivatives_cache_control,
        },
    )

    try:
        with Image.open(io.BytesIO(output_bytes)) as out_img:
            out_w, out_h = out_img.size
    except Exception:
        out_w, out_h = None, None

    # Record the dimensions last: they are what marks the rendition as ready.
    assert mk.catalog._db is not None
    await mk.catalog._db.execute(
        "UPDATE derivatives SET width = ?, height = ? WHERE original_key = ? AND iiif_params = ?",
        (out_w, out_h, key, iiif_key),
    )
    await mk.catalog._db.commit()
    return derivative_key


async def prerender(mk: MediaKit, key: str) -> None:
    """
    Render the admin's renditions of *key* in the background after an upload.

    Failures are logged and dropped: a rendition that is not ready is simply made
    on first request, as before.
    """
    try:
        store = mk.storage._get_store()
        result = await obstore.get_async(store, key)
        original_bytes = bytes(await result.bytes_async())

        for region, size in PRERENDERED_RENDITIONS:
            params = parse_iiif_params(region, size, "0", "default", "webp")
            await ensure_derivative(mk, key, params, original_bytes)
    except Exception:
        logger.warning("astraeus_media.iiif.prerender_failed", key=key, exc_info=True)


def make_iiif_routes(mk: MediaKit) -> list[Route]:
    """Return the IIIF Image API route list for *mk*."""

    async def info_json(request: Request) -> Response:
        """``GET /iiif/{key:path}/info.json`` — IIIF Image Information."""
        key = request.path_params["key"]

        asset = await mk.catalog.get_asset(key)
        if asset is None:
            return JSONResponse({"error": "Asset not found"}, status_code=404)

        # Build the IIIF info.json response
        base_url = str(request.url).rstrip("/info.json")

        info: dict = {
            "@context": "http://iiif.io/api/image/2/context.json",
            "@id": base_url,
            "protocol": "http://iiif.io/api/image",
            "profile": [
                "http://iiif.io/api/image/2/level1.json",
                {
                    "formats": ["jpg", "webp", "png"],
                    "qualities": ["default", "color", "gray"],
                    "supports": [
                        "regionByPx",
                        "sizeByW",
                        "sizeByH",
                        "sizeByWh",
                        "sizeByForcedWh",
                        "rotationBy90s",
                    ],
                },
            ],
        }

        if asset.get("width"):
            info["width"] = asset["width"]
        if asset.get("height"):
            info["height"] = asset["height"]

        # Include tile/size hints if dimensions are known
        if asset.get("width") and asset.get("height"):
            w = asset["width"]
            h = asset["height"]
            info["sizes"] = [
                {"width": max(1, w // 2), "height": max(1, h // 2)},
                {"width": max(1, w // 4), "height": max(1, h // 4)},
            ]
            info["tiles"] = [{"width": 256, "scaleFactors": [1, 2, 4, 8]}]

        return JSONResponse(info, headers={"Content-Type": "application/ld+json"})

    async def image(request: Request) -> Response:
        """``GET /iiif/{key:path}/{region}/{size}/{rotation}/{quality}.{format}``

        1. Parse IIIF params → 400 on invalid.
        2. Look up ``(key, iiif_params)`` in ``catalog.get_or_create_derivative``.
        3. If cached → 302 redirect to stored derivative URL.
        4. If not cached → download original, render, upload, record, then 302.
        """
        key = request.path_params["key"]
        region = request.path_params["region"]
        size = request.path_params["size"]
        rotation = request.path_params["rotation"]
        quality_fmt = request.path_params["quality_format"]

        # Split "quality.format" (e.g. "default.webp")
        if "." not in quality_fmt:
            return JSONResponse({"error": "Invalid quality.format segment"}, status_code=400)
        quality, fmt = quality_fmt.rsplit(".", 1)

        # Parse and validate IIIF parameters
        try:
            params = parse_iiif_params(region, size, rotation, quality, fmt)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        # Verify asset exists
        asset = await mk.catalog.get_asset(key)
        if asset is None:
            return JSONResponse({"error": "Asset not found"}, status_code=404)

        derivative_storage_key = await ensure_derivative(mk, key, params)
        url = await mk.derivative_url(derivative_storage_key)
        return RedirectResponse(url, status_code=302)

    return [
        Route("/iiif/{key:path}/info.json", endpoint=info_json, methods=["GET"]),
        Route(
            "/iiif/{key:path}/{region}/{size}/{rotation}/{quality_format}",
            endpoint=image,
            methods=["GET"],
        ),
    ]
