"""Minimal HTTP transport abstraction.

Drivers never talk to the network directly; they receive a ``Transport`` whose
``get`` returns an ``HttpResponse`` or ``None`` when the request could not be
completed (DNS failure, timeout, connection reset). HTTP error statuses are
returned as responses so that callers can classify them (a 429 is counted as
``NET`` without aggressive retries).

The default implementation uses the standard library only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import __version__

DEFAULT_TIMEOUT = 15.0
REPOSITORY_URL = "https://github.com/arielgiamportone/catastro-enrichment-es"
CONTACT_ENV = "CATASTRO_ENRICH_CONTACT"


def default_user_agent(contact: str | None = None) -> str:
    """Identifiable User-Agent, as public services ask for. The contact comes
    from the argument or the ``CATASTRO_ENRICH_CONTACT`` environment variable."""
    contact = contact or os.environ.get(CONTACT_ENV, "")
    base = f"catastro-enrich/{__version__} (+{REPOSITORY_URL}"
    return f"{base}; {contact})" if contact else f"{base})"


@dataclass
class HttpResponse:
    status: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").lower()

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class Transport(Protocol):
    def get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse | None: ...


def build_url(url: str, params: Mapping[str, str] | None) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(dict(params), quote_via=urllib.parse.quote)
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{query}"


class UrllibTransport:
    """Standard-library transport. Network failures return ``None``."""

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or default_user_agent()

    def get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse | None:
        full = build_url(url, params)
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        request = urllib.request.Request(full, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    content=response.read(),
                    headers={k.lower(): v for k, v in response.headers.items()},
                    url=full,
                )
        except urllib.error.HTTPError as err:
            body = err.read() if hasattr(err, "read") else b""
            return HttpResponse(
                status=err.code,
                content=body,
                headers={k.lower(): v for k, v in err.headers.items()} if err.headers else {},
                url=full,
            )
        except (urllib.error.URLError, OSError, ValueError):
            return None


class Pacer:
    """Guarantees a minimum delay between consecutive calls (fair use).

    ``clock`` and ``sleep`` are injectable so tests can verify pacing without
    waiting.
    """

    def __init__(
        self,
        delay: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = max(0.0, float(delay))
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> float:
        """Sleep as needed and return the seconds actually waited."""
        waited = 0.0
        now = self._clock()
        if self._last is not None:
            remaining = self.delay - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                waited = remaining
                now = self._clock()
        self._last = now
        return waited
