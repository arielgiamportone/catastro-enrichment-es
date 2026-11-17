"""Enrichment cascade and idempotent batches.

Cascade for coordinates: cadastral reference -> cadastre of the territory;
if the reference is unknown or invalid and there is an address -> fallback
geocoding; otherwise the property stays pending with a reason. Geocoding
never overwrites a cadastral coordinate.

Batches are dry runs by default, additive (only empty fields are filled,
each one separately) and therefore idempotent: a second pass writes nothing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .cadastre import DGC_NOT_FOUND_CODES, CadastreDriver
from .geocoding import Geocoder, geocode_address
from .imagery import ImageryDriver, ImageStore
from .models import (
    INVALID_RC,
    LOW_CONFIDENCE,
    NET,
    NO_COVERAGE,
    NO_TOKEN,
    NOT_FOUND,
    NOT_SUPPORTED,
    EnrichmentDiff,
    ImageDocument,
    PropertyRecord,
    RunStats,
)
from .telemetry import RunLog, guarded_close, guarded_open

ProgressCallback = Callable[[int, int, EnrichmentDiff], None]
ApplyCallback = Callable[[PropertyRecord, EnrichmentDiff], Any]
ImageCallback = Callable[[PropertyRecord, ImageDocument], Any]

CADASTRE_MISS_CODES = frozenset({NOT_FOUND, INVALID_RC, *DGC_NOT_FOUND_CODES})
"""Codes after which the cascade may fall back to geocoding."""

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped_existing"
STATUS_PENDING = "pending"
STATUS_NOT_FOUND = "not_found"
STATUS_INVALID = "invalid"
STATUS_NET = "net"
STATUS_LOW_CONFIDENCE = "low_confidence"
STATUS_ZONE = "zone"
STATUS_UNBUILT = "unbuilt"
STATUS_ERROR = "error"


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == 0


# --- single-property steps ----------------------------------------------------


def enrich_property(
    record: PropertyRecord,
    *,
    cadastre: CadastreDriver | None,
    geocoder: Geocoder | None = None,
    overwrite: bool = False,
    only_coords: bool = False,
    accept_zone: bool = False,
    province: str | None = None,
) -> EnrichmentDiff:
    """Coordinates (and official street when empty) for one property."""
    if record.has_coordinates and not overwrite:
        return EnrichmentDiff(record.id, STATUS_SKIPPED, reason="coordinates already present")
    rc = (record.cadastral_reference or "").strip()
    if rc and cadastre is not None:
        result = cadastre.lookup_rc(rc)
        if result.ok and result.lat is not None and result.lon is not None:
            changes: dict[str, Any] = {"lat": result.lat, "lon": result.lon}
            if not only_coords and result.description and (_empty(record.street) or overwrite):
                changes["street"] = result.description
            return EnrichmentDiff(record.id, STATUS_OK, changes, source=result.source)
        if (
            result.error_code not in CADASTRE_MISS_CODES
            or geocoder is None
            or _empty(record.street)
        ):
            status = _status_for_code(result.error_code)
            return EnrichmentDiff(
                record.id,
                status,
                source=result.source,
                error_code=result.error_code,
                reason=result.description or result.error_code,
            )
        fallback = _geocode_step(record, geocoder, accept_zone=accept_zone, province=province)
        fallback.reason = f"cadastre {result.error_code}; " + (fallback.reason or "geocoded")
        return fallback
    if geocoder is not None and not _empty(record.street):
        return _geocode_step(record, geocoder, accept_zone=accept_zone, province=province)
    reason = "no cadastral reference and no address" if not rc else "no cadastre driver"
    return EnrichmentDiff(record.id, STATUS_PENDING, reason=reason)


def _geocode_step(
    record: PropertyRecord,
    geocoder: Geocoder,
    *,
    accept_zone: bool,
    province: str | None,
) -> EnrichmentDiff:
    result = geocode_address(
        record.street, geocoder=geocoder, locality=record.locality, province=province
    )
    if result.ok and result.lat is not None and result.lon is not None:
        if result.precision == "exact" or (result.precision == "zone" and accept_zone):
            return EnrichmentDiff(
                record.id,
                STATUS_OK,
                {"lat": result.lat, "lon": result.lon},
                source=result.source,
                precision=result.precision,
            )
        return EnrichmentDiff(
            record.id,
            STATUS_ZONE,
            source=result.source,
            precision=result.precision,
            reason=f"zone-level match ({result.place_class}/{result.place_type}); not persisted",
        )
    if result.error_code == LOW_CONFIDENCE:
        return EnrichmentDiff(
            record.id,
            STATUS_LOW_CONFIDENCE,
            source=result.source,
            error_code=LOW_CONFIDENCE,
            precision="administrative",
            reason="only administrative matches",
        )
    return EnrichmentDiff(
        record.id,
        _status_for_code(result.error_code),
        source=result.source,
        error_code=result.error_code,
        reason=result.error_code,
    )


def physical_enrichment(
    record: PropertyRecord, *, cadastre: CadastreDriver, overwrite: bool = False
) -> EnrichmentDiff:
    """Built area, construction year and use, each only where empty or 0."""
    rc = (record.cadastral_reference or "").strip()
    if not rc:
        return EnrichmentDiff(record.id, STATUS_PENDING, reason="no cadastral reference")
    targets = {
        "built_area_m2": record.built_area_m2,
        "construction_year": record.construction_year,
        "cadastral_use": record.cadastral_use,
    }
    if not overwrite and not any(_empty(v) for v in targets.values()):
        return EnrichmentDiff(record.id, STATUS_SKIPPED, reason="physical data already present")
    result = cadastre.physical_data(rc)
    if not result.ok:
        if result.error_code == NOT_FOUND and result.raw.get("unbuilt"):
            return EnrichmentDiff(
                record.id, STATUS_UNBUILT, source=result.source, reason="unbuilt parcel"
            )
        return EnrichmentDiff(
            record.id,
            _status_for_code(result.error_code),
            source=result.source,
            error_code=result.error_code,
            reason=result.description or result.error_code,
        )
    values = {
        "built_area_m2": result.area_m2,
        "construction_year": result.year,
        "cadastral_use": result.use,
    }
    changes = {
        key: value
        for key, value in values.items()
        if not _empty(value) and (overwrite or _empty(targets[key]))
    }
    return EnrichmentDiff(record.id, STATUS_OK, changes, source=result.source)


def _status_for_code(code: str) -> str:
    if code in CADASTRE_MISS_CODES:
        return STATUS_INVALID if code == INVALID_RC else STATUS_NOT_FOUND
    if code == NET:
        return STATUS_NET
    if code == NOT_SUPPORTED:
        return STATUS_ERROR
    return STATUS_ERROR


# --- batches --------------------------------------------------------------------


def _count(stats: RunStats, diff: EnrichmentDiff) -> None:
    if diff.status == STATUS_SKIPPED:
        stats.skipped_existing += 1
        return
    if diff.status == STATUS_PENDING:
        return  # nothing could be asked: no reference, no address
    stats.queried += 1
    stats.count_source(diff.source)
    if diff.status == STATUS_OK:
        stats.ok += 1
    elif diff.status == STATUS_NOT_FOUND:
        stats.not_found += 1
    elif diff.status == STATUS_INVALID:
        stats.invalid += 1
    elif diff.status == STATUS_NET:
        stats.net += 1
        stats.errors += 1
    elif diff.status == STATUS_LOW_CONFIDENCE:
        stats.low_confidence += 1
    elif diff.status == STATUS_ZONE:
        stats.zone += 1
    elif diff.status == STATUS_UNBUILT:
        stats.unbuilt += 1
    else:
        stats.errors += 1


def _notify(
    on_progress: ProgressCallback | None, index: int, total: int, diff: EnrichmentDiff
) -> None:
    if on_progress is None:
        return
    # A progress callback must never stop the batch.
    with contextlib.suppress(Exception):
        on_progress(index, total, diff)


def _apply(record: PropertyRecord, diff: EnrichmentDiff, apply: ApplyCallback | None) -> None:
    for key, value in diff.changes.items():
        setattr(record, key, value)
    if apply is not None:
        apply(record, diff)


def enrich_batch(
    records: Sequence[PropertyRecord],
    *,
    cadastre: CadastreDriver | None = None,
    geocoder: Geocoder | None = None,
    kind: str = "cadastre",
    write: bool = False,
    overwrite: bool = False,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
    telemetry: RunLog | None = None,
    apply: ApplyCallback | None = None,
    accept_zone: bool = False,
    only_coords: bool = False,
    province: str | None = None,
    municipality_id: str = "",
    launched_by: str = "library",
) -> RunStats:
    """Run one enrichment kind over the records.

    ``kind`` is ``cadastre`` (coordinates and street via the cascade),
    ``physical`` (area, year, use; DGC only) or ``geocoding`` (address only,
    no cadastre). ``limit`` caps the number of *queried* records; already
    complete ones are skipped without a request. With ``write`` the diff is
    applied to the record object and handed to ``apply`` for persistence.
    """
    driver_name = (
        getattr(cadastre, "name", "") if kind != "geocoding" else getattr(geocoder, "source", "")
    )
    stats = RunStats(
        kind=kind, driver=driver_name, municipality_id=municipality_id, dry_run=not write
    )
    run_id = guarded_open(
        telemetry,
        municipality_id=municipality_id,
        kind=kind,
        driver=driver_name,
        dry_run=not write,
        launched_by=launched_by,
    )
    total = len(records)
    try:
        for index, record in enumerate(records):
            if limit is not None and stats.queried >= limit:
                break
            if kind == "physical":
                if cadastre is None:
                    raise ValueError("physical enrichment needs a cadastre driver")
                diff = physical_enrichment(record, cadastre=cadastre, overwrite=overwrite)
            elif kind == "geocoding":
                diff = enrich_property(
                    record,
                    cadastre=None,
                    geocoder=geocoder,
                    overwrite=overwrite,
                    accept_zone=accept_zone,
                    province=province,
                )
            else:
                diff = enrich_property(
                    record,
                    cadastre=cadastre,
                    geocoder=geocoder,
                    overwrite=overwrite,
                    only_coords=only_coords,
                    accept_zone=accept_zone,
                    province=province,
                )
            _count(stats, diff)
            if diff.changes:
                stats.would_write += 1
                if write:
                    _apply(record, diff, apply)
                    stats.written += 1
            elif diff.status not in (STATUS_SKIPPED, STATUS_OK):
                stats.pending.append(
                    (record.id, record.cadastral_reference or record.street or "", diff.reason)
                )
            _notify(on_progress, index, total, diff)
    finally:
        guarded_close(telemetry, run_id, stats)
    return stats


def enrich_imagery_batch(
    records: Sequence[PropertyRecord],
    *,
    drivers: Iterable[ImageryDriver],
    store: ImageStore,
    write: bool = False,
    overwrite: bool = False,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
    telemetry: RunLog | None = None,
    on_image: ImageCallback | None = None,
    municipality_id: str = "",
    launched_by: str = "library",
) -> RunStats:
    """Fetch one image per (property, source) for properties with
    coordinates. Existing images are not requested again unless
    ``overwrite``; identical bytes are de-duplicated by SHA-256."""
    drivers = list(drivers)
    names = "+".join(d.name for d in drivers)
    stats = RunStats(
        kind="imagery", driver=names, municipality_id=municipality_id, dry_run=not write
    )
    run_id = guarded_open(
        telemetry,
        municipality_id=municipality_id,
        kind="imagery",
        driver=names,
        dry_run=not write,
        launched_by=launched_by,
    )
    total = len(records)
    try:
        for index, record in enumerate(records):
            if limit is not None and stats.queried >= limit:
                break
            if not record.has_coordinates:
                diff = EnrichmentDiff(record.id, STATUS_PENDING, reason="no coordinates")
                stats.pending.append((record.id, record.inventory_number, diff.reason))
                _notify(on_progress, index, total, diff)
                continue
            diff = EnrichmentDiff(record.id, STATUS_OK)
            for driver in drivers:
                if store.has(record.id, driver.name, driver.kind) and not overwrite:
                    stats.skipped_existing += 1
                    continue
                stats.queried += 1
                stats.count_source(driver.name)
                result = driver.fetch(record.lat, record.lon)  # type: ignore[arg-type]
                if not result.ok:
                    if result.error_code == NO_COVERAGE:
                        stats.no_coverage += 1
                    elif result.error_code == NET:
                        stats.net += 1
                        stats.errors += 1
                    else:
                        stats.errors += 1
                    stats.pending.append((record.id, driver.name, result.error_code or NO_TOKEN))
                    diff.error_code = result.error_code
                    continue
                stats.ok += 1
                status, doc = store.store(record, result, write=write, overwrite=overwrite)
                if status == "duplicate":
                    stats.duplicates += 1
                elif status == "skipped_existing":
                    stats.skipped_existing += 1
                elif doc is not None:
                    stats.would_write += 1
                    diff.changes[f"image:{driver.name}"] = doc.relative_path
                    if status == "written":
                        stats.written += 1
                        if on_image is not None:
                            on_image(record, doc)
            _notify(on_progress, index, total, diff)
    finally:
        guarded_close(telemetry, run_id, stats)
    return stats
