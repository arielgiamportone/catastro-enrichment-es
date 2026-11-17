"""Province -> driver resolution.

The two leading digits of a municipality INE code identify the province. Four
provinces run their own cadastre; everything else is served by the DGC. A
valid INE code always wins over a province name.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from ..transport import Transport
from .alava import AlavaDriver
from .base import CadastreDriver
from .bizkaia import BizkaiaDriver
from .dgc import DgcDriver, DgcTerritory
from .gipuzkoa import GipuzkoaDriver
from .navarra import NavarraDriver

FORAL_PROVINCES: dict[str, type[CadastreDriver]] = {
    "01": AlavaDriver,
    "20": GipuzkoaDriver,
    "31": NavarraDriver,
    "48": BizkaiaDriver,
}

PROVINCE_SYNONYMS: dict[str, str] = {
    "alava": "01",
    "araba": "01",
    "araba/alava": "01",
    "alava/araba": "01",
    "gipuzkoa": "20",
    "guipuzcoa": "20",
    "navarra": "31",
    "nafarroa": "31",
    "nafarroa/navarra": "31",
    "navarra/nafarroa": "31",
    "bizkaia": "48",
    "vizcaya": "48",
}

DRIVER_NAMES = {"01": "alava", "20": "gipuzkoa", "31": "navarra", "48": "bizkaia"}


def normalize_province_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


def province_code(value: str | int | None) -> str | None:
    """Two-digit province code from an INE code (2 or 5 digits, str or int);
    ``None`` when the value is not a valid code."""
    if value is None:
        return None
    text = str(value).strip()
    if isinstance(value, int):
        text = f"{value:02d}" if value < 100 else f"{value:05d}"
    if not text.isdigit() or len(text) not in (2, 5):
        return None
    code = text[:2]
    if not 1 <= int(code) <= 52:
        return None
    return code


def province_code_from_name(name: str | None) -> str | None:
    if not name:
        return None
    return PROVINCE_SYNONYMS.get(normalize_province_name(name))


def driver_name_for(code: str | None) -> str:
    return DRIVER_NAMES.get(code or "", "dgc")


def resolve_cadastre_driver(
    territory: str | int | None = None,
    *,
    ine_code: str | int | None = None,
    province_name: str | None = None,
    dgc_territory: DgcTerritory | tuple[str, str] | None = None,
    delay: float | None = None,
    transport: Transport | None = None,
    timeout: float | None = None,
    **driver_kwargs: Any,
) -> CadastreDriver:
    """Pick the cadastre driver for a territory.

    ``territory`` may be an INE code (province or municipality) or a province
    name; ``ine_code`` and ``province_name`` are the explicit forms. A valid
    INE code always wins. Unknown names fall back to the DGC and never raise.
    """
    if isinstance(territory, int) or (isinstance(territory, str) and territory.strip().isdigit()):
        ine_code = territory if ine_code is None else ine_code
    elif isinstance(territory, str) and province_name is None:
        province_name = territory

    code = province_code(ine_code) or province_code_from_name(province_name)
    common: dict[str, Any] = {"transport": transport, "delay": delay, "timeout": timeout}
    common.update(driver_kwargs)
    driver_cls = FORAL_PROVINCES.get(code or "")
    if driver_cls is not None:
        return driver_cls(**common)
    if isinstance(dgc_territory, tuple):
        dgc_territory = DgcTerritory(*dgc_territory)
    if dgc_territory is None and province_name:
        dgc_territory = DgcTerritory(province=province_name)
    return DgcDriver(dgc_territory, **common)
