"""Álava foral cadastre: ArcGIS Server WFS 2.0 with an FES filter on
``REF_CATASTRAL``; urban parcels are tried before rural ones."""

from __future__ import annotations

from typing import Any

from ..models import INVALID_RC, NO_COORDS, NOT_FOUND, CadastreResult
from ..xmlutil import float_of, text_of
from .arcgis import equals_filter, get_feature_params
from .base import CadastreDriver, normalize_rc
from .gml import feature_point, first_member

ALAVA_WFS_URL = "https://geo.araba.eus/WFS_Katastroa"
ALAVA_URBAN_LAYER = "WFS_Katastroa:PartzelaHiritarrak_ParcelasUrbanas"
ALAVA_RURAL_LAYER = "WFS_Katastroa:PartzelaLandatarrak_ParcelasRusticas"
ALAVA_RC_FIELD = "REF_CATASTRAL"
ALAVA_INFO_FIELD = "INFO"


class AlavaDriver(CadastreDriver):
    name = "alava"
    source = "alava"
    default_delay = 0.4
    attribution = "Arabako Foru Aldundia / Diputación Foral de Álava (CC-BY)"

    def __init__(
        self,
        *,
        url: str = ALAVA_WFS_URL,
        layers: tuple[str, ...] = (ALAVA_URBAN_LAYER, ALAVA_RURAL_LAYER),
        rc_field: str = ALAVA_RC_FIELD,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.layers = layers
        self.rc_field = rc_field

    def lookup_rc(self, rc: str) -> CadastreResult:
        ref = normalize_rc(rc)
        if not ref:
            return CadastreResult.failure(ref, INVALID_RC, self.source)
        last_code = NOT_FOUND
        for layer in self.layers:
            params = get_feature_params(layer, equals_filter({self.rc_field: ref}))
            root, code = self._get_xml(self.url, params)
            if root is None:
                last_code = code
                continue
            feature = first_member(root)
            if root.get("numberMatched") == "0" or feature is None:
                last_code = NOT_FOUND
                continue
            point = feature_point(feature)
            if point is None:
                return CadastreResult.failure(ref, NO_COORDS, self.source, layer=layer)
            return CadastreResult(
                rc=ref,
                ok=True,
                description=text_of(feature, ALAVA_INFO_FIELD),
                lat=point[0],
                lon=point[1],
                area_m2=float_of(feature, "SHAPE.STArea__"),
                source=self.source,
                raw={"layer": layer, "municipality": text_of(feature, "MUNICIPIO")},
            )
        return CadastreResult.failure(ref, last_code, self.source)
