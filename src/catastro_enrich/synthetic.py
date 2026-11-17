"""Synthetic data for tests, examples and demos.

Nothing here comes from a real inventory or a real service response. The
generators produce records, cadastral references with deliberately unreal
patterns, and service responses (OVC XML, INSPIRE GML, ArcGIS WFS, Nominatim
JSON, Mapillary JSON, image bytes) shaped like the public services document
them.
"""

from __future__ import annotations

import random
import re
import struct
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .models import PropertyRecord
from .transport import DEFAULT_TIMEOUT, HttpResponse, build_url

# --- synthetic identifiers --------------------------------------------------

SYNTHETIC_SHEET = "ZZ"
"""Two-letter sheet code that no real cadastral reference uses."""

_STREET_NAMES = [
    "Mayor",
    "de la Fuente",
    "del Molino",
    "Real",
    "de la Iglesia",
    "Nueva",
    "del Sol",
    "de las Eras",
    "de la Estacion",
    "del Prado",
]
_STREET_TYPES = ["Calle", "Plaza", "Avenida", "Camino", "Travesia"]
_ASSET_TYPES = ["building", "plot", "warehouse", "school", "sports", "office"]
_USES = ["Residencial", "Industrial", "Oficinas", "Deportivo", "Almacen", "Suelo"]
_LOCALITIES = ["Villanueva Sintetica", "Aldea Modelo", "Puerto Ficticio", "Valle Ejemplo"]


def synthetic_rc(index: int, *, length: int = 14) -> str:
    """Synthetic DGC-style reference: 7 digits + ``ZZ`` + 4 digits + ``S``.

    The ``ZZ`` sheet code and the trailing control letter make the pattern
    recognisably artificial while keeping the 14-character shape. ``length``
    20 appends a synthetic unit suffix.
    """
    base = f"{index % 10_000_000:07d}{SYNTHETIC_SHEET}{(index * 7) % 10_000:04d}S"
    if length == 20:
        return base + f"{index % 10_000:04d}ZZ"
    return base


def synthetic_gipuzkoa_rc(index: int) -> str:
    return f"{index % 90 + 1:03d}-{index * 13 % 9000 + 1000}-{index % 50 + 1:04d}"


def synthetic_navarra_rc(index: int) -> str:
    return f"{index % 900 + 1:03d}{index % 20 + 1:03d}{index % 999 + 1:03d}"


def synthetic_bizkaia_rc(index: int) -> str:
    return f"{index % 110 + 1}-{index % 9000 + 1000}-{index % 90000 + 10000}"


def synthetic_alava_rc(index: int) -> str:
    return f"{index % 50 + 1:02d}-{index % 30 + 1:03d}-{index % 999 + 1:05d}"


# --- records ----------------------------------------------------------------


def synthetic_records(
    count: int,
    *,
    seed: int = 7,
    municipality_id: str = "m-synthetic",
    locality: str | None = None,
    with_coordinates: float = 0.3,
    with_rc: float = 0.8,
    with_street: float = 0.9,
    with_physical: float = 0.2,
) -> list[PropertyRecord]:
    """Deterministic list of inventory records with controllable gaps.

    The ratios are probabilities that a record already has coordinates, a
    cadastral reference, a street and physical data respectively.
    """
    rng = random.Random(seed)
    records: list[PropertyRecord] = []
    for i in range(1, count + 1):
        loc = locality or rng.choice(_LOCALITIES)
        has_coords = rng.random() < with_coordinates
        has_rc = rng.random() < with_rc
        has_street = rng.random() < with_street
        has_phys = rng.random() < with_physical
        lat = round(rng.uniform(36.0, 43.5), 6) if has_coords else None
        lon = round(rng.uniform(-9.0, 3.0), 6) if has_coords else None
        street = (
            f"{rng.choice(_STREET_TYPES)} {rng.choice(_STREET_NAMES)}, {rng.randint(1, 120)}"
            if has_street
            else None
        )
        records.append(
            PropertyRecord(
                id=f"p-{i:04d}",
                inventory_number=f"{i:05d}",
                name=f"Synthetic asset {i}",
                cadastral_reference=synthetic_rc(i) if has_rc else None,
                street=street,
                locality=loc,
                lat=lat,
                lon=lon,
                built_area_m2=float(rng.randint(40, 4000)) if has_phys else None,
                construction_year=rng.randint(1900, 2020) if has_phys else None,
                asset_type=rng.choice(_ASSET_TYPES),
                status="active",
                municipality_id=municipality_id,
            )
        )
    return records


# --- synthetic service responses -------------------------------------------

OVC_NS = "http://www.catastro.meh.es/"
CP_NS = "http://inspire.ec.europa.eu/schemas/cp/4.0"
GML_NS = "http://www.opengis.net/gml/3.2"
WFS_NS = "http://www.opengis.net/wfs/2.0"


def dgc_coordinates_xml(*, ldt: str, lon: float, lat: float, rc: str = "") -> bytes:
    """Consulta_CPMRC-style response with a centroid."""
    pc1, pc2 = (rc[:7], rc[7:14]) if len(rc) >= 14 else ("", "")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_coordenadas xmlns="{OVC_NS}">
  <control><cucoor>1</cucoor><cuerr>0</cuerr></control>
  <coordenadas><coord>
    <pc><pc1>{escape(pc1)}</pc1><pc2>{escape(pc2)}</pc2></pc>
    <geo><xcen>{lon}</xcen><ycen>{lat}</ycen><srs>EPSG:4326</srs></geo>
    <ldt>{escape(ldt)}</ldt>
  </coord></coordenadas>
</consulta_coordenadas>""".encode()


def dgc_error_xml(code: int | str, message: str = "synthetic error") -> bytes:
    """OVC-style business error delivered with HTTP 200."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_coordenadas xmlns="{OVC_NS}">
  <control><cucoor>0</cucoor><cuerr>1</cuerr></control>
  <lerr><err><cod>{code}</cod><des>{escape(message)}</des></err></lerr>
</consulta_coordenadas>""".encode()


def dgc_point_xml(*, pc1: str, pc2: str, ldt: str) -> bytes:
    """Consulta_RCCOOR-style response."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_coordenadas xmlns="{OVC_NS}">
  <control><cucoor>1</cucoor><cuerr>0</cuerr></control>
  <coordenadas><coord>
    <pc><pc1>{escape(pc1)}</pc1><pc2>{escape(pc2)}</pc2></pc>
    <ldt>{escape(ldt)}</ldt>
  </coord></coordenadas>
</consulta_coordenadas>""".encode()


def dgc_physical_xml(
    units: Sequence[Mapping[str, object]],
    *,
    constructions: Sequence[Mapping[str, object]] = (),
    ldt: str = "",
) -> bytes:
    """Consulta_DNPRC-style response.

    ``units`` are property entries with ``sfc`` (m²), ``ant`` (year) and
    ``luso`` (use); ``constructions`` are ``cons`` entries with ``lcd``,
    ``pt`` and ``stl``.
    """
    ldt_xml = f"<ldt>{escape(ldt)}</ldt>" if ldt else ""
    unit_xml = "".join(
        f"<bi>{ldt_xml}<debi>"
        f"<luso>{escape(str(u.get('luso', '')))}</luso>"
        f"<sfc>{u.get('sfc', '')}</sfc>"
        f"<ant>{u.get('ant', '')}</ant>"
        "</debi></bi>"
        for u in units
    )
    cons_xml = "".join(
        "<cons>"
        f"<lcd>{escape(str(c.get('lcd', '')))}</lcd>"
        f"<dt><lourb><loint><pt>{escape(str(c.get('pt', '')))}</pt></loint></lourb></dt>"
        f"<dfcons><stl>{c.get('stl', '')}</stl></dfcons>"
        "</cons>"
        for c in constructions
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_dnp xmlns="{OVC_NS}">
  <control><cudnp>{len(units)}</cudnp><cucons>{len(constructions)}</cucons><cuerr>0</cuerr></control>
  <bico>{unit_xml}<lcons>{cons_xml}</lcons></bico>
</consulta_dnp>""".encode()


def dgc_unit_list_xml(unit_rcs: Iterable[str]) -> bytes:
    """Consulta_DNPRC-style answer for a parcel with several units: the list
    of 20-character references without physical data."""
    items = "".join(
        "<rcdnp><rc>"
        f"<pc1>{escape(rc[:7])}</pc1><pc2>{escape(rc[7:14])}</pc2>"
        f"<car>{escape(rc[14:18])}</car><cc1>{escape(rc[18:19])}</cc1><cc2>{escape(rc[19:20])}</cc2>"
        "</rc></rcdnp>"
        for rc in unit_rcs
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_dnp xmlns="{OVC_NS}">
  <control><cudnp>{items.count("<rcdnp>")}</cudnp><cuerr>0</cuerr></control>
  <lrcdnp>{items}</lrcdnp>
</consulta_dnp>""".encode()


def dgc_address_xml(rcs: Iterable[str]) -> bytes:
    """ConsultaNumero-style response listing parcel references."""
    items = "".join(
        f"<nump><pc><pc1>{escape(rc[:7])}</pc1><pc2>{escape(rc[7:14])}</pc2></pc></nump>"
        for rc in rcs
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<consulta_numerero xmlns="{OVC_NS}">
  <control><cuerr>0</cuerr></control>
  <numerero>{items}</numerero>
</consulta_numerero>""".encode()


def ring_pos_list(ring: Sequence[tuple[float, float]], *, close: bool = True) -> str:
    pts = list(ring)
    if close and pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return " ".join(f"{a} {b}" for a, b in pts)


def inspire_parcel_gml(
    *,
    reference: str,
    ring: Sequence[tuple[float, float]] | None = None,
    reference_point: tuple[float, float] | None = None,
    area: float | None = 1234.5,
    local_id: str = "",
    number_matched: str = "1",
    number_returned: str = "1",
    srs: str = "http://www.opengis.net/def/crs/EPSG/0/4258",
    patches: bool = False,
    prefix: str = "cp",
) -> bytes:
    """INSPIRE ``CadastralParcel`` feature collection (Gipuzkoa, Navarra and
    the DGC INSPIRE service share this shape). Coordinates are ``lat lon`` in
    EPSG:4258 unless another ``srs`` is given. ``patches`` wraps the ring in a
    ``Surface/patches/PolygonPatch`` as Navarra does."""
    if number_returned == "0":
        members = ""
    else:
        pos_list = ring_pos_list(
            ring or [(40.0, -3.0), (40.0, -2.999), (40.001, -2.999), (40.001, -3.0)]
        )
        if patches:
            geom = (
                f'<gml:MultiSurface srsName="{srs}"><gml:surfaceMember><gml:Surface><gml:patches>'
                f"<gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList>{pos_list}"
                "</gml:posList></gml:LinearRing></gml:exterior></gml:PolygonPatch>"
                "</gml:patches></gml:Surface></gml:surfaceMember></gml:MultiSurface>"
            )
        else:
            geom = (
                f'<gml:Polygon srsName="{srs}"><gml:exterior><gml:LinearRing><gml:posList>'
                f"{pos_list}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>"
            )
        area_xml = (
            f'<{prefix}:areaValue uom="m2">{area}</{prefix}:areaValue>' if area is not None else ""
        )
        point_xml = (
            f'<{prefix}:referencePoint><gml:Point srsName="{srs}"><gml:pos>'
            f"{reference_point[0]} {reference_point[1]}</gml:pos></gml:Point>"
            f"</{prefix}:referencePoint>"
            if reference_point
            else ""
        )
        members = (
            f'<wfs:member><{prefix}:CadastralParcel gml:id="CP.{escape(reference)}">'
            f"{area_xml}<{prefix}:geometry>{geom}</{prefix}:geometry>"
            f"<{prefix}:inspireId><base:Identifier><base:localId>{escape(local_id or reference)}"
            "</base:localId><base:namespace>ES.SYNTH.CP</base:namespace></base:Identifier>"
            f"</{prefix}:inspireId><{prefix}:label>{escape(reference)}</{prefix}:label>"
            f"<{prefix}:nationalCadastralReference>{escape(reference)}</{prefix}:nationalCadastralReference>"
            f"{point_xml}</{prefix}:CadastralParcel></wfs:member>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection xmlns:wfs="{WFS_NS}" '
        f'xmlns:gml="{GML_NS}" xmlns:{prefix}="{CP_NS}" '
        'xmlns:base="http://inspire.ec.europa.eu/schemas/base/3.3" '
        f'numberMatched="{number_matched}" numberReturned="{number_returned}">{members}'
        "</wfs:FeatureCollection>"
    ).encode()


def arcgis_parcel_xml(
    *,
    layer: str,
    fields: Mapping[str, object],
    ring_utm: Sequence[tuple[float, float]] | None,
    number_matched: str = "1",
    namespace: str = "https://example.invalid/wfs",
) -> bytes:
    """ArcGIS Server WFS 2.0 feature collection with EPSG:25830 geometry
    (``easting northing``), as the Basque foral services publish. The
    namespace is deliberately different from the published one to exercise
    local-name parsing."""
    if ring_utm is None:
        members = ""
    else:
        attrs = "".join(f"<ns:{k}>{escape(str(v))}</ns:{k}>" for k, v in fields.items())
        members = (
            f'<wfs:member><ns:{layer} gml:id="{layer}.1">{attrs}<ns:SHAPE>'
            '<gml:MultiSurface srsName="urn:ogc:def:crs:EPSG::25830"><gml:surfaceMember>'
            f"<gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>{ring_pos_list(ring_utm)}"
            "</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>"
            f"</gml:surfaceMember></gml:MultiSurface></ns:SHAPE></ns:{layer}></wfs:member>"
        )
    returned = "0" if ring_utm is None else "1"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection xmlns:wfs="{WFS_NS}" '
        f'xmlns:gml="{GML_NS}" xmlns:ns="{namespace}" numberMatched="{number_matched}" '
        f'numberReturned="{returned}">{members}</wfs:FeatureCollection>'
    ).encode()


def nominatim_hit(
    *,
    lat: float,
    lon: float,
    place_class: str,
    place_type: str,
    importance: float = 0.3,
    display_name: str = "Synthetic place, Spain",
    osm_id: int = 1,
) -> dict[str, object]:
    return {
        "lat": str(lat),
        "lon": str(lon),
        "class": place_class,
        "type": place_type,
        "importance": importance,
        "display_name": display_name,
        "osm_id": osm_id,
        "address": {"country_code": "es"},
    }


def mapillary_json(images: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return {"data": [dict(img) for img in images]}


def tiny_jpeg(seed: int = 0) -> bytes:
    """Minimal bytes with a JPEG signature; distinct per ``seed``."""
    return b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + seed.to_bytes(4, "big") + b"\xff\xd9"


def tiny_png(seed: int = 0) -> bytes:
    """A valid 1x1 PNG; the pixel value derives from ``seed``."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = b"\x00" + bytes([seed % 256, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# --- scripted transport -----------------------------------------------------

Responder = Callable[[str, Mapping[str, str]], "HttpResponse | None"]


@dataclass
class RecordedRequest:
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout: float

    @property
    def full_url(self) -> str:
        return build_url(self.url, self.params)


@dataclass
class ScriptedTransport:
    """Offline transport for tests and examples.

    Rules are tried in order: the first whose ``match`` substring appears in
    the full URL answers. A rule's response may be an ``HttpResponse``, raw
    bytes (served as HTTP 200 with the given content type), ``None`` (network
    failure) or a callable receiving ``(url, params)``.
    """

    rules: list[tuple[str, object]] = field(default_factory=list)
    content_type: str = "text/xml; charset=utf-8"
    requests: list[RecordedRequest] = field(default_factory=list)

    def add(self, match: str, response: object) -> ScriptedTransport:
        self.rules.append((match, response))
        return self

    def get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse | None:
        req = RecordedRequest(url, dict(params or {}), dict(headers or {}), timeout)
        self.requests.append(req)
        full = req.full_url
        for match, response in self.rules:
            if match in full:
                return self._materialise(response, url, req.params)
        return HttpResponse(status=404, content=b"no scripted response", url=full)

    def _materialise(
        self, response: object, url: str, params: Mapping[str, str]
    ) -> HttpResponse | None:
        if callable(response):
            response = response(url, params)
        if response is None:
            return None
        if isinstance(response, HttpResponse):
            return response
        if isinstance(response, bytes):
            return HttpResponse(
                status=200, content=response, headers={"content-type": self.content_type}
            )
        if isinstance(response, str):
            return HttpResponse(
                status=200, content=response.encode(), headers={"content-type": self.content_type}
            )
        raise TypeError(f"unsupported scripted response: {type(response)!r}")

    @property
    def calls(self) -> int:
        return len(self.requests)

    def urls(self) -> list[str]:
        return [r.full_url for r in self.requests]


def is_synthetic_rc(rc: str) -> bool:
    """True for references produced by :func:`synthetic_rc`."""
    return bool(re.fullmatch(rf"\d{{7}}{SYNTHETIC_SHEET}\d{{4}}S(\d{{4}}ZZ)?", rc))
