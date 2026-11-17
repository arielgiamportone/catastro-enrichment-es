"""Geometry extraction from GML features (INSPIRE CP and ArcGIS WFS)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..geo import Point, as_lat_lon, parse_pos_list, ring_centroid, utm_to_wgs84
from ..xmlutil import attr_of, find_first, iter_local, text_of

_UTM_CODE = re.compile(r"258(2[89]|3[01])(?!\d)")


def srs_of(feature: ET.Element) -> str:
    """First ``srsName`` found inside the feature (geometry elements carry it)."""
    for el in feature.iter():
        srs = attr_of(el, "srsName")
        if srs:
            return srs
    return ""


def utm_zone_of(srs: str) -> int | None:
    match = _UTM_CODE.search(srs)
    return int(match.group(0)[-2:]) if match else None


def to_lat_lon(point: Point, srs: str) -> Point:
    """Interpret a raw coordinate pair according to the SRS: UTM zones are
    projected (``easting northing``), everything else is taken as ``lat lon``
    with a range check that swaps the axes when needed."""
    zone = utm_zone_of(srs)
    if zone is not None:
        return utm_to_wgs84(point[0], point[1], zone)
    return as_lat_lon(point)


def exterior_ring(feature: ET.Element) -> list[Point]:
    """Vertices of the first exterior ring, in document order."""
    exterior = find_first(feature, "exterior")
    pos_list = find_first(exterior, "posList") if exterior is not None else None
    if pos_list is None:
        pos_list = find_first(feature, "posList")
    if pos_list is None or not pos_list.text:
        return []
    return parse_pos_list(pos_list.text)


def reference_point(feature: ET.Element) -> Point | None:
    ref = find_first(feature, "referencePoint")
    if ref is None:
        return None
    pos = text_of(ref, "pos")
    pairs = parse_pos_list(pos) if pos else []
    return pairs[0] if pairs else None


def feature_point(feature: ET.Element) -> Point | None:
    """``(lat, lon)`` of a parcel: its reference point when published,
    otherwise the arithmetic centroid of the exterior ring."""
    srs = srs_of(feature)
    point = reference_point(feature)
    if point is None:
        point = ring_centroid(exterior_ring(feature))
    if point is None:
        return None
    return to_lat_lon(point, srs)


def feature_ring_lat_lon(feature: ET.Element) -> list[Point]:
    srs = srs_of(feature)
    return [to_lat_lon(p, srs) for p in exterior_ring(feature)]


def first_member(root: ET.Element) -> ET.Element | None:
    """First feature inside ``wfs:member``/``gml:featureMember`` wrappers."""
    for wrapper in ("member", "featureMember", "featureMembers"):
        for el in iter_local(root, wrapper):
            children = list(el)
            if children:
                return children[0]
    return None
