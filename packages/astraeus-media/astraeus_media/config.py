"""astraeus-media configuration model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MediakitConfig(BaseModel):
    # Storage
    bucket: str
    provider: str = "s3"  # "s3" (AWS S3, R2, MinIO) or "gcs"
    endpoint_url: str | None = None  # None for AWS S3; set for R2/MinIO
    aws_access_key_id: str | None = None  # falls back to AWS_ACCESS_KEY_ID env var
    aws_secret_access_key: str | None = None
    region_name: str = "auto"

    # Catalog
    catalog_path: str = "./media_catalog.db"

    # Processing
    # True (default): the uploaded file is stored untouched and every rendition
    # is cut from it by IIIF. False: normalise on upload, replacing the original
    # with a stripped, capped, converted copy (the three settings below).
    keep_originals: bool = True
    max_dimension: int = 4096
    upload_format: str = "webp"
    upload_quality: int = 85
    strip_exif: bool = True

    # Auth
    api_key: str | None = None
    auth: Any | None = None  # callable (request) -> bool, or None

    # Admin UI
    # URL of the shared Astraeus design tokens, e.g. "/cms/static/tokens.css".
    # A URL, not an import — astraeus-media keeps no dependency on the CMS packages.
    # Unset means the admin uses its own built-in values, which is the right
    # default for a standalone astraeus-media deployment.
    tokens_url: str | None = None
    # Where to send a signed-out visitor to an admin page, with ?next= back to it,
    # e.g. the CMS login "/api/auth/login". Unset, the page answers 401.
    login_url: str | None = None
    # Where the Astraeus portal is mounted, e.g. "/". Set, the admin pages load
    # the portal's app bar ({portal_url}/app-bar.js), which links home and to the
    # other apps. Like tokens_url, a URL rather than an import.
    portal_url: str | None = None

    # Renditions
    # Where IIIF renditions are stored. Unset: beside the originals, in `bucket`.
    # Set: a separate bucket (same endpoint and credentials), which is how
    # originals stay private while renditions are served publicly.
    derivatives_bucket: str | None = None
    # Public base URL of derivatives_bucket, e.g. "https://media.example.com".
    # Set: IIIF redirects straight there, unsigned, so the CDN in front can cache
    # them. Unset: redirects are presigned URLs for derivatives_bucket (or bucket).
    derivatives_public_url: str | None = None
    # Sent with each stored rendition, so a CDN and browsers know how long to keep it.
    derivatives_cache_control: str = "public, max-age=2592000"

    # Serving
    public_read: bool = False
    presign_expires: int = 3600  # 1 hour
    upload_expires: int = 900  # 15 minutes

    # Paths
    mount_path: str = "/media"

    model_config = {"arbitrary_types_allowed": True}
