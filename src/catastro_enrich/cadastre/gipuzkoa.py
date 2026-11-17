"""Gipuzkoa foral cadastre: INSPIRE WFS 2.0 stored query."""

from __future__ import annotations

import re
from typing import Any

from ..models import INVALID_RC, NOT_FOUND, CadastreResult
from ..xmlutil import find_first, float_of, text_of
from .base import CadastreDriver, normalize_rc
from .gml import feature_point

GIPUZKOA_WFS_URL = "https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_cp"
STORED_QUERY = "urn:x-inspire:query:GetNationalRefParcel"

_INSPIRE_FORM = re.compile(r"^(\d{1,3})-([0-9A-Z]+)-(\d{1,4})$")


def gipuzkoa_reference(rc: str) -> str:
    """Value sent as ``nationalcadastralreference``: the short numeric form, or
    the middle part of the ``MMM-REF-NNNN`` INSPIRE form."""
    ref = normalize_rc(rc)
    match = _INSPIRE_FORM.match(ref)
    return match.group(2) if match else ref


class GipuzkoaDriver(CadastreDriver):
    name = "gipuzkoa"
    source = "gipuzkoa"
    default_delay = 0.3
    attribution = "Gipuzkoako Foru Aldundia / Diputación Foral de Gipuzkoa (CC-BY)"

    def __init__(self, *, url: str = GIPUZKOA_WFS_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url

    def lookup_rc(self, rc: str) -> CadastreResult:
        ref = normalize_rc(rc)
        query_ref = gipuzkoa_reference(ref)
        if not query_ref:
            return CadastreResult.failure(ref, INVALID_RC, self.source)
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "storedquery_id": STORED_QUERY,
            "nationalcadastralreference": query_ref,
            "count": "1",
        }
        root, code = self._get_xml(self.url, params)
        if root is None:
            return CadastreResult.failure(ref, code, self.source)
        feature = find_first(root, "CadastralParcel")
        if root.get("numberMatched") == "0" or feature is None:
            return CadastreResult.failure(ref, NOT_FOUND, self.source)
        point = feature_point(feature)
        local_id = text_of(feature, "localId")
        national = text_of(feature, "nationalCadastralReference") or query_ref
        if point is None:
            return CadastreResult.failure(ref, "NO_COORDS", self.source, local_id=local_id)
        return CadastreResult(
            rc=ref,
            ok=True,
            description=f"Gipuzkoa parcel {local_id or national}",
            lat=point[0],
            lon=point[1],
            area_m2=float_of(feature, "areaValue"),
            source=self.source,
            raw={"local_id": local_id, "national_reference": national},
        )
