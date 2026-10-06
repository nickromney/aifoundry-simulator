"""Memoization must retain exact hashing, dimensions and owned output vectors."""

import hashlib

from app.embeddings import _bucket_and_sign, _cached_bucket_and_sign, embed_text


def reference(feature, dimensions):
    value = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
    return value % dimensions, 1.0 if (value >> 63) & 1 == 0 else -1.0


def test_hash_cache_dimensions_eviction_and_long_key_bypass():
    _cached_bucket_and_sign.cache_clear()
    for dimensions in (8, 256, 1024):
        for index in range(1100):
            feature = f"w:term-{index}"
            assert _bucket_and_sign(feature, dimensions) == reference(feature, dimensions)
        assert _bucket_and_sign("w:term-0", dimensions) == reference("w:term-0", dimensions)
    assert _cached_bucket_and_sign.cache_info().currsize == 1024
    before = _cached_bucket_and_sign.cache_info()
    feature = "w:" + "x" * 10000
    assert _bucket_and_sign(feature, 256) == reference(feature, 256)
    assert _cached_bucket_and_sign.cache_info() == before


def test_memoized_features_do_not_share_mutable_vectors():
    first = embed_text("same words", 8)
    expected = first.copy()
    first[0] = 99
    assert embed_text("same words", 8) == expected
    assert len(embed_text("same words", 256)) == 256
