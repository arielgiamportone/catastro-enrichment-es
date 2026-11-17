"""Cadastre drivers and the provincial router."""

from .alava import AlavaDriver
from .base import CadastreDriver, normalize_rc
from .bizkaia import BizkaiaDriver, split_bizkaia_rc
from .dgc import DGC_NOT_FOUND_CODES, DgcDriver, DgcTerritory
from .gipuzkoa import GipuzkoaDriver, gipuzkoa_reference
from .navarra import NavarraDriver, navarra_reference
from .router import (
    FORAL_PROVINCES,
    driver_name_for,
    normalize_province_name,
    province_code,
    province_code_from_name,
    resolve_cadastre_driver,
)

__all__ = [
    "DGC_NOT_FOUND_CODES",
    "FORAL_PROVINCES",
    "AlavaDriver",
    "BizkaiaDriver",
    "CadastreDriver",
    "DgcDriver",
    "DgcTerritory",
    "GipuzkoaDriver",
    "NavarraDriver",
    "driver_name_for",
    "gipuzkoa_reference",
    "navarra_reference",
    "normalize_province_name",
    "normalize_rc",
    "province_code",
    "province_code_from_name",
    "resolve_cadastre_driver",
    "split_bizkaia_rc",
]
