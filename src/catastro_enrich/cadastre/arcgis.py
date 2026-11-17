"""Shared pieces for ArcGIS Server WFS 2.0 endpoints (Álava and Bizkaia).

ArcGIS ignores ``cql_filter`` and silently returns the first feature, so the
filter must be an OGC FES 2.0 XML document passed in the ``filter`` parameter.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

FES_NS = "http://www.opengis.net/fes/2.0"


def equals_filter(conditions: dict[str, str]) -> str:
    """FES 2.0 ``PropertyIsEqualTo`` filter; several conditions are joined
    with ``And``."""
    parts = "".join(
        f"<fes:PropertyIsEqualTo><fes:ValueReference>{escape(name)}</fes:ValueReference>"
        f"<fes:Literal>{escape(value)}</fes:Literal></fes:PropertyIsEqualTo>"
        for name, value in conditions.items()
    )
    if len(conditions) > 1:
        parts = f"<fes:And>{parts}</fes:And>"
    return f'<fes:Filter xmlns:fes="{FES_NS}">{parts}</fes:Filter>'


def get_feature_params(type_name: str, fes_filter: str, *, count: int = 1) -> dict[str, str]:
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": type_name,
        "filter": fes_filter,
        "count": str(count),
    }
