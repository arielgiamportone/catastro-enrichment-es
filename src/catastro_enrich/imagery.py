"""Location imagery: storable orthophotos (WMS, PNOA by default) and street
views (Mapillary), plus live links to Google that are never cached.

Storing versus linking follows the licence of each source: open licences
with attribution (IGN, regional orthophotos, Mapillary) are downloaded and
kept with their attribution; terms that forbid caching (Google) only get a
deterministic URL.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .geo import bbox_around, wms_bbox
from .models import (
    BAD_RESPONSE,
    NET,
    NO_COVERAGE,
    NO_TOKEN,
    ImageDocument,
    ImageKind,
    ImageryResult,
    PropertyRecord,
)
from .transport import HttpResponse, Pacer, Transport, UrllibTransport

IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
    (b"GIF8", "image/gif"),
)
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}

PNOA_WMS_URL = "https://www.ign.es/wms-inspire/pnoa-ma"
PNOA_LAYER = "OI.OrthoimageCoverage"
PNOA_ATTRIBUTION = "PNOA © Instituto Geográfico Nacional"

MAPILLARY_URL = "https://graph.mapillary.com/images"
MAPILLARY_FIELDS = "id,thumb_1024_url,compass_angle,captured_at"
MAPILLARY_ATTRIBUTION = "© Mapillary contributors (CC-BY-SA)"

GOOGLE_STREET_VIEW = "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"
GOOGLE_MAPS = "https://www.google.com/maps/search/?api=1&query={lat},{lon}"
GOOGLE_SATELLITE = "https://www.google.com/maps/@{lat},{lon},18z/data=!3m1!1e3"


def sniff_image(response: HttpResponse | None) -> str | None:
    """MIME type if the response is an image (by Content-Type or by the
    first bytes); ``None`` otherwise. WMS failures often arrive as HTTP 200
    with an XML ``ServiceException``."""
    if response is None or not response.ok or not response.content:
        return None
    for signature, mime in IMAGE_SIGNATURES:
        if response.content.startswith(signature):
            return mime
    content_type = response.content_type.split(";")[0].strip()
    if content_type.startswith("image/"):
        return content_type
    return None


class ImageryDriver(Protocol):
    name: str
    kind: ImageKind
    attribution: str

    def fetch(self, lat: float, lon: float) -> ImageryResult: ...


class WmsOrthoDriver:
    """Generic WMS GetMap crop around a point."""

    kind: ImageKind = "ORTHO"

    def __init__(
        self,
        *,
        url: str,
        layer: str,
        name: str,
        attribution: str,
        version: str = "1.3.0",
        span_m: float = 80.0,
        size: int = 512,
        image_format: str = "image/jpeg",
        styles: str = "",
        transport: Transport | None = None,
        delay: float = 0.2,
        timeout: float = 20.0,
        pacer: Pacer | None = None,
    ) -> None:
        self.url = url
        self.layer = layer
        self.name = name
        self.attribution = attribution
        self.version = version
        self.span_m = span_m
        self.size = size
        self.image_format = image_format
        self.styles = styles
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.pacer = pacer or Pacer(delay)

    def params(self, lat: float, lon: float) -> dict[str, str]:
        crs_name, bbox = wms_bbox(self.version, bbox_around(lat, lon, self.span_m))
        return {
            "SERVICE": "WMS",
            "VERSION": self.version,
            "REQUEST": "GetMap",
            "LAYERS": self.layer,
            "STYLES": self.styles,
            "FORMAT": self.image_format,
            "WIDTH": str(self.size),
            "HEIGHT": str(self.size),
            crs_name: "EPSG:4326",
            "BBOX": bbox,
        }

    def fetch(self, lat: float, lon: float) -> ImageryResult:
        self.pacer.wait()
        response = self.transport.get(self.url, self.params(lat, lon), timeout=self.timeout)
        mime = sniff_image(response)
        if mime is None:
            status = response.status if response is not None else None
            return ImageryResult(
                ok=False, kind=self.kind, source=self.name, error_code=NET, raw={"status": status}
            )
        assert response is not None
        return ImageryResult(
            ok=True,
            kind=self.kind,
            source=self.name,
            bytes=response.content,
            mime=mime,
            attribution=self.attribution,
            raw={"url": self.url, "layer": self.layer, "span_m": self.span_m},
        )


def ign_pnoa_driver(**kwargs: Any) -> WmsOrthoDriver:
    """PNOA Máxima Actualidad (IGN), CC-BY 4.0."""
    defaults: dict[str, Any] = {
        "url": PNOA_WMS_URL,
        "layer": PNOA_LAYER,
        "name": "ign_pnoa",
        "attribution": PNOA_ATTRIBUTION,
        "version": "1.3.0",
    }
    defaults.update(kwargs)
    return WmsOrthoDriver(**defaults)


OrthoFactory = Callable[..., WmsOrthoDriver]
ORTHO_ALIASES: dict[str, OrthoFactory] = {"ign": ign_pnoa_driver}


def register_ortho_alias(alias: str, factory: OrthoFactory) -> None:
    """Register a regional orthophoto WMS under a CLI alias."""
    ORTHO_ALIASES[alias.lower()] = factory


def ortho_driver(alias: str, **kwargs: Any) -> WmsOrthoDriver | None:
    """``auto``/``ign`` give PNOA, ``none`` gives ``None``, any registered
    alias gives its driver. Unknown aliases raise ``KeyError``."""
    key = alias.lower().strip()
    if key in ("none", ""):
        return None
    if key == "auto":
        key = "ign"
    return ORTHO_ALIASES[key](**kwargs)


class MapillaryDriver:
    kind: ImageKind = "STREET"
    name = "mapillary"
    attribution = MAPILLARY_ATTRIBUTION

    def __init__(
        self,
        token: str | None,
        *,
        span_m: float = 30.0,
        url: str = MAPILLARY_URL,
        transport: Transport | None = None,
        delay: float = 0.2,
        timeout: float = 20.0,
        pacer: Pacer | None = None,
    ) -> None:
        self.token = (token or "").strip()
        self.span_m = span_m
        self.url = url
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.pacer = pacer or Pacer(delay)

    def fetch(self, lat: float, lon: float) -> ImageryResult:
        if not self.token:
            return ImageryResult(ok=False, kind=self.kind, source=self.name, error_code=NO_TOKEN)
        min_lon, min_lat, max_lon, max_lat = bbox_around(lat, lon, self.span_m)
        params = {
            "access_token": self.token,
            "fields": MAPILLARY_FIELDS,
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "limit": "1",
        }
        self.pacer.wait()
        response = self.transport.get(self.url, params, timeout=self.timeout)
        if response is None or not response.ok:
            return ImageryResult(ok=False, kind=self.kind, source=self.name, error_code=NET)
        try:
            payload = response.json()
            images = payload.get("data", []) if isinstance(payload, dict) else None
        except (ValueError, json.JSONDecodeError, AttributeError):
            images = None
        if images is None:
            return ImageryResult(
                ok=False, kind=self.kind, source=self.name, error_code=BAD_RESPONSE
            )
        if not images:
            return ImageryResult(ok=False, kind=self.kind, source=self.name, error_code=NO_COVERAGE)
        image = images[0]
        thumb_url = str(image.get("thumb_1024_url", ""))
        if not thumb_url:
            return ImageryResult(
                ok=False, kind=self.kind, source=self.name, error_code=BAD_RESPONSE
            )
        self.pacer.wait()
        thumb = self.transport.get(thumb_url, timeout=self.timeout)
        mime = sniff_image(thumb)
        if thumb is None or not thumb.ok:
            return ImageryResult(ok=False, kind=self.kind, source=self.name, error_code=NET)
        if mime is None:
            return ImageryResult(
                ok=False, kind=self.kind, source=self.name, error_code=BAD_RESPONSE
            )
        image_id = str(image.get("id", ""))
        return ImageryResult(
            ok=True,
            kind=self.kind,
            source=self.name,
            bytes=thumb.content,
            mime=mime,
            attribution=f"{self.attribution}, image {image_id}" if image_id else self.attribution,
            captured_at=_mapillary_timestamp(image.get("captured_at")),
            image_id=image_id,
            raw={"compass_angle": image.get("compass_angle")},
        )


def _mapillary_timestamp(value: Any) -> str | None:
    """Mapillary ``captured_at`` is epoch milliseconds."""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=UTC).isoformat()


def fetch_imagery(
    lat: float, lon: float, *, drivers: Iterable[ImageryDriver]
) -> list[ImageryResult]:
    """Query every driver for the point; one result per driver, errors as data."""
    return [driver.fetch(lat, lon) for driver in drivers]


def live_links(lat: float, lon: float) -> dict[str, str]:
    """Deterministic Google links (6 decimals). Never downloaded or stored."""
    la, lo = f"{lat:.6f}", f"{lon:.6f}"
    return {
        "street_view": GOOGLE_STREET_VIEW.format(lat=la, lon=lo),
        "maps": GOOGLE_MAPS.format(lat=la, lon=lo),
        "satellite": GOOGLE_SATELLITE.format(lat=la, lon=lo),
    }


# --- storage with de-duplication ---------------------------------------------

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, default: str = "unknown") -> str:
    value = _SLUG.sub("-", (text or "").lower()).strip("-")
    return value or default


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class ImageStore:
    """Writes images under ``<root>/enrichment/<municipality>/<inventory
    number>/<source>_<kind>.<ext>`` and refuses duplicates.

    Two guards: one image per (property, source, kind) unless ``overwrite``,
    and a SHA-256 of the bytes that skips identical crops (identical
    coordinates produce identical images).
    """

    root: Path
    existing: Sequence[ImageDocument] = ()
    base_dir: str = "enrichment"
    hashes: set[str] = field(init=False)
    keys: set[tuple[str, str, str]] = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.hashes = {doc.sha256 for doc in self.existing}
        self.keys = {(doc.property_id, doc.source, doc.kind) for doc in self.existing}

    def has(self, property_id: str, source: str, kind: str) -> bool:
        return (property_id, source, kind) in self.keys

    def relative_path(self, record: PropertyRecord, source: str, kind: str, mime: str) -> str:
        ext = EXTENSIONS.get(mime, "bin")
        municipality = slug(record.municipality_id or record.locality or "")
        number = slug(record.inventory_number or record.id, default="item")
        return f"{self.base_dir}/{municipality}/{number}/{source}_{kind.lower()}.{ext}"

    def store(
        self,
        record: PropertyRecord,
        result: ImageryResult,
        *,
        write: bool,
        overwrite: bool = False,
    ) -> tuple[str, ImageDocument | None]:
        """Return ``(status, document)`` with status one of ``written``,
        ``dry_run``, ``skipped_existing`` or ``duplicate``."""
        key = (record.id, result.source, result.kind)
        if key in self.keys and not overwrite:
            return "skipped_existing", None
        digest = sha256_hex(result.bytes)
        if digest in self.hashes and not overwrite:
            return "duplicate", None
        rel = self.relative_path(record, result.source, result.kind, result.mime)
        doc = ImageDocument(
            property_id=record.id,
            filename=rel.rsplit("/", 1)[-1],
            relative_path=rel,
            mime=result.mime,
            sha256=digest,
            size=len(result.bytes),
            source=result.source,
            kind=result.kind,
            attribution=result.attribution,
            captured_at=result.captured_at,
        )
        if not write:
            return "dry_run", doc
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.bytes)
        self.hashes.add(digest)
        self.keys.add(key)
        return "written", doc
