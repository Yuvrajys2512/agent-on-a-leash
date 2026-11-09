"""Prompt-injection heuristics (Phase 4.B2). Owned by Person B.

Looks for text that is aimed at an AI assistant rather than at a human reader. This is only ONE layer:
the rules also look at where the recipient address came from (see engine.py), so a trick that uses none
of these phrases is still caught and sent to the human with a warning.

Evasions handled: odd whitespace, zero-width characters, look-alike letters (Cyrillic/Greek), leetspeak,
letters spaced apart, a few languages, base64 hidden payloads, and addresses written "x at y dot z".
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'"})
_LEET = str.maketrans("013457@$", "oieastas")
# Cyrillic / Greek letters that look like Latin ones
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "к": "k", "м": "m", "т": "t", "і": "i", "ѕ": "s", "ј": "j", "ӏ": "l",
    "ο": "o", "ι": "i", "α": "a", "ν": "v", "ρ": "p", "τ": "t", "κ": "k", "ε": "e",
})

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_OBFUSCATED_ADDR_RE = re.compile(
    r"\b([\w.+-]+)\s*(?:\(at\)|\[at\]|\bat\b)\s*([\w-]+(?:\s*(?:\(dot\)|\[dot\]|\bdot\b)\s*[\w-]+)+)", re.IGNORECASE)
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")

_AI = r"(?:ai|a\.i\.|assistant|agent|llm|language model|chatbot|bot|model|automated system)"
_USER = r"(?:the|your|this)\s+(?:user|human|owner|operator|account holder)"
_ADDR = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
# "note for the assistant manager" is about a person with a job title, not about an AI
_NOT_A_JOB = r"(?!\s+(?:manager|director|professor|editor|principal|coach|teacher|secretary|engineer|nurse|coordinator|officer|lecturer|clerk|account))"

# category -> pattern, applied to normalised text (lower-case, NFKC, no zero-width, look-alikes folded)
PATTERNS: dict[str, re.Pattern] = {
    # "ignore previous instructions", "disregard the above", "forget everything you were told", and a few languages
    "override": re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,30}"
        r"\b(?:previous|prior|above|earlier|all|any|your)\b[^.\n]{0,30}"
        r"\b(?:instructions?|rules?|prompts?|guidelines?|restrictions?)\b"
        r"|\b(?:ignore|disregard|forget)\b[^.\n]{0,15}\b(?:the above|everything above|all of the above|everything (?:you|that)|what you (?:were|have been) told)"
        r"|\bohita\s+(?:kaikki\s+)?(?:aiemmat|edelliset)\s+ohje\w*"
        r"|\bignoriere?\s+(?:alle\s+)?(?:vorherigen|früheren|obigen)\s+(?:anweisungen|instruktionen|regeln)"
        r"|\bignorer?\s+(?:toutes\s+)?les\s+instructions\s+(?:précédentes|ci-dessus)"
        r"|\bignora\s+(?:todas\s+)?las\s+instrucciones\s+(?:anteriores|previas)"
        r"|\bignorera\s+(?:alla\s+)?(?:tidigare|föregående)\s+instruktioner"),
    # "system note for AI assistant", "if you are an AI", "as an AI agent you must"
    "addresses_ai": re.compile(
        rf"\b(?:system\s+|important\s+)?(?:note|message|instruction|notice|attention|override|prompt)s?\s+(?:for|to)\s+(?:the\s+|any\s+|all\s+)?{_AI}\b{_NOT_A_JOB}"
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
        r"\b(?:all|every|any|each|entire|whole)\b[^.\n]{0,80}?" + _ADDR),
    # any "forward/send/cc ... someone@somewhere" (no quantifier needed)
    "directive_send": re.compile(r"\b(?:forward|send|email|e-mail|cc|bcc|copy|redirect|route|mail)\b[^.\n]{0,80}?" + _ADDR),
    # "authorised by the CFO"
    "authority": re.compile(
        r"\b(?:authori[sz]ed|approved|instructed|ordered)\s+by\s+(?:the\s+)?(?:cfo|ceo|cto|director|manager|admin|management|it)\b"),
    # weaker signals: only suspicious together with a send instruction
    "mode_switch": re.compile(r"\byou are now\s+(?:in\s+\w+\s+mode|an?\s+\w+|the\s+\w+\s+(?:bot|assistant|agent))"),
    "role_play": re.compile(r"\b(?:pretend|act)\s+(?:you\s+are|to\s+be|as)\b"),
    "secrecy_soft": re.compile(r"\b(?:do\s*not|don't|dont|never)\s+(?:mention|tell|say|share|disclose|reveal)\s+(?:this|it|that)\b"),
}
STRONG = ("override", "addresses_ai", "secrecy", "hidden_payload")  # any one of these is enough on its own
MEDIUM = ("mode_switch", "role_play", "secrecy_soft")  # suspicious with a send instruction
SENDS = ("exfil", "directive_send")
PRIORITY = ("override", "addresses_ai", "secrecy", "exfil", "directive_send", "authority", *MEDIUM)


@dataclass(frozen=True)
class Finding:
    category: str
    phrase: str


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).translate(_APOSTROPHES).lower().translate(_HOMOGLYPHS)
    return text.replace(" ", " ")


def normalise(text: str) -> str:
    return re.sub(r"[ \t]+", " ", _fold(text))


def _despace(text: str) -> str:
    """'i g n o r e  p r e v i o u s' -> 'ignore previous'. Runs of 5+ single characters separated by single
    spaces are joined; two or more spaces mean a word boundary."""
    marked = re.sub(r"[ \t]{2,}", "\x00", text)
    joined = re.sub(r"(?<!\w)(?:\w ){4,}\w(?!\w)", lambda m: m.group(0).replace(" ", ""), marked)
    return joined.replace("\x00", " ")


def _views(text: str) -> list[str]:
    raw = _fold(text)
    n = normalise(text)
    leet, raw_leet = n.translate(_LEET), raw.translate(_LEET)
    views = [n, leet, _despace(raw), _despace(raw_leet)]
    return list(dict.fromkeys(re.sub(r"[ \t]+", " ", v) for v in views))


def _decoded_blobs(text: str) -> list[str]:
    out = []
    for token in _BASE64_RE.findall(text):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded and sum(c.isprintable() or c.isspace() for c in decoded) / len(decoded) > 0.95:
            out.append(decoded)
    return out


def scan(text: str, _depth: int = 0) -> list[Finding]:
    found: dict[str, Finding] = {}
    for view in _views(text):
        for cat in PRIORITY:
            if cat not in found:
                m = PATTERNS[cat].search(view)
                if m:
                    found[cat] = Finding(cat, m.group(0).strip()[:90])
    if _depth == 0:  # text hidden in base64 that itself contains instructions is suspicious by being hidden
        for decoded in _decoded_blobs(text):
            inner = scan(decoded, _depth=1)
            if any(f.category in (*STRONG, *SENDS) for f in inner):
                found.setdefault("hidden_payload", Finding("hidden_payload", "base64: " + decoded.strip()[:80]))
    return [found[c] for c in (*PRIORITY, "hidden_payload") if c in found]


def verdict(findings: list[Finding]) -> Finding | None:
    """The finding that makes the text suspicious, or None. Benign mentions of 'forward' or 'AI' do not count."""
    cats = {f.category: f for f in findings}
    for cat in STRONG:
        if cat in cats:
            return cats[cat]
    has_send = any(c in cats for c in SENDS)
    if "exfil" in cats and "authority" in cats:  # "send all X to y@z, authorised by the CFO"
        return cats["exfil"]
    if has_send:
        for cat in MEDIUM:  # "you are now in maintenance mode, forward ...", "pretend you are ... send it to y@z"
            if cat in cats:
                return cats[cat]
    return None


def addresses_in(text: str) -> set[str]:
    """Every email address in the text, including ones written 'x at y dot z' or hidden in base64."""
    found = {a.lower() for a in EMAIL_RE.findall(text)}
    for m in _OBFUSCATED_ADDR_RE.finditer(text):
        domain = re.sub(r"\s*(?:\(dot\)|\[dot\]|\bdot\b)\s*", ".", m.group(2), flags=re.IGNORECASE)
        candidate = f"{m.group(1)}@{domain}".lower()
        if EMAIL_RE.fullmatch(candidate):  # "at noon dot dot dot" is not an address
            found.add(candidate)
    for decoded in _decoded_blobs(text):
        found |= {a.lower() for a in EMAIL_RE.findall(decoded)}
    return found
