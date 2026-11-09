"""Prompt-injection heuristics (Phase 4.B2). Owned by Person B.

Looks for text that is aimed at an AI assistant rather than at a human reader. This is only ONE layer:
the rules also look at where the recipient address came from (see engine.py), so a trick that uses none
of these phrases is still caught.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'"})

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_AI = r"(?:ai|a\.i\.|assistant|agent|llm|language model|chatbot|bot|model|automated system)"
_USER = r"(?:the|your|this)\s+(?:user|human|owner|operator|account holder)"

# category -> pattern, applied to normalised (lower-case, NFKC, no zero-width) text
PATTERNS: dict[str, re.Pattern] = {
    # "ignore previous instructions", "disregard all prior rules"
    "override": re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,30}"
        r"\b(?:previous|prior|above|earlier|all|any|your)\b[^.\n]{0,30}"
        r"\b(?:instructions?|rules?|prompts?|guidelines?|restrictions?)\b"),
    # "system note for AI assistant", "if you are an AI", "as an AI agent"
    "addresses_ai": re.compile(
        rf"\b(?:system\s+|important\s+)?(?:note|message|instruction|notice|attention|override|prompt)s?\s+(?:for|to)\s+(?:the\s+|any\s+|all\s+)?{_AI}\b"
        rf"|\bif you are an?\s+{_AI}\b"
        rf"|\bas an? (?:ai|llm|language model)\b(?:\s+(?:assistant|agent))?,?\s+(?:you must|you should|you will)"),
    # "do not tell the user", "without informing the owner", "keep this secret from the user"
    "secrecy": re.compile(
        rf"\b(?:do\s*not|don't|dont|never)\s+(?:tell|inform|notify|alert|show|mention\s+(?:this\s+)?to)\s+{_USER}"
        rf"|\bwithout\s+(?:telling|informing|notifying|alerting)\s+{_USER}"
        rf"|\bkeep\s+(?:this|it)\s+(?:secret|hidden|confidential)\s+from\s+{_USER}"),
    # "forward all invoices ... to someone@somewhere"
    "exfil": re.compile(
        r"\b(?:forward|send|email|e-mail|cc|bcc|copy|redirect|route)\b[^.\n]{0,60}"
        r"\b(?:all|every|any|each|entire|whole)\b[^.\n]{0,80}?[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    # "authorised by the CFO"
    "authority": re.compile(
        r"\b(?:authori[sz]ed|approved|instructed|ordered)\s+by\s+(?:the\s+)?(?:cfo|ceo|cto|director|manager|admin|management|it)\b"),
}
STRONG = ("override", "addresses_ai", "secrecy")  # any one of these is enough on its own
PRIORITY = ("override", "addresses_ai", "secrecy", "exfil", "authority")


@dataclass(frozen=True)
class Finding:
    category: str
    phrase: str


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).translate(_APOSTROPHES).lower()
    return re.sub(r"[ \t]+", " ", text)


def scan(text: str) -> list[Finding]:
    norm = normalise(text)
    out = []
    for cat in PRIORITY:
        m = PATTERNS[cat].search(norm)
        if m:
            out.append(Finding(cat, m.group(0).strip()[:90]))
    return out


def verdict(findings: list[Finding]) -> Finding | None:
    """The finding that makes the text suspicious, or None. Benign mentions of 'forward' or 'AI' do not count."""
    cats = {f.category: f for f in findings}
    for cat in STRONG:
        if cat in cats:
            return cats[cat]
    if "exfil" in cats and "authority" in cats:  # "send all X to y@z, authorised by the CFO"
        return cats["exfil"]
    return None


def addresses_in(text: str) -> set[str]:
    return {a.lower() for a in EMAIL_RE.findall(text)}
