"""Deterministic PII detection for the Azure AI Language surface.

The real service runs NER models; this simulator cannot, so detection is
transparent instead of pretend-neural (documented in docs/PII.md):

1. Regex detectors with validators for structured identifiers — Email,
   PhoneNumber, CreditCardNumber (Luhn-checked), USSocialSecurityNumber,
   UKNationalInsuranceNumber and IPAddress (IPv4, octets 0-255).
2. Simulation triggers for entities a regex cannot find, following the
   ``[simulate:...]`` convention: ``[simulate:pii=Person:Jane Doe]`` makes
   every occurrence of ``Jane Doe`` in the document a ``Person`` entity.
   Any supported category may be forced the same way.

Offsets and lengths are Python string indices (Unicode code points). Azure
defaults to ``TextElements_v8`` (grapheme clusters); the two agree for text
without combining sequences or emoji, which covers the APIM use case of a
JSON request body.

Redaction follows the documented policy kinds: ``characterMask`` keeps the
text the same length and only replaces entity characters (so a JSON body
stays valid JSON), ``entityMask`` substitutes ``[CATEGORY_N]`` and
``noMask`` returns entities without ``redactedText``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

MODEL_VERSION = "2026-05-01"

# Canonical category names, in the order overlapping matches are preferred
# when two detectors claim the same span with the same length.
CATEGORIES: tuple[str, ...] = (
    "Person",
    "Email",
    "USSocialSecurityNumber",
    "UKNationalInsuranceNumber",
    "CreditCardNumber",
    "IPAddress",
    "PhoneNumber",
)
_CANONICAL = {name.lower(): name for name in CATEGORIES}
_PRIORITY = {name: index for index, name in enumerate(CATEGORIES)}

CONFIDENCE = {
    "Person": 1.0,  # only produced by an explicit simulation trigger
    "Email": 0.8,
    "USSocialSecurityNumber": 0.85,
    "UKNationalInsuranceNumber": 0.85,
    "CreditCardNumber": 0.9,
    "IPAddress": 0.8,
    "PhoneNumber": 0.8,
}

# Characters Azure documents as valid ``redactionCharacter`` values.
REDACTION_CHARACTERS = frozenset("!#$%&*+-=?@^_~")
POLICY_KINDS = ("characterMask", "entityMask", "noMask")


def canonical_category(raw: str) -> str | None:
    return _CANONICAL.get(raw.lower())


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _credit_card_valid(match: str) -> bool:
    digits = re.sub(r"[ -]", "", match)
    return 13 <= len(digits) <= 19 and _luhn_valid(digits)


def _ipv4_valid(match: str) -> bool:
    return all(int(octet) <= 255 for octet in match.split("."))


@dataclass(frozen=True)
class _Detector:
    category: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] | None = None


_DETECTORS: tuple[_Detector, ...] = (
    _Detector("Email", re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    _Detector(
        "USSocialSecurityNumber",
        re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])"),
    ),
    _Detector(
        "UKNationalInsuranceNumber",
        re.compile(
            r"(?<![\w])(?!BG|GB|KN|NK|NT|TN|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D](?![\w])"
        ),
    ),
    _Detector("CreditCardNumber", re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])"), _credit_card_valid),
    _Detector("IPAddress", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"), _ipv4_valid),
    _Detector(
        "PhoneNumber",
        re.compile(
            r"(?<![\w+])(?:"
            r"\+1[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]\d{4}"  # +1 (555) 010-0199
            r"|\(\d{3}\) ?\d{3}[ .-]\d{4}"  # (555) 010-0199
            r"|\d{3}[.-]\d{3}[.-]\d{4}"  # 555-010-0199
            r"|\+44 ?\d{2,4} ?\d{3,4} ?\d{3,4}"  # +44 20 7946 0958
            r"|0\d{3,4} \d{3} ?\d{3,4}"  # 020 7946 0958 / 01632 960 001
            r")(?![\w])"
        ),
    ),
)

# ``[simulate:pii=Person:Jane Doe]``. The value cannot contain ``"`` or
# ``\`` so a characterMask redaction never breaks a surrounding JSON string.
_TRIGGER_RE = re.compile(r"\[simulate:pii=([A-Za-z]+):([^\]\"\\]+)\]")


@dataclass(frozen=True)
class PiiEntity:
    text: str
    category: str
    offset: int
    length: int
    confidence_score: float

    def payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "category": self.category,
            "offset": self.offset,
            "length": self.length,
            "confidenceScore": self.confidence_score,
        }


def _trigger_candidates(text: str) -> list[PiiEntity]:
    candidates: list[PiiEntity] = []
    for trigger in _TRIGGER_RE.finditer(text):
        category = canonical_category(trigger.group(1))
        value = trigger.group(2).strip()
        if category is None or not value:
            continue
        for occurrence in re.finditer(re.escape(value), text):
            candidates.append(PiiEntity(value, category, occurrence.start(), len(value), CONFIDENCE["Person"]))
    return candidates


def detect(text: str, categories: set[str] | None = None, *, simulation_triggers: bool = True) -> list[PiiEntity]:
    """Return non-overlapping entities in offset order.

    ``categories`` limits the result to canonical category names (Azure's
    ``piiCategories``); ``None`` means every supported category. Where spans
    overlap, the earliest wins, then the longest, then the category order in
    ``CATEGORIES``.
    """
    candidates: list[PiiEntity] = _trigger_candidates(text) if simulation_triggers else []
    for detector in _DETECTORS:
        for match in detector.pattern.finditer(text):
            value = match.group(0)
            if detector.validate is not None and not detector.validate(value):
                continue
            candidates.append(
                PiiEntity(value, detector.category, match.start(), len(value), CONFIDENCE[detector.category])
            )
    if categories is not None:
        candidates = [entity for entity in candidates if entity.category in categories]
    candidates.sort(key=lambda entity: (entity.offset, -entity.length, _PRIORITY[entity.category]))
    selected: list[PiiEntity] = []
    end = -1
    for entity in candidates:
        if entity.offset >= end:
            selected.append(entity)
            end = entity.offset + entity.length
    return selected


@dataclass(frozen=True)
class RedactionPolicy:
    kind: str = "characterMask"
    character: str = "*"
    entity_types: frozenset[str] | None = None  # None = default policy


def _policy_for(category: str, policies: list[RedactionPolicy]) -> RedactionPolicy:
    for policy in policies:
        if policy.entity_types is not None and category in policy.entity_types:
            return policy
    for policy in policies:
        if policy.entity_types is None:
            return policy
    return RedactionPolicy()


def redact(text: str, entities: list[PiiEntity], policies: list[RedactionPolicy]) -> str | None:
    """Apply redaction policies; ``None`` when every policy is ``noMask``."""
    if policies and all(policy.kind == "noMask" for policy in policies):
        return None
    parts: list[str] = []
    cursor = 0
    counters: dict[str, int] = {}
    labels: dict[tuple[str, str], str] = {}
    for entity in entities:
        policy = _policy_for(entity.category, policies)
        parts.append(text[cursor : entity.offset])
        if policy.kind == "characterMask":
            parts.append(policy.character * entity.length)
        elif policy.kind == "entityMask":
            key = (entity.category, entity.text)
            if key not in labels:
                counters[entity.category] = counters.get(entity.category, 0) + 1
                labels[key] = f"[{entity.category.upper()}_{counters[entity.category]}]"
            parts.append(labels[key])
        else:  # noMask for this category
            parts.append(entity.text)
        cursor = entity.offset + entity.length
    parts.append(text[cursor:])
    return "".join(parts)
