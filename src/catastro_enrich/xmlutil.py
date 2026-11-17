"""Namespace-tolerant XML helpers.

Public services are inconsistent about namespaces (ArcGIS Server may answer
with an internal namespace that differs from the published one), so every
lookup here is by *local* tag name.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator


def parse_xml(data: bytes | str) -> ET.Element | None:
    """Parse leniently; return ``None`` on malformed input."""
    try:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_local(root: ET.Element, name: str) -> Iterator[ET.Element]:
    for el in root.iter():
        if local_name(el.tag) == name:
            yield el


def find_first(root: ET.Element | None, name: str) -> ET.Element | None:
    if root is None:
        return None
    return next(iter_local(root, name), None)


def find_all(root: ET.Element | None, name: str) -> list[ET.Element]:
    if root is None:
        return []
    return list(iter_local(root, name))


def text_of(root: ET.Element | None, name: str, default: str = "") -> str:
    el = find_first(root, name)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def float_of(root: ET.Element | None, name: str) -> float | None:
    text = text_of(root, name)
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def int_of(root: ET.Element | None, name: str) -> int | None:
    text = text_of(root, name)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def attr_of(root: ET.Element | None, name: str) -> str:
    """Attribute value by local name on the given element."""
    if root is None:
        return ""
    for key, value in root.attrib.items():
        if local_name(key) == name:
            return value
    return ""
