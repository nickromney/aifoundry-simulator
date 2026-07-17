"""Deterministic local embeddings.

The simulator cannot ship a neural embedding model, and a fake similarity
metric would teach the wrong mental model — so the mechanics are real and
only the featurisation is simple. Texts are hashed into a fixed-dimension
vector using feature hashing over lowercase word unigrams (weighted) and
character trigrams, then L2-normalised. Cosine similarity over these vectors
behaves the way users expect from an embeddings API: identical texts score
1.0, near-duplicates score close to 1.0, and unrelated texts score near 0.0.

The honest limitation, documented in docs/SEMANTIC-CACHE.md: similarity is
lexical, not semantic. Paraphrases that share no vocabulary will not match.
"""

from __future__ import annotations

import hashlib
import math
import re

_WORD_RE = re.compile(r"[a-z0-9']+")
_WORD_WEIGHT = 3.0
_TRIGRAM_WEIGHT = 1.0


def _bucket_and_sign(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    bucket = value % dimensions
    sign = 1.0 if (value >> 63) & 1 == 0 else -1.0
    return bucket, sign


def embed_text(text: str, dimensions: int = 256) -> list[float]:
    vector = [0.0] * dimensions
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    for word in words:
        bucket, sign = _bucket_and_sign(f"w:{word}", dimensions)
        vector[bucket] += sign * _WORD_WEIGHT
    compact = re.sub(r"\s+", " ", lowered).strip()
    for start in range(max(0, len(compact) - 2)):
        trigram = compact[start : start + 3]
        bucket, sign = _bucket_and_sign(f"t:{trigram}", dimensions)
        vector[bucket] += sign * _TRIGRAM_WEIGHT
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # Empty input maps to a fixed unit vector so cosine stays defined.
        vector[0] = 1.0
        return vector
    return [component / norm for component in vector]


def cosine_similarity(first: list[float], second: list[float]) -> float:
    if len(first) != len(second):
        raise ValueError("vectors must share dimensions")
    return sum(a * b for a, b in zip(first, second, strict=True))


def cosine_distance(first: list[float], second: list[float]) -> float:
    """Vector distance in [0, 2]; the semantic cache treats lower as more similar."""
    return 1.0 - cosine_similarity(first, second)
