"""Digitise a site plan image into GeoJSON UniCAD's site_import reads.

Points are pixels of one image (a page rendered by source_page_image, or an image source). The scale comes from a known distance, an
explicit m/px, or, as on a scanned plan without a scale bar, a least-squares fit to the areas the plan states. Polygons with a stated area
can be normalised about their centroid to match it exactly (removes tracing error, keeps the shape). The drawing is placed so a chosen
point (default: the centroid of the boundary features) sits at the anchor lon/lat, north up as drawn. Every feature name carries ASSUMED:
a traced plan is not a survey.
"""

import io
import math
from pathlib import Path
from typing import Any, Literal

from PIL import Image as PILImage, ImageDraw
from pydantic import BaseModel, Field
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from uniplan.store import SiteFeature, SitePlan

KINDS = ("boundary", "exclusion", "building", "existing_structure", "obstacle", "water", "switchyard", "road", "transmission_line", "setback_line")
LINES = {"road", "transmission_line", "setback_line"}
AREA_TOLERANCE = 0.02  # stated vs traced area after scaling, before normalising


class TraceFeature(BaseModel):
    name: str = Field(description="What it is and where it comes from, e.g. 'Block 1 solar area (Annex 13, 3.03 acre)'; ASSUMED is added when missing")
    kind: Literal["boundary", "exclusion", "building", "existing_structure", "obstacle", "water", "switchyard", "road", "transmission_line", "setback_line"]
    points: list[list[float]] = Field(description="[[x, y], ...] in pixels of the image; polygons need >= 3 points (do not repeat the first), lines >= 2")
    stated_area_m2: float | None = Field(None, description="Area the documents state for this polygon (1 acre = 4046.856 m2); drives the scale and normalisation")
    width_m: float | None = Field(None, description="Lines: corridor width, e.g. a transmission line's RoW")
    normalise: bool = Field(True, description="Scale this polygon about its centroid so its area equals stated_area_m2 exactly")


class KnownDistance(BaseModel):
    p1: list[float]
    p2: list[float]
    metres: float


def _lonlat(anchor_lon: float, anchor_lat: float, x: float, y: float) -> list[float]:
    lat0 = math.radians(anchor_lat)
    m_lat = 111132.92 - 559.82 * math.cos(2 * lat0) + 1.175 * math.cos(4 * lat0)
    m_lon = 111412.84 * math.cos(lat0) - 93.5 * math.cos(3 * lat0)
    return [round(anchor_lon + x / m_lon, 8), round(anchor_lat + y / m_lat, 8)]


def trace(
    features: list[TraceFeature],
    anchor_lon: float,
    anchor_lat: float,
    m_per_px: float | None = None,
    known_distance: KnownDistance | None = None,
    anchor_px: list[float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (geojson, report)."""
    if not features:
        raise ValueError("no features to trace")
    for f in features:
        need = 2 if f.kind in LINES else 3
        if len(f.points) < need or any(len(p) != 2 for p in f.points):
            raise ValueError(f"{f.name}: a {f.kind} needs >= {need} [x, y] points")
        if f.kind not in LINES and not Polygon(f.points).is_valid:
            raise ValueError(f"{f.name}: the outline crosses itself; list the points in order around it")
    stated = [f for f in features if f.stated_area_m2 and f.kind not in LINES]
    if m_per_px:
        s, scale_from = m_per_px, f"given {m_per_px:g} m/px"
    elif known_distance:
        px = math.dist(known_distance.p1, known_distance.p2)
        if px <= 0:
            raise ValueError("known_distance: p1 and p2 are the same point")
        s, scale_from = known_distance.metres / px, f"known distance {known_distance.metres:g} m over {px:.1f} px"
    elif stated:
        s = math.sqrt(sum(f.stated_area_m2 for f in stated) / sum(Polygon(f.points).area for f in stated))  # type: ignore[misc]
        scale_from = "least-squares fit to the stated areas of " + ", ".join(f.name for f in stated)
    else:
        raise ValueError("no scale: give m_per_px, a known_distance, or stated_area_m2 on at least one polygon")

    rows, warnings, pts_by = [], [], {}
    for f in features:
        pts = [tuple(p) for p in f.points]
        row: dict[str, Any] = {"name": f.name, "kind": f.kind}
        if f.kind in LINES:
            row["length_m"] = round(LineString(pts).length * s, 1)
        else:
            traced = Polygon(pts).area * s * s
            row["traced_area_m2"] = round(traced, 1)
            if f.stated_area_m2:
                dev = traced / f.stated_area_m2 - 1
                row["stated_area_m2"] = f.stated_area_m2
                row["traced_vs_stated_pct"] = round(100 * dev, 1)
                if abs(dev) > 0.15:
                    warnings.append(f"{f.name}: traced area is {100 * dev:+.0f} % off the stated area; check the trace against the image (site_preview) before trusting the scale")
                if f.normalise:
                    p = Polygon(pts)
                    k = math.sqrt(f.stated_area_m2 / (p.area * s * s))
                    c = p.centroid
                    pts = [(c.x + (x - c.x) * k, c.y + (y - c.y) * k) for x, y in pts]
                    row["normalise_factor"] = round(k, 4)
            row["area_m2"] = round(Polygon(pts).area * s * s, 1)
        pts_by[f.name] = pts
        rows.append(row)

    if anchor_px:
        ax, ay = anchor_px
        anchor_desc = f"pixel {anchor_px}"
    else:
        bounds = [Polygon(pts_by[f.name]) for f in features if f.kind == "boundary"] or [Polygon(pts_by[f.name]) for f in features if f.kind not in LINES]
        if not bounds:
            raise ValueError("no polygon to anchor on: give anchor_px")
        c = unary_union(bounds).centroid
        ax, ay, anchor_desc = c.x, c.y, "centroid of the boundary features"

    def geo(pts: list[tuple[float, float]]) -> list[list[float]]:
        return [_lonlat(anchor_lon, anchor_lat, (x - ax) * s, -(y - ay) * s) for x, y in pts]  # image y runs down; north is up

    out = []
    for f in features:
        name = f.name if "ASSUMED" in f.name.upper() else f"ASSUMED {f.name}"
        props: dict[str, Any] = {"name": name, "kind": f.kind}
        if f.width_m:
            props["width_m"] = f.width_m
        if f.stated_area_m2:
            props["stated_area_m2"] = f.stated_area_m2
        ring = geo(pts_by[f.name])
        geom = {"type": "LineString", "coordinates": ring} if f.kind in LINES else {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
        out.append({"type": "Feature", "properties": props, "geometry": geom})
    gj = {"type": "FeatureCollection", "features": out}
    report = {"m_per_px": round(s, 5), "scale_from": scale_from, "anchor": f"{anchor_desc} at {anchor_lat}, {anchor_lon}", "features": rows, "warnings": warnings}
    return gj, report


def site_plan(image: str, geojson: str, anchor_lon: float, anchor_lat: float, report: dict[str, Any], notes: list[str]) -> SitePlan:
    feats = [
        SiteFeature(name=r["name"], kind=r["kind"], area_m2=r.get("area_m2"), stated_area_m2=r.get("stated_area_m2"), length_m=r.get("length_m"))
        for r in report["features"]
    ]
    return SitePlan(image=image, geojson=geojson, anchor_lon=anchor_lon, anchor_lat=anchor_lat, m_per_px=report["m_per_px"],
                    scale_from=report["scale_from"], features=feats, notes=notes)  # fmt: skip


COLOURS = {"boundary": (0, 160, 0), "exclusion": (220, 0, 0), "transmission_line": (230, 120, 0), "road": (120, 120, 120)}


def preview(image: Path, features: list[TraceFeature], crop: list[float] | None, max_edge: int = 1600) -> bytes:
    """The traced outlines drawn over the image, so the planner can see whether its trace follows the plan."""
    with PILImage.open(image) as im:
        im = im.convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        for f in features:
            col = COLOURS.get(f.kind, (0, 80, 220))
            pts = [tuple(p) for p in f.points]
            if f.kind in LINES:
                d.line(pts, fill=col + (255,), width=3)
            else:
                d.polygon(pts, outline=col + (255,), fill=col + (50,))
            for x, y in pts:
                d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=col + (255,))
            d.text(pts[0], f.name[:40], fill=col + (255,))
        if crop:
            im = im.crop(tuple(int(v) for v in crop))
        k = min(4.0, max_edge / max(im.size))
        im = im.resize((round(im.width * k), round(im.height * k)), PILImage.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
