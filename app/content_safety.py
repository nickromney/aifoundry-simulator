"""Deterministic content safety engine.

Real moderation models cannot run in this simulator, and pretending
otherwise would be misleading — so verdicts are transparent instead of
pretend-neural. Three mechanisms drive severities, all documented in
docs/CONTENT-SAFETY.md:

1. Simulation triggers — ``[simulate:hate=6]``, ``[simulate:jailbreak]``,
   ``[simulate:output-violence=4]`` — force exact verdicts so tests, demos,
   and gateway policies can exercise every branch without offensive content
   in prompts or this repository.
2. Configurable lexicons — per-category term lists in severity bands
   (2=low, 4=medium, 6=high); severity is the highest band matched. The
   shipped demo lexicon is deliberately naive ("kill" trips violence-low on
   "kill the process"), which is itself a teaching point about lexical
   classifiers and false positives.
3. Jailbreak heuristics — common prompt-injection phrasings trip Prompt
   Shields, mirroring the ``text:shieldPrompt`` surface.

The *mechanics* around the verdicts — category names, severity scales,
blocklist matching, the 400 ``content_filter`` error shape, annotations on
successful completions — follow the documented Azure wire formats.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.config import CATEGORIES, Blocklist, ContentFilterPolicy, FoundryConfig

AZURE_CATEGORY_NAMES = {"hate": "Hate", "self_harm": "SelfHarm", "sexual": "Sexual", "violence": "Violence"}
INTERNAL_CATEGORY_NAMES = {name.lower(): internal for internal, name in AZURE_CATEGORY_NAMES.items()}
SEVERITY_LABELS = {0: "safe", 2: "low", 4: "medium", 6: "high"}

_TRIGGER_RE = re.compile(r"\[simulate:(output-)?([a-z_-]+)(?:=([0-7]))?\]")

_JAILBREAK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|directions|rules)",
        r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|directions|rules)",
        r"forget\s+(?:all\s+)?(?:your|previous)\s+(?:instructions|rules|guidelines)",
        r"pretend\s+(?:that\s+)?you\s+(?:have\s+no|are\s+free\s+of)\s+(?:restrictions|rules|guidelines)",
        r"bypass\s+(?:your\s+)?safety",
        r"reveal\s+(?:your\s+)?(?:hidden\s+)?system\s+prompt",
        r"you\s+are\s+now\s+(?:dan|unrestricted|jailbroken)",
        r"do\s+anything\s+now",
    )
)


@dataclass
class TriggerSet:
    prompt_categories: dict[str, int] = field(default_factory=dict)
    output_categories: dict[str, int] = field(default_factory=dict)
    jailbreak: bool = False


@dataclass
class CategoryAnalysis:
    category: str  # internal key: hate | self_harm | sexual | violence
    severity: int  # 0-7; lexicon hits produce 2/4/6, triggers may produce any
    matched_terms: list[str] = field(default_factory=list)
    simulated: bool = False


@dataclass
class BlocklistMatch:
    blocklist_name: str
    item_id: str
    item_text: str


def _normalise_category(raw: str) -> str | None:
    candidate = raw.replace("-", "_").lower()
    if candidate in CATEGORIES:
        return candidate
    return INTERNAL_CATEGORY_NAMES.get(raw.replace("-", "").replace("_", "").lower())


def extract_triggers(text: str) -> TriggerSet:
    triggers = TriggerSet()
    for match in _TRIGGER_RE.finditer(text.lower()):
        is_output = match.group(1) is not None
        name = match.group(2)
        severity = int(match.group(3)) if match.group(3) is not None else 6
        if name == "jailbreak":
            triggers.jailbreak = True
            continue
        category = _normalise_category(name)
        if category is None:
            continue
        target = triggers.output_categories if is_output else triggers.prompt_categories
        target[category] = max(target.get(category, 0), severity)
    return triggers


def _lexicon_severity(text: str, bands: dict[int, tuple[str, ...]]) -> tuple[int, list[str]]:
    severity = 0
    matched: list[str] = []
    for band in (6, 4, 2):
        for term in bands.get(band, ()):
            if re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
                matched.append(term)
                severity = max(severity, band)
    return severity, matched


def analyze_text(
    text: str,
    config: FoundryConfig,
    *,
    categories: list[str] | None = None,
    include_output_triggers: bool = False,
) -> dict[str, CategoryAnalysis]:
    """Score one text across the four harm categories.

    ``categories`` filters the result (Azure ``text:analyze`` accepts a
    category subset). ``include_output_triggers`` folds ``output-`` triggers
    in as well; the model pipeline uses that for completion-side filtering.
    """
    wanted = set(CATEGORIES)
    if categories:
        requested = {_normalise_category(category) for category in categories}
        wanted = {category for category in requested if category is not None}
    triggers = extract_triggers(text) if config.simulation_triggers else TriggerSet()
    results: dict[str, CategoryAnalysis] = {}
    for category in CATEGORIES:
        if category not in wanted:
            continue
        severity, matched = _lexicon_severity(text, config.lexicons[category])
        simulated = False
        trigger_severity = triggers.prompt_categories.get(category, 0)
        if include_output_triggers:
            trigger_severity = max(trigger_severity, triggers.output_categories.get(category, 0))
        if trigger_severity > severity:
            severity = trigger_severity
            simulated = True
        results[category] = CategoryAnalysis(
            category=category, severity=severity, matched_terms=matched, simulated=simulated
        )
    return results


def detect_jailbreak(text: str, config: FoundryConfig) -> bool:
    if config.simulation_triggers and extract_triggers(text).jailbreak:
        return True
    return any(pattern.search(text) for pattern in _JAILBREAK_PATTERNS)


def match_blocklists(text: str, blocklists: list[Blocklist]) -> list[BlocklistMatch]:
    matches: list[BlocklistMatch] = []
    for blocklist in blocklists:
        for item in blocklist.items:
            if item.is_regex:
                try:
                    hit = re.search(item.text, text, re.IGNORECASE) is not None
                except re.error:
                    hit = False
            else:
                hit = item.text.lower() in text.lower()
            if hit:
                matches.append(BlocklistMatch(blocklist_name=blocklist.name, item_id=item.item_id, item_text=item.text))
    return matches


def to_four_level(severity: int) -> int:
    """Bucket a 0-7 severity down to the FourSeverityLevels scale (0, 2, 4, 6)."""
    if severity >= 6:
        return 6
    if severity >= 4:
        return 4
    if severity >= 2:
        return 2
    return 0


def severity_label(severity: int) -> str:
    return SEVERITY_LABELS[to_four_level(severity)]


@dataclass
class FilterOutcome:
    """Result of applying one content filter policy to one text."""

    blocked: bool
    analyses: dict[str, CategoryAnalysis]
    blocked_categories: list[str]
    jailbreak_detected: bool = False
    blocklist_matches: list[BlocklistMatch] = field(default_factory=list)

    def content_filter_result(self, *, include_jailbreak: bool) -> dict[str, Any]:
        """Azure OpenAI annotation shape shared by 400 bodies and 200 annotations."""
        result: dict[str, Any] = {}
        for category in CATEGORIES:
            analysis = self.analyses.get(category)
            severity = analysis.severity if analysis is not None else 0
            result[category] = {
                "filtered": category in self.blocked_categories,
                "severity": severity_label(severity),
            }
        if include_jailbreak:
            result["jailbreak"] = {"filtered": self.jailbreak_detected, "detected": self.jailbreak_detected}
        if self.blocklist_matches:
            result["custom_blocklists"] = {
                "filtered": True,
                "details": [
                    {"filtered": True, "id": match.blocklist_name, "block_item_id": match.item_id}
                    for match in self.blocklist_matches
                ],
            }
        return result


def apply_filter_policy(
    text: str,
    policy: ContentFilterPolicy,
    config: FoundryConfig,
    *,
    is_output: bool = False,
) -> FilterOutcome:
    analyses = analyze_text(text, config, include_output_triggers=is_output)
    blocked_categories: list[str] = []
    for category, analysis in analyses.items():
        threshold = policy.block_threshold(category)
        if threshold is not None and analysis.severity >= threshold:
            blocked_categories.append(category)
    jailbreak = False
    if not is_output and policy.prompt_shield:
        jailbreak = detect_jailbreak(text, config)
    blocklist_matches: list[BlocklistMatch] = []
    if not is_output and policy.blocklists:
        resolved = [config.blocklists[name] for name in policy.blocklists if name in config.blocklists]
        blocklist_matches = match_blocklists(text, resolved)
    blocked = bool(blocked_categories) or jailbreak or bool(blocklist_matches)
    return FilterOutcome(
        blocked=blocked,
        analyses=analyses,
        blocked_categories=blocked_categories,
        jailbreak_detected=jailbreak,
        blocklist_matches=blocklist_matches,
    )


class SafetyStats:
    """Counters plus a small event ring for the management surface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=50)
        self.prompt_checks = 0
        self.prompts_blocked = 0
        self.output_checks = 0
        self.outputs_filtered = 0
        self.analyze_calls = 0
        self.shield_calls = 0
        self.shield_attacks_detected = 0
        self.blocked_by_category: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
        self.blocked_by_jailbreak = 0
        self.blocked_by_blocklist = 0

    def record_prompt_check(self, deployment: str, outcome: FilterOutcome) -> None:
        with self._lock:
            self.prompt_checks += 1
            if outcome.blocked:
                self.prompts_blocked += 1
                for category in outcome.blocked_categories:
                    self.blocked_by_category[category] += 1
                if outcome.jailbreak_detected:
                    self.blocked_by_jailbreak += 1
                if outcome.blocklist_matches:
                    self.blocked_by_blocklist += 1
                self._events.append(
                    {
                        "type": "prompt_blocked",
                        "deployment": deployment,
                        "categories": outcome.blocked_categories,
                        "jailbreak": outcome.jailbreak_detected,
                        "blocklists": [match.blocklist_name for match in outcome.blocklist_matches],
                        "at": time.time(),
                    }
                )

    def record_output_check(self, deployment: str, outcome: FilterOutcome) -> None:
        with self._lock:
            self.output_checks += 1
            if outcome.blocked:
                self.outputs_filtered += 1
                self._events.append(
                    {
                        "type": "output_filtered",
                        "deployment": deployment,
                        "categories": outcome.blocked_categories,
                        "at": time.time(),
                    }
                )

    def record_analyze(self) -> None:
        with self._lock:
            self.analyze_calls += 1

    def record_shield(self, attack_detected: bool) -> None:
        with self._lock:
            self.shield_calls += 1
            if attack_detected:
                self.shield_attacks_detected += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "prompt_checks": self.prompt_checks,
                "prompts_blocked": self.prompts_blocked,
                "output_checks": self.output_checks,
                "outputs_filtered": self.outputs_filtered,
                "analyze_calls": self.analyze_calls,
                "shield_calls": self.shield_calls,
                "shield_attacks_detected": self.shield_attacks_detected,
                "blocked_by_category": dict(self.blocked_by_category),
                "blocked_by_jailbreak": self.blocked_by_jailbreak,
                "blocked_by_blocklist": self.blocked_by_blocklist,
                "events": list(self._events),
            }
