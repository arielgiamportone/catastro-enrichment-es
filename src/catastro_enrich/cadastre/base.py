"""Abstract cadastre driver: one interface for five Spanish cadastres."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping

from ..models import NET, NOT_SUPPORTED, PARSE, CadastreResult
from ..transport import DEFAULT_TIMEOUT, HttpResponse, Pacer, Transport, UrllibTransport
from ..xmlutil import parse_xml


def normalize_rc(rc: str | None) -> str:
    """Trim, upper-case and drop inner whitespace."""
    if rc is None:
        return ""
    return "".join(str(rc).split()).upper()


class CadastreDriver(ABC):
    """Common behaviour: paced, transport-injected HTTP and XML fetching.

    Subclasses set ``name`` (router key) and ``source`` (attribution key, used
    in results) and implement :meth:`lookup_rc`.
    """

    name: str = "abstract"
    source: str = "abstract"
    default_delay: float = 0.4
    default_timeout: float = DEFAULT_TIMEOUT
    attribution: str = ""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        delay: float | None = None,
        timeout: float | None = None,
        pacer: Pacer | None = None,
    ) -> None:
        self.transport: Transport = transport or UrllibTransport()
        self.delay = self.default_delay if delay is None else float(delay)
        self.timeout = self.default_timeout if timeout is None else float(timeout)
        self.pacer = pacer or Pacer(self.delay)

    # --- HTTP helpers -------------------------------------------------------

    def _get(self, url: str, params: Mapping[str, str] | None = None) -> HttpResponse | None:
        self.pacer.wait()
        return self.transport.get(url, params, timeout=self.timeout)

    def _get_xml(
        self, url: str, params: Mapping[str, str] | None = None
    ) -> tuple[ET.Element | None, str]:
        """Fetch and parse XML; return ``(root, error_code)``. Network
        failures and HTTP errors map to ``NET``, malformed XML to ``PARSE``."""
        response = self._get(url, params)
        if response is None or not response.ok:
            return None, NET
        root = parse_xml(response.content)
        if root is None:
            return None, PARSE
        return root, ""

    # --- public contract ----------------------------------------------------

    @abstractmethod
    def lookup_rc(self, rc: str) -> CadastreResult:
        """Coordinates (WGS84) and official description for a reference."""

    def physical_data(self, rc: str) -> CadastreResult:
        """Built area, construction year and use. Only the DGC exposes it."""
        return CadastreResult.failure(normalize_rc(rc), NOT_SUPPORTED, self.source)

    def batch(self, rcs: Iterable[str]) -> Iterator[CadastreResult]:
        for rc in rcs:
            yield self.lookup_rc(rc)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} delay={self.delay}>"
