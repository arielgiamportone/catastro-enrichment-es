"""Dirección General del Catastro (common regime): OVC XML services and the
INSPIRE cadastral-parcel WFS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..geo import polygon_wkt
from ..models import (
    INVALID_RC,
    NO_COORDS,
    NOT_FOUND,
    PARSE,
    CadastreResult,
    PointLookup,
)
from ..xmlutil import find_all, find_first, float_of, int_of, text_of
from .base import CadastreDriver, normalize_rc
from .gml import exterior_ring, feature_ring_lat_lon, first_member

OVC_BASE = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/"
INSPIRE_CP_URL = "https://ovc.catastro.meh.es/INSPIRE/wfsCP.aspx"

RC_PARCEL_LENGTH = 14
RC_UNIT_LENGTH = 20

DGC_NOT_FOUND_CODES = frozenset({"9", "16"})
"""OVC business codes meaning "no such reference" / "no reference at point"."""


@dataclass(frozen=True)
class DgcTerritory:
    """Province and municipality as the OVC expects them: upper case, official
    INE spelling. Override the spelling when the inventory uses another."""

    province: str = ""
    municipality: str = ""

    def params(self) -> dict[str, str]:
        return {
            "Provincia": self.province.strip().upper(),
            "Municipio": self.municipality.strip().upper(),
        }


def _business_error(root: Any) -> tuple[str, str]:
    err = find_first(root, "err")
    if err is None:
        return "", ""
    return text_of(err, "cod"), text_of(err, "des")


def _unit_references(root: Any) -> list[str]:
    """20-character references listed in ``lrcdnp/rcdnp`` entries."""
    refs: list[str] = []
    for entry in find_all(root, "rcdnp"):
        rc_el = find_first(entry, "rc")
        ref = "".join(text_of(rc_el, part) for part in ("pc1", "pc2", "car", "cc1", "cc2"))
        if len(ref) == RC_UNIT_LENGTH and ref not in refs:
            refs.append(ref)
    return refs


@dataclass
class _PhysicalSummary:
    areas: list[float] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    constructions: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    units: int = 0

    @classmethod
    def from_root(cls, root: Any) -> _PhysicalSummary:
        summary = cls()
        for unit in find_all(root, "debi"):
            summary.units += 1
            area = float_of(unit, "sfc")
            year = int_of(unit, "ant")
            use = text_of(unit, "luso")
            if area is not None:
                summary.areas.append(area)
            if year:
                summary.years.append(year)
            if use and use not in summary.uses:
                summary.uses.append(use)
        for cons in find_all(root, "cons"):
            summary.constructions.append(
                {
                    "use": text_of(cons, "lcd"),
                    "floor": text_of(cons, "pt"),
                    "area_m2": float_of(cons, "stl"),
                }
            )
        summary.description = text_of(root, "ldt")
        return summary

    def merge(self, other: _PhysicalSummary) -> None:
        self.areas.extend(other.areas)
        self.years.extend(other.years)
        self.uses.extend(u for u in other.uses if u not in self.uses)
        self.constructions.extend(other.constructions)
        self.units += other.units
        if not self.description:
            self.description = other.description

    def first_use(self) -> str:
        if self.uses:
            return self.uses[0]
        return next((c["use"] for c in self.constructions if c["use"]), "")

    def total_area(self) -> float:
        if self.areas:
            return float(sum(self.areas))
        return float(sum(c["area_m2"] for c in self.constructions if c["area_m2"] is not None))


class DgcDriver(CadastreDriver):
    name = "dgc"
    source = "dgc"
    default_delay = 0.4
    attribution = "Dirección General del Catastro (Sede Electrónica del Catastro)"

    def __init__(
        self,
        territory: DgcTerritory | None = None,
        *,
        base_url: str = OVC_BASE,
        inspire_url: str = INSPIRE_CP_URL,
        max_units: int = 40,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.territory = territory or DgcTerritory()
        self.base_url = base_url.rstrip("/") + "/"
        self.inspire_url = inspire_url
        self.max_units = max_units

    # --- RC -> coordinates --------------------------------------------------

    def lookup_rc(self, rc: str) -> CadastreResult:
        ref = normalize_rc(rc)
        if len(ref) < RC_PARCEL_LENGTH:
            return CadastreResult.failure(ref, INVALID_RC, self.source)
        parcel = ref[:RC_PARCEL_LENGTH]
        params = {"SRS": "EPSG:4326", **self.territory.params(), "RC": parcel}
        root, code = self._get_xml(self.base_url + "ovccoordenadas.asmx/Consulta_CPMRC", params)
        if root is None:
            return CadastreResult.failure(ref, code, self.source)
        err_code, err_text = _business_error(root)
        if err_code:
            return CadastreResult.failure(ref, err_code, self.source, description=err_text)
        coord = find_first(root, "coord")
        description = text_of(coord, "ldt") if coord is not None else text_of(root, "ldt")
        lon = float_of(coord, "xcen")
        lat = float_of(coord, "ycen")
        if lon is None or lat is None:
            return CadastreResult.failure(ref, NO_COORDS, self.source, description=description)
        return CadastreResult(
            rc=ref,
            ok=True,
            description=description,
            lat=lat,
            lon=lon,
            source=self.source,
            raw={"parcel": parcel},
        )

    # --- physical data ------------------------------------------------------

    def physical_data(self, rc: str) -> CadastreResult:
        """Built area (sum over all units), oldest construction year and use.

        A 14-character parcel reference may answer with a list of units
        (``lrcdnp/rcdnp``) without areas; each unit is then queried by its
        20-character reference, up to ``max_units``, and aggregated. A zero
        total means an unbuilt parcel: ``NOT_FOUND`` with ``raw["unbuilt"]``
        set, which batches count separately.
        """
        ref = normalize_rc(rc)
        if len(ref) not in (RC_PARCEL_LENGTH, RC_UNIT_LENGTH):
            return CadastreResult.failure(ref, INVALID_RC, self.source)
        root, code = self._fetch_dnprc(ref)
        if root is None:
            return CadastreResult.failure(ref, code, self.source)
        err_code, err_text = _business_error(root)
        if err_code:
            return CadastreResult.failure(ref, err_code, self.source, description=err_text)

        summary = _PhysicalSummary.from_root(root)
        unit_refs = _unit_references(root)
        expanded = 0
        skipped: list[str] = []
        if unit_refs and not summary.areas:
            for unit_ref in unit_refs[: self.max_units]:
                unit_root, _ = self._fetch_dnprc(unit_ref)
                if unit_root is None or _business_error(unit_root)[0]:
                    skipped.append(unit_ref)
                    continue
                summary.merge(_PhysicalSummary.from_root(unit_root))
                expanded += 1
        raw: dict[str, Any] = {
            "units": max(len(unit_refs), summary.units),
            "expanded_units": expanded,
            "skipped_units": skipped,
            "truncated": len(unit_refs) > self.max_units,
            "constructions": summary.constructions,
        }
        total = summary.total_area()
        if total <= 0:
            return CadastreResult.failure(
                ref, NOT_FOUND, self.source, description="unbuilt parcel", unbuilt=True, **raw
            )
        return CadastreResult(
            rc=ref,
            ok=True,
            description=summary.description,
            area_m2=total,
            year=min(summary.years) if summary.years else None,
            use=summary.first_use(),
            source=self.source,
            raw=raw,
        )

    def _fetch_dnprc(self, ref: str) -> tuple[Any, str]:
        params = {**self.territory.params(), "RC": ref}
        return self._get_xml(self.base_url + "OVCCallejero.asmx/Consulta_DNPRC", params)

    # --- point -> RC ---------------------------------------------------------

    def rc_at_point(self, lat: float, lon: float) -> PointLookup:
        params = {"SRS": "EPSG:4326", "Coordenada_X": f"{lon:.6f}", "Coordenada_Y": f"{lat:.6f}"}
        root, code = self._get_xml(self.base_url + "ovccoordenadas.asmx/Consulta_RCCOOR", params)
        if root is None:
            return PointLookup(ok=False, error_code=code)
        err_code, err_text = _business_error(root)
        if err_code:
            mapped = NOT_FOUND if err_code in DGC_NOT_FOUND_CODES else err_code
            return PointLookup(ok=False, description=err_text, error_code=mapped)
        pc = find_first(root, "pc")
        rc = text_of(pc, "pc1") + text_of(pc, "pc2")
        if len(rc) != RC_PARCEL_LENGTH:
            return PointLookup(ok=False, error_code=PARSE)
        return PointLookup(ok=True, rc=rc, description=text_of(root, "ldt"))

    # --- address -> RCs -------------------------------------------------------

    def rcs_by_address(self, street_type: str, street: str, number: str | int) -> list[str]:
        """Parcel references at a street number; empty on error or no match."""
        params = {
            **self.territory.params(),
            "TipoVia": street_type.strip().upper(),
            "NomVia": street.strip().upper(),
            "Numero": str(number).strip(),
        }
        root, _ = self._get_xml(self.base_url + "OVCCallejero.asmx/ConsultaNumero", params)
        if root is None:
            return []
        err_code, _ = _business_error(root)
        if err_code:
            return []
        refs: list[str] = []
        for pc in find_all(root, "pc"):
            rc = text_of(pc, "pc1") + text_of(pc, "pc2")
            if len(rc) == RC_PARCEL_LENGTH and rc not in refs:
                refs.append(rc)
        return refs

    # --- INSPIRE parcel -------------------------------------------------------

    def _inspire_parcel(self, rc: str) -> tuple[Any, str]:
        ref = normalize_rc(rc)[:RC_PARCEL_LENGTH]
        if len(ref) < RC_PARCEL_LENGTH:
            return None, INVALID_RC
        params = {
            "service": "wfs",
            "version": "2.0.0",
            "request": "GetFeature",
            "STOREDQUERIE_ID": "GetParcel",
            "REFCAT": ref,
            "srsname": "EPSG::4326",
        }
        root, code = self._get_xml(self.inspire_url, params)
        if root is None:
            return None, code
        feature = find_first(root, "CadastralParcel")
        if feature is None:
            feature = first_member(root)
        if feature is None:
            return None, NOT_FOUND
        return feature, ""

    def parcel_area(self, rc: str) -> float | None:
        feature, _ = self._inspire_parcel(rc)
        return float_of(feature, "areaValue") if feature is not None else None

    def parcel_polygon_wkt(self, rc: str) -> str | None:
        feature, _ = self._inspire_parcel(rc)
        if feature is None or not exterior_ring(feature):
            return None
        return polygon_wkt(feature_ring_lat_lon(feature))


__all__ = ["DGC_NOT_FOUND_CODES", "DgcDriver", "DgcTerritory"]
