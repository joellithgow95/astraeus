"""IIIF Image API routes."""

from __future__ import annotations

from astraeus_media.routes.iiif import IIIFParams, make_iiif_routes, parse_iiif_params, render_iiif

__all__ = ["IIIFParams", "make_iiif_routes", "parse_iiif_params", "render_iiif"]
