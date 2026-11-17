"""Clean-room guard: the repository tree and its history must not contain
any term from the leak list that governs this case study.

The list itself is stored base64-encoded so that it is not a readable leak in
the public repository, and this file is excluded from the sweep.

Matching rules:

* ``WORDS`` are matched as whole words, case- and accent-insensitive.
* ``SUBSTRINGS`` are matched anywhere, case- and accent-insensitive.
* ``ALLOWED`` strings (for example the owner's public handle) are masked
  before matching, and lines that match ``ALLOWED_LINE`` (the attribution
  sentence required by the publication rules) are skipped.

The sweep covers every tracked or untracked-but-not-ignored file, file names
included, plus ``git log -p`` of the current branch with full author and
committer details.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import unicodedata
from pathlib import Path

import pytest

SELF = Path(__file__).resolve()
ROOT = SELF.parent.parent

WORDS = "WyJJbnRlcnZhbG9yIENvbnN1bHRpbmcgR3JvdXAiLCAiaW50ZXJ2YWxvci5lcyIsICJpbnRlcnZhbG9yc29mdCIsICJBWVVOREFUQSIsICJJTlZBWVVOVCIsICJJTlRFUlZBTCIsICJJTlRFUkRBVEEiLCAiQXNzZXREYXRhIiwgIkFzc2V0VmlzaW9uIiwgIkludkRhdGEiLCAiS0FUSEFSU0lEIiwgIlBMVUciLCAiRGF0YVN0YXIiLCAiZ2V0cGx1Z2dlZCIsICJNZWdhc2lzdGVtYXMiLCAiTWFuc2lzIiwgIkFFTkEiLCAiRVVJUE8iLCAiQmFyYWphcyIsICJNQUQgNjkvMjMiLCAiTUFELTExMiIsICJPVDEiLCAiU2FuIEJhcnRvbG9tw6kiLCAiVGlyYWphbmEiLCAiRmlyZ2FzIiwgIlphbXVkaW8iLCAiU2FndW50byIsICJPbGFiZXJyaWEiLCAiQnJ1Z2Fyb2xhcyIsICJBaXphcm5hemFiYWwiLCAiTWV0cm8gZGUgTWFkcmlkIiwgIkRpcHV0YWNpw7NuIGRlIFNvcmlhIiwgIkFnw6huY2lhIGRlIFJlc2lkdXMiLCAiQXJpZWwgR2lhbXBvcnRlIiwgIkludGVydmFsb3IgRGF0YSIsICJpbnRlcnZhbG9yLWRhdGEuY29tIiwgImF5dW5kYXRhLWthdGhhcnNpZCIsICJGQVNFIDY1IiwgIkZBU0UgOTciLCAiU3ByaW50IDJCIiwgIlNwcmludCA3IiwgIkJsb3F1ZSBBIiwgIlNhbiBCYXJ0b2xvbcOpIGRlIFRpcmFqYW5hIiwgIlNCVCIsICJTYW4gQWd1c3TDrW4gZGUgR3VhZGFsaXgiLCAiWXVycmUiLCAiUmHDumwiLCAiVmljdG9yaWEiLCAiTnVyaWEiLCAiRXZhIiwgIkFyaWVsIiwgIkdpYW1wb3J0b25lIiwgIkx1asOhbiIsICJiaWVuZXNfaW5tdWVibGVzIiwgImVucmljaG1lbnRfcnVucyIsICJkb2N1bWVudG9zIiwgImthdGhhcnNpZF9pbWFnZXJ5IiwgImVucmlxdWVjZXJfbXVuaWNpcGlvIiwgImdlb2NvZGlmaWNhcl9tdW5pY2lwaW8iLCAicmVzb2x2ZXJfZHJpdmVyIiwgIlJlc3VsdGFkb0NhdGFzdHJvIiwgIl91dG0iLCAiUGlwZWxpbmVDb250ZXh0IiwgImRvY3NfZGF0YV9yb290IiwgInVzZXJkYXRhX2RpciIsICJpbnZlbnRhcmlvX3NidC5kYiIsICJHUkFGQ0FOIiwgIk9uZURyaXZlIl0="
SUBSTRINGS = "WyJpbnRlcnZhbG9yIiwgImF5dW5kYXRhIiwgImthdGhhcnNpZCIsICJpbnZheXVudCIsICJnaWFtcG9ydG9uZSIsICJjOlxcdXNlcnMiLCAib25lZHJpdmUgLSBpbnRlcnZhbG9yIiwgImdpYW1wb3J0b25lMUBnbWFpbC5jb20iXQ=="
ALLOWED = "WyJhcmllbGdpYW1wb3J0b25lIl0="
ALLOWED_LINE = "ImludGVydmFsb3IgZGF0YS4qKGF1dG9yaXphY2l8YXV0aG9yaVtzel1hdGlvbnxwZXJtaXNzaW9uKSI="

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
}


def _decode(blob: str) -> list[str] | str:
    return json.loads(base64.b64decode(blob).decode("utf-8"))


def fold(text: str) -> str:
    """Lower-case and strip accents so that matching is accent-insensitive."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _compile() -> tuple[list[tuple[str, re.Pattern[str]]], list[str], re.Pattern[str]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for term in _decode(WORDS):
        folded = fold(term)
        patterns.append((term, re.compile(rf"(?<!\w){re.escape(folded)}(?!\w)")))
    for term in _decode(SUBSTRINGS):
        patterns.append((term, re.compile(re.escape(fold(term)))))
    allowed = [fold(a) for a in _decode(ALLOWED)]
    allowed_line = re.compile(str(_decode(ALLOWED_LINE)))
    return patterns, allowed, allowed_line


PATTERNS, ALLOWED_STRINGS, ALLOWED_LINE_RE = _compile()


def _mask(term: str) -> str:
    """Show only the first and last character of a term in failure output."""
    if len(term) <= 2:
        return "*" * len(term)
    return term[0] + "*" * (len(term) - 2) + term[-1]


def scan_text(text: str, location: str) -> list[str]:
    hits: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = fold(raw)
        if ALLOWED_LINE_RE.search(line):
            continue
        for allowed in ALLOWED_STRINGS:
            line = line.replace(allowed, "<allowed>")
        for term, pattern in PATTERNS:
            if pattern.search(line):
                hits.append(f"{location}:{lineno}: {_mask(term)}")
    return hits


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, errors="replace", check=False
    )
    return result.stdout


def repository_files() -> list[Path]:
    listing = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files: list[Path] = []
    for entry in listing.split("\0"):
        if not entry:
            continue
        path = ROOT / entry
        if not path.is_file() or path.resolve() == SELF:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def test_tree_has_no_forbidden_terms() -> None:
    hits: list[str] = []
    for path in repository_files():
        rel = path.relative_to(ROOT).as_posix()
        hits.extend(scan_text(rel, f"{rel} (name)"))
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        hits.extend(scan_text(text, rel))
    assert not hits, "forbidden terms found in the tree:\n" + "\n".join(hits)


def test_history_has_no_forbidden_terms() -> None:
    inside = _git("rev-parse", "--is-inside-work-tree").strip()
    if inside != "true":
        pytest.skip("not a git checkout")
    log = _git(
        "log",
        "-p",
        "--format=fuller",
        "HEAD",
        "--",
        ".",
        f":(exclude){SELF.relative_to(ROOT).as_posix()}",
    )
    hits = scan_text(log, "git log -p")
    assert not hits, "forbidden terms found in git history:\n" + "\n".join(hits)


def test_leak_list_is_usable() -> None:
    """Sanity check of the detector itself, using synthetic text only."""
    assert len(PATTERNS) > 50
    assert scan_text("nothing to see here\nstreet 12, Madrid", "probe") == []
    probe = fold(str(_decode(WORDS)[0]))
    assert scan_text(f"prefix {probe} suffix", "probe")
