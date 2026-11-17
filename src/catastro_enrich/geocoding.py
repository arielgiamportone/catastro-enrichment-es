"""Fallback geocoding with Nominatim (OpenStreetMap).

Confidence is decided by the OSM *class* of the hit, not by ``importance``:
streets in small towns get very low importance values without the match
being wrong. A hit on a zone (``place``, ``landuse``...) gives the centroid
of the zone, not of the property, so results carry a ``precision`` label and
batches persist only ``exact`` ones unless told otherwise.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .models import EMPTY_QUERY, LOW_CONFIDENCE, NET, NOT_FOUND, PARSE, GeocodeResult, Precision
from .transport import DEFAULT_TIMEOUT, Pacer, Transport, UrllibTransport

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY = 1.1
"""Seconds between requests: the OSM Foundation policy allows at most 1 req/s."""

ACCEPT_LANGUAGE = "es,eu;q=0.8,en;q=0.5"
MAX_ADDRESS_LENGTH = 200

CONCRETE_CLASSES = frozenset(
    {"highway", "building", "amenity", "shop", "office", "tourism", "leisure"}
)
ZONE_CLASSES = frozenset({"place", "landuse", "natural", "waterway"})
ADMINISTRATIVE_CLASSES = frozenset({"boundary"})

_NOISY_PREFIX = re.compile(
    r"^\s*"
    r"(?:(?:sita|sito|situada|situado|ubicada|ubicado)\s+en\s+)?"
    r"(?:(?:la|el|los|las)\s+)?"
    r"(?:(?:calle|c/|barrio|plaza|avenida|avda\.?|av\.?|paraje|lugar|t[eé]rmino\s+municipal)\s+)?"
    r"(?:(?:de|del|de\s+la|de\s+los|de\s+las)\s+)?",
    re.IGNORECASE,
)


def normalize_address(text: str | None) -> str:
    """First line only; drop one noisy prefix of registry-style notes
    ("sita en la calle de", "barrio de", "avda."...); cap the length."""
    if not text:
        return ""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    first = unicodedata.normalize("NFC", first)
    cleaned = _NOISY_PREFIX.sub("", first, count=1).strip(" ,;")
    if not cleaned:
        cleaned = first
    return cleaned[:MAX_ADDRESS_LENGTH].strip()


def build_query(
    street: str | None, locality: str | None = None, province: str | None = None
) -> str:
    """``<street>, <locality>, <province>, Spain`` skipping empty parts."""
    parts = [p.strip() for p in (street, locality, province) if p and p.strip()]
    if not parts:
        return ""
    return ", ".join([*parts, "Spain"])


def classify_precision(place_class: str, place_type: str = "") -> Precision:
    cls = (place_class or "").lower()
    if cls in CONCRETE_CLASSES:
        return "exact"
    if cls in ADMINISTRATIVE_CLASSES or place_type == "administrative":
        return "administrative"
    if cls in ZONE_CLASSES:
        return "zone"
    return "zone"


class Geocoder(Protocol):
    source: str

    def search(self, query: str) -> tuple[list[dict[str, Any]] | None, str]:
        """Return ``(hits, error_code)``; hits is ``None`` when the call
        failed and the error code says why (``NET`` or ``PARSE``)."""
        ...


class NominatimGeocoder:
    source = "nominatim"
    attribution = "© OpenStreetMap contributors (ODbL)"

    def __init__(
        self,
        *,
        url: str = NOMINATIM_URL,
        transport: Transport | None = None,
        delay: float = NOMINATIM_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
        languages: str = ACCEPT_LANGUAGE,
        limit: int = 5,
        country_codes: str = "es",
        pacer: Pacer | None = None,
    ) -> None:
        self.url = url
        self.transport = transport or UrllibTransport()
        self.delay = delay
        self.timeout = timeout
        self.languages = languages
        self.limit = limit
        self.country_codes = country_codes
        self.pacer = pacer or Pacer(delay)

    def search(self, query: str) -> tuple[list[dict[str, Any]] | None, str]:
        params = {
            "q": query,
            "format": "json",
            "addressdetails": "1",
            "limit": str(self.limit),
            "countrycodes": self.country_codes,
        }
        self.pacer.wait()
        response = self.transport.get(
            self.url, params, headers={"Accept-Language": self.languages}, timeout=self.timeout
        )
        if response is None or not response.ok:
            return None, NET
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return None, PARSE
        if not isinstance(data, list):
            return None, PARSE
        return [hit for hit in data if isinstance(hit, Mapping)], ""


def pick_hit(hits: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, Precision]:
    """First concrete hit wins even if an administrative one ranks higher;
    otherwise the first zone-level hit; otherwise the first administrative
    one. ``(None, "")`` when there are no hits."""
    ranked: dict[str, Mapping[str, Any]] = {}
    for hit in hits:
        precision = classify_precision(str(hit.get("class", "")), str(hit.get("type", "")))
        ranked.setdefault(precision, hit)
    for precision in ("exact", "zone", "administrative"):
        if precision in ranked:
            return ranked[precision], precision  # type: ignore[return-value]
    return None, ""


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def geocode_address(
    text: str | None,
    *,
    geocoder: Geocoder | None = None,
    locality: str | None = None,
    province: str | None = None,
) -> GeocodeResult:
    """Geocode a free-text address. ``ok`` is True for ``exact`` and ``zone``
    precision; administrative-only matches come back as ``LOW_CONFIDENCE``
    with their coordinates included so the caller can decide."""
    query = build_query(normalize_address(text), locality, province)
    if not query:
        return GeocodeResult(query="", error_code=EMPTY_QUERY)
    geocoder = geocoder or NominatimGeocoder()
    hits, code = geocoder.search(query)
    source = getattr(geocoder, "source", "")
    if hits is None:
        return GeocodeResult(query=query, error_code=code or NET, source=source)
    if not hits:
        return GeocodeResult(query=query, error_code=NOT_FOUND, source=source)
    hit, precision = pick_hit(hits)
    if hit is None:
        return GeocodeResult(query=query, error_code=NOT_FOUND, source=source)
    lat, lon = _as_float(hit.get("lat")), _as_float(hit.get("lon"))
    if lat is None or lon is None:
        return GeocodeResult(query=query, error_code=PARSE, source=source)
    result = GeocodeResult(
        query=query,
        ok=precision != "administrative",
        error_code="" if precision != "administrative" else LOW_CONFIDENCE,
        lat=lat,
        lon=lon,
        confidence=_as_float(hit.get("importance")) or 0.0,
        place_class=str(hit.get("class", "")),
        place_type=str(hit.get("type", "")),
        precision=precision,
        display_name=str(hit.get("display_name", "")),
        source=source,
    )
    return result
