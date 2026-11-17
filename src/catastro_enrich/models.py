"""Data model shared by every module: input records, per-query results,
enrichment diffs, image documents and batch statistics.

Errors are data: every result carries an ``error_code`` string and the batch
never aborts because of a single property.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# --- error codes ------------------------------------------------------------

INVALID_RC = "INVALID_RC"
NOT_FOUND = "NOT_FOUND"
NET = "NET"
PARSE = "PARSE"
NO_COORDS = "NO_COORDS"
NOT_SUPPORTED = "NOT_SUPPORTED"
EMPTY_QUERY = "EMPTY_QUERY"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NO_COVERAGE = "NO_COVERAGE"
NO_TOKEN = "NO_TOKEN"
BAD_RESPONSE = "BAD_RESPONSE"

Precision = Literal["exact", "zone", "administrative", ""]
ImageKind = Literal["ORTHO", "STREET"]
RunKind = Literal["cadastre", "physical", "geocoding", "imagery"]


# --- input record -----------------------------------------------------------


@dataclass
class PropertyRecord:
    """One property of an inventory, as received from the caller's store.

    ``municipality_id`` is optional and only used to group telemetry and image
    folders; when empty the ``locality`` is used instead.
    """

    id: str
    inventory_number: str = ""
    name: str = ""
    cadastral_reference: str | None = None
    street: str | None = None
    locality: str | None = None
    lat: float | None = None
    lon: float | None = None
    built_area_m2: float | None = None
    construction_year: int | None = None
    asset_type: str | None = None
    status: str | None = None
    cadastral_use: str | None = None
    municipality_id: str = ""

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropertyRecord:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# --- query results ----------------------------------------------------------


@dataclass
class CadastreResult:
    """Outcome of a cadastre lookup. ``ok`` is False whenever ``error_code``
    is set; a provider-specific business code (for example an OVC error
    number) is propagated as text."""

    rc: str
    ok: bool = False
    error_code: str = ""
    description: str = ""
    lat: float | None = None
    lon: float | None = None
    area_m2: float | None = None
    year: int | None = None
    use: str = ""
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(
        cls, rc: str, code: str, source: str, description: str = "", **raw: Any
    ) -> CadastreResult:
        return cls(
            rc=rc, ok=False, error_code=code, description=description, source=source, raw=dict(raw)
        )


@dataclass
class PointLookup:
    """Result of reverse lookup: cadastral reference at a WGS84 point."""

    ok: bool
    rc: str = ""
    description: str = ""
    error_code: str = ""


@dataclass
class GeocodeResult:
    query: str
    ok: bool = False
    error_code: str = ""
    lat: float | None = None
    lon: float | None = None
    confidence: float = 0.0
    place_class: str = ""
    place_type: str = ""
    precision: Precision = ""
    display_name: str = ""
    source: str = ""


@dataclass
class ImageryResult:
    ok: bool
    kind: ImageKind
    source: str
    bytes: bytes = b""
    mime: str = ""
    attribution: str = ""
    error_code: str = ""
    captured_at: str | None = None
    image_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageDocument:
    """Metadata of a stored image, as persisted next to the property."""

    property_id: str
    filename: str
    relative_path: str
    mime: str
    sha256: str
    size: int
    source: str
    kind: ImageKind
    attribution: str
    captured_at: str | None = None


# --- enrichment outcomes ----------------------------------------------------


@dataclass
class EnrichmentDiff:
    """What an enrichment step would change on one property.

    ``changes`` maps field name to new value and is empty when nothing would
    be written. ``status`` summarises the outcome for counters.
    """

    property_id: str
    status: str
    changes: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    error_code: str = ""
    reason: str = ""
    precision: Precision = ""

    @property
    def would_write(self) -> bool:
        return bool(self.changes)


@dataclass
class RunStats:
    """Counters of one batch, mirrored into the telemetry row."""

    kind: str = "cadastre"
    driver: str = ""
    municipality_id: str = ""
    dry_run: bool = True
    queried: int = 0
    ok: int = 0
    errors: int = 0
    written: int = 0
    not_found: int = 0
    invalid: int = 0
    net: int = 0
    low_confidence: int = 0
    zone: int = 0
    no_coverage: int = 0
    unbuilt: int = 0
    skipped_existing: int = 0
    duplicates: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    pending: list[tuple[str, str, str]] = field(default_factory=list)
    """(property id, cadastral reference or query, reason) per unresolved item."""

    def extra(self) -> dict[str, Any]:
        return {
            "not_found": self.not_found,
            "invalid": self.invalid,
            "net": self.net,
            "low_confidence": self.low_confidence,
            "zone": self.zone,
            "no_coverage": self.no_coverage,
            "unbuilt": self.unbuilt,
            "skipped_existing": self.skipped_existing,
            "duplicates": self.duplicates,
            "per_source": dict(self.per_source),
        }

    def count_source(self, source: str) -> None:
        if source:
            self.per_source[source] = self.per_source.get(source, 0) + 1
