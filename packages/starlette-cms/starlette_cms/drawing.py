"""
Hand-drawn shapes stored as SVG paths: the value shared by every field that
holds a drawing: ``DoodleField`` (a list of placed doodles) and
``DrawingField`` (one drawing, e.g. a taste shape).

A drawing is a viewBox and a list of paths, one per stroke, each with its own
stroke and fill, so an outline and a shaded area can sit in one drawing::

    {
        "viewbox": "0 0 120 80",
        "paths": [
            {"d": "M 4 4 Q 10 2 16 8", "stroke": "currentColor", "stroke_width": 2, "fill": "none"},
            {"d": "M 40 60 L 50 70 Z", "stroke": "none", "fill": "#c9a227", "opacity": 0.4},
        ],
        "symmetry": "vertical",
    }

The editor's ``components/drawing.js`` writes and renders the same shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# SVG path data: commands, numbers, separators. Nothing that could carry markup.
_PATH_DATA = r"^[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\-\s]*$"
_VIEWBOX = r"^\s*-?[\d.]+(?:[\s,]+-?[\d.]+){3}\s*$"
# A paint value: a colour name, #hex, currentColor, none, or rgb()/hsl().
_PAINT = r"^[#a-zA-Z0-9(),.%\s]{1,40}$"


class DrawingPath(BaseModel):
    """One stroke or filled area of a drawing."""

    model_config = ConfigDict(extra="forbid")

    d: str = Field(min_length=1, max_length=20_000, pattern=_PATH_DATA)
    stroke: str | None = Field(default=None, pattern=_PAINT)
    stroke_width: float | None = Field(default=None, ge=0, le=50)
    fill: str | None = Field(default=None, pattern=_PAINT)
    opacity: float | None = Field(default=None, ge=0, le=1)


class Drawing(BaseModel):
    """A hand-drawn shape: a viewBox and its paths."""

    model_config = ConfigDict(extra="forbid")

    viewbox: str = Field(pattern=_VIEWBOX)
    paths: list[DrawingPath] = Field(min_length=1, max_length=500)
    symmetry: Literal["vertical", "horizontal"] | None = None


class PlacementAxis(BaseModel):
    """A CSS length, e.g. ``{"value": 20, "unit": "%"}``."""

    model_config = ConfigDict(extra="forbid")

    value: float
    unit: Literal["%", "px", "rem", "em", "vw", "vh"]


class PlacementSpec(BaseModel):
    """One breakpoint's position. Only the axes present apply, as in CSS."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["static", "absolute"]
    top: PlacementAxis | None = None
    left: PlacementAxis | None = None
    right: PlacementAxis | None = None
    bottom: PlacementAxis | None = None
    width: PlacementAxis | None = None


class Placement(BaseModel):
    """Where a doodle sits on its anchor: a base spec and per-breakpoint overrides."""

    model_config = ConfigDict(extra="forbid")

    base: PlacementSpec
    overrides: dict[Literal["mobile", "tablet", "desktop"], PlacementSpec] | None = None


class Doodle(BaseModel):
    """A drawing placed on a page: one item of a ``DoodleField``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    drawing: Drawing
    placement: Placement
    z_index: int | None = None
