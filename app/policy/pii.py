"""Personal-data detection (Phase 5.B). Owned by Person B.

Finds IBANs (with the mod-97 checksum, so random codes do not trigger), phone numbers, and the full names
of people known from the mailbox. `redact()` masks what was found so a human can see what would have left
the company without seeing the data itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

KIND_LABEL = {"iban": "IBAN", "phone": "phone number", "name": "person's name"}

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b", re.IGNORECASE)
# +358 50 123 4567, 040 765 4321, +49 (0)30 1234567 ... Needs a leading + or 0, then groups of digits.
_PHONE_RE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s-]?|0)\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4}(?!\d)")
_LOCAL_NAME_RE = re.compile(r"^([a-z]{2,})[._-]([a-z]{2,})$")


@dataclass(frozen=True)
class PiiHit:
    kind: str   # "iban" | "phone" | "name"
    text: str
    start: int
    end: int


def iban_valid(value: str) -> bool:
    s = re.sub(r"\s+", "", value).upper()
    if not (15 <= len(s) <= 34) or not s.isalnum():
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # A=10 ... Z=35
    return int(digits) % 97 == 1


def names_from_addresses(addresses: Iterable[str]) -> set[str]:
    """'pekka.hiltunen@x.example' -> 'Pekka Hiltunen'. Single-word locals (sam@, it@) are not names."""
    names = set()
    for addr in addresses:
        m = _LOCAL_NAME_RE.match(addr.strip().lower().split("@")[0])
        if m:
            names.add(f"{m.group(1).capitalize()} {m.group(2).capitalize()}")
    return names


def find_pii(text: str, known_names: Iterable[str] = ()) -> list[PiiHit]:
    hits: list[PiiHit] = []
    for m in _IBAN_RE.finditer(text):
        if iban_valid(m.group(0)):
            hits.append(PiiHit("iban", m.group(0), m.start(), m.end()))
    taken = [(h.start, h.end) for h in hits]
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        overlaps = any(m.start() < e and s < m.end() for s, e in taken)
        if 8 <= len(digits) <= 15 and not overlaps:
            hits.append(PiiHit("phone", m.group(0), m.start(), m.end()))
    for name in known_names:
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in name.split()) + r"\b"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            hits.append(PiiHit("name", m.group(0), m.start(), m.end()))
    return sorted(hits, key=lambda h: h.start)


def redact(text: str, hits: list[PiiHit]) -> str:
    out, pos = [], 0
    for h in sorted(hits, key=lambda h: h.start):
        if h.start < pos:  # overlapping hit, already masked
            continue
        out.append(text[pos:h.start])
        out.append(f"[{h.kind.upper()}]")
        pos = h.end
    out.append(text[pos:])
    return "".join(out)
