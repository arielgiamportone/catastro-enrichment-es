"""Navarra cadastre: INSPIRE WFS 2.0 filtered with ``cql_filter``."""

from __future__ import annotations

from typing import Any

from ..models import INVALID_RC, NO_COORDS, NOT_FOUND, CadastreResult
from ..xmlutil import find_first, float_of, text_of
from .base import CadastreDriver, normalize_rc
from .gml import feature_point

NAVARRA_WFS_URL = "https://inspire.navarra.es/services/CP/wfs"


def navarra_reference(rc: str) -> str:
    """Tail after the last dot (``ES.XXXX.CP.<RC>`` -> ``<RC>``); the plain
    nine-digit form is returned as is."""
    ref = normalize_rc(rc)
    if "." in ref:
        ref = ref.rsplit(".", 1)[-1]
    return ref


class NavarraDriver(CadastreDriver):
    name = "navarra"
    source = "navarra"
    default_delay = 0.4
    attribution = "Gobierno de Navarra - IDENA (CC-BY 4.0)"

    def __init__(self, *, url: str = NAVARRA_WFS_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url

    def lookup_rc(self, rc: str) -> CadastreResult:
        ref = navarra_reference(rc)
        if not ref.isdigit():
            return CadastreResult.failure(normalize_rc(rc), INVALID_RC, self.source)
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "CP:CadastralParcel",
            "cql_filter": f"nationalCadastralReference='{ref}'",
            "count": "1",
        }
        root, code = self._get_xml(self.url, params)
        if root is None:
            return CadastreResult.failure(ref, code, self.source)
        feature = find_first(root, "CadastralParcel")
        if root.get("numberReturned") == "0" or feature is None:
            return CadastreResult.failure(ref, NOT_FOUND, self.source)
        point = feature_point(feature)
        if point is None:
            return CadastreResult.failure(ref, NO_COORDS, self.source)
        local_id = text_of(feature, "localId")
        return CadastreResult(
            rc=ref,
            ok=True,
            description=f"Navarra parcel {local_id or ref}",
            lat=point[0],
            lon=point[1],
            area_m2=float_of(feature, "areaValue"),
            source=self.source,
            raw={"local_id": local_id},
        )
