"""Bizkaia foral cadastre: ArcGIS Server WFS 2.0. The parcel layer has no
unified reference field, so the reference is split into municipality,
polygon and parcel codes and filtered with three ``PropertyIsEqualTo``."""

from __future__ import annotations

import re
from typing import Any

from ..models import INVALID_RC, NO_COORDS, NOT_FOUND, CadastreResult
from ..xmlutil import float_of
from .arcgis import equals_filter, get_feature_params
from .base import CadastreDriver, normalize_rc
from .gml import feature_point, first_member

BIZKAIA_WFS_URL = (
    "https://geo.bizkaia.eus/arcgisserverinspire/services/"
    "LurraldeAntolamendua_PlanificacionTerritorial/Katastro_Catastro_WFS/MapServer/WFSServer"
)
BIZKAIA_PARCEL_LAYER = "Katastro_Catastro_WFS:Parcelas"
BIZKAIA_FIELDS = ("Codigo_Mun", "Codigo_Pol", "Codigo_Par")
BIZKAIA_AREA_FIELD = "Area"

_SEPARATED = re.compile(r"^(\d+)[-/](\d+)[-/](\d+)$")


def split_bizkaia_rc(rc: str) -> tuple[int, int, int] | None:
    """``(municipality, polygon, parcel)`` as integers from ``MUN-POL-PAR``,
    ``MUN/POL/PAR``, 12 digits (3+4+5) or 9 digits (2+3+4). ``None`` when the
    reference does not fit any of those shapes."""
    ref = normalize_rc(rc)
    match = _SEPARATED.match(ref)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    if ref.isdigit() and len(ref) == 12:
        return int(ref[:3]), int(ref[3:7]), int(ref[7:])
    if ref.isdigit() and len(ref) == 9:
        return int(ref[:2]), int(ref[2:5]), int(ref[5:])
    return None


class BizkaiaDriver(CadastreDriver):
    name = "bizkaia"
    source = "bizkaia"
    default_delay = 0.4
    default_timeout = 15.0
    attribution = "Bizkaiko Foru Aldundia / Diputación Foral de Bizkaia (CC-BY 4.0)"

    def __init__(
        self,
        *,
        url: str = BIZKAIA_WFS_URL,
        layer: str = BIZKAIA_PARCEL_LAYER,
        fields: tuple[str, str, str] = BIZKAIA_FIELDS,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.layer = layer
        self.fields = fields

    def lookup_rc(self, rc: str) -> CadastreResult:
        ref = normalize_rc(rc)
        parts = split_bizkaia_rc(ref)
        if parts is None:
            return CadastreResult.failure(ref, INVALID_RC, self.source)
        conditions = {name: str(value) for name, value in zip(self.fields, parts, strict=True)}
        params = get_feature_params(self.layer, equals_filter(conditions))
        root, code = self._get_xml(self.url, params)
        if root is None:
            return CadastreResult.failure(ref, code, self.source)
        feature = first_member(root)
        if root.get("numberMatched") == "0" or feature is None:
            return CadastreResult.failure(ref, NOT_FOUND, self.source)
        point = feature_point(feature)
        if point is None:
            return CadastreResult.failure(ref, NO_COORDS, self.source)
        key = "-".join(str(p) for p in parts)
        return CadastreResult(
            rc=ref,
            ok=True,
            description=f"Bizkaia parcel {key}",
            lat=point[0],
            lon=point[1],
            area_m2=float_of(feature, BIZKAIA_AREA_FIELD),
            source=self.source,
            raw={"key": key},
        )
