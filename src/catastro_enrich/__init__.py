"""Cadastral and geographic enrichment of Spanish property inventories.

Public API (see the README for the case study):

- :func:`resolve_cadastre_driver` picks the cadastre for a province (INE
  code or name); drivers share :class:`CadastreDriver`.
- :func:`geocode_address` is the fallback geocoder (Nominatim).
- :func:`utm_to_wgs84` / :func:`wgs84_to_utm` convert by Spanish UTM zone.
- :func:`enrich_property` / :func:`enrich_batch` run the cascade with
  additive, idempotent, dry-run-by-default semantics.
- :func:`fetch_imagery` / :func:`live_links` get storable imagery or live
  links according to each source's licence.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .cadastre import (
    AlavaDriver,
    BizkaiaDriver,
    CadastreDriver,
    DgcDriver,
    DgcTerritory,
    GipuzkoaDriver,
    NavarraDriver,
    resolve_cadastre_driver,
)
from .enrichment import enrich_batch, enrich_imagery_batch, enrich_property, physical_enrichment
from .geo import utm_to_wgs84, wgs84_to_utm
from .geocoding import NominatimGeocoder, geocode_address, normalize_address
from .imagery import (
    ImageStore,
    MapillaryDriver,
    WmsOrthoDriver,
    fetch_imagery,
    ign_pnoa_driver,
    live_links,
    register_ortho_alias,
)
from .models import (
    CadastreResult,
    EnrichmentDiff,
    GeocodeResult,
    ImageDocument,
    ImageryResult,
    PointLookup,
    PropertyRecord,
    RunStats,
)
from .storage import BackupError, SqliteInventory
from .telemetry import MemoryRunLog, RunLog, SqliteRunLog
from .transport import HttpResponse, Pacer, Transport, UrllibTransport

__all__ = [
    "AlavaDriver",
    "BackupError",
    "BizkaiaDriver",
    "CadastreDriver",
    "CadastreResult",
    "DgcDriver",
    "DgcTerritory",
    "EnrichmentDiff",
    "GeocodeResult",
    "GipuzkoaDriver",
    "HttpResponse",
    "ImageDocument",
    "ImageStore",
    "ImageryResult",
    "MapillaryDriver",
    "MemoryRunLog",
    "NavarraDriver",
    "NominatimGeocoder",
    "Pacer",
    "PointLookup",
    "PropertyRecord",
    "RunLog",
    "RunStats",
    "SqliteInventory",
    "SqliteRunLog",
    "Transport",
    "UrllibTransport",
    "WmsOrthoDriver",
    "__version__",
    "enrich_batch",
    "enrich_imagery_batch",
    "enrich_property",
    "fetch_imagery",
    "geocode_address",
    "ign_pnoa_driver",
    "live_links",
    "normalize_address",
    "physical_enrichment",
    "register_ortho_alias",
    "resolve_cadastre_driver",
    "utm_to_wgs84",
    "wgs84_to_utm",
]
