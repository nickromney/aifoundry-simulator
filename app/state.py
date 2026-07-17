"""Shared per-application state: loaded config plus runtime stores."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import Blocklist, FoundryConfig
from app.content_safety import SafetyStats
from app.semantic_cache import SemanticCache


@dataclass
class AppState:
    config: FoundryConfig
    semantic_cache: SemanticCache = field(default_factory=SemanticCache)
    safety_stats: SafetyStats = field(default_factory=SafetyStats)
    started_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # The Content Safety CRUD surface mutates blocklists at runtime;
        # filter policies read the same dict, so edits apply immediately.
        # Each app instance loads its own config, so no copy is needed.
        self.blocklists: dict[str, Blocklist] = self.config.blocklists
