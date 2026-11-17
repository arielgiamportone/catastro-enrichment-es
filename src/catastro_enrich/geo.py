"""Coordinate helpers: UTM zones for Spain, WGS84 conversions, ring
centroids and bounding boxes for WMS/Mapillary requests."""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache

from pyproj import Transformer

SPANISH_UTM_ZONES = (28, 29, 30, 31)
WGS84 = "EPSG:4326"
METERS_PER_DEGREE = 111_320.0

SPAIN_LON_RANGE = (-20.0, 5.0)
SPAIN_LAT_RANGE = (25.0, 45.0)

Point = tuple[float, float]


def utm_epsg(zone: int) -> str:
    """ETRS89 / UTM zone *N* code (EPSG:25828–25831 for Spain)."""
    if zone not in SPANISH_UTM_ZONES:
        raise ValueError(f"UTM zone {zone} is outside the Spanish zones {SPANISH_UTM_ZONES}")
    return f"EPSG:258{zone}"


def zone_from_longitude(lon: float) -> int:
    return math.floor((lon + 180.0) / 6.0) + 1


@lru_cache(maxsize=8)
def _to_wgs84(zone: int) -> Transformer:
    return Transformer.from_crs(utm_epsg(zone), WGS84, always_xy=True)


@lru_cache(maxsize=8)
def _from_wgs84(zone: int) -> Transformer:
    return Transformer.from_crs(WGS84, utm_epsg(zone), always_xy=True)


def utm_to_wgs84(easting: float, northing: float, zone: int = 30) -> tuple[float, float]:
    """Return ``(lat, lon)`` for an ETRS89/UTM point in the given zone."""
    lon, lat = _to_wgs84(zone).transform(easting, northing)
    return float(lat), float(lon)


def wgs84_to_utm(lat: float, lon: float) -> tuple[float, float, int]:
    """Return ``(easting, northing, zone)``; the zone is derived from ``lon``."""
    zone = zone_from_longitude(lon)
    easting, northing = _from_wgs84(zone).transform(lon, lat)
    return float(easting), float(northing), zone


def looks_like_spain(lon: float, lat: float) -> bool:
    return (
        SPAIN_LON_RANGE[0] <= lon <= SPAIN_LON_RANGE[1]
        and SPAIN_LAT_RANGE[0] <= lat <= SPAIN_LAT_RANGE[1]
    )


def parse_pos_list(text: str, *, dimension: int = 2) -> list[Point]:
    """Split a GML ``posList`` into pairs in document order (no axis swap)."""
    values = [float(v) for v in text.replace(",", " ").split()]
    return [(values[i], values[i + 1]) for i in range(0, len(values) - dimension + 1, dimension)]


def ring_centroid(points: Sequence[Point]) -> Point | None:
    """Arithmetic mean of the vertices, ignoring a closing vertex equal to the
    first one. Enough to place a parcel; it is not an area-weighted centroid."""
    pts = list(points)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def as_lat_lon(point: Point) -> Point:
    """Interpret a pair as ``(lat, lon)``; swap if only the reversed order
    falls inside the Spanish range."""
    a, b = point
    if looks_like_spain(lon=b, lat=a):
        return a, b
    if looks_like_spain(lon=a, lat=b):
        return b, a
    return a, b


def bbox_around(lat: float, lon: float, span_m: float) -> tuple[float, float, float, float]:
    """``(min_lon, min_lat, max_lon, max_lat)`` of a square of side ``span_m``
    centred on the point."""
    half = span_m / 2.0
    dlat = half / METERS_PER_DEGREE
    dlon = half / (METERS_PER_DEGREE * max(0.2, math.cos(math.radians(lat))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def wms_bbox(version: str, bbox: tuple[float, float, float, float]) -> tuple[str, str]:
    """Return ``(crs parameter name, BBOX value)`` for EPSG:4326.

    WMS 1.1.1 expects ``SRS`` and ``minlon,minlat,maxlon,maxlat``; WMS 1.3.0
    expects ``CRS`` and the axis order of the CRS, which for EPSG:4326 is
    ``minlat,minlon,maxlat,maxlon``.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    if version.startswith("1.3"):
        return "CRS", f"{min_lat},{min_lon},{max_lat},{max_lon}"
    return "SRS", f"{min_lon},{min_lat},{max_lon},{max_lat}"


def polygon_wkt(ring_lat_lon: Sequence[Point]) -> str:
    """WKT polygon in ``lon lat`` order from a ``(lat, lon)`` ring."""
    pts = list(ring_lat_lon)
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return "POLYGON((" + ", ".join(f"{lon} {lat}" for lat, lon in pts) + "))"
