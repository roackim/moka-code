"""Grapheme cluster helpers for stream reveal granularity.

No stdlib segmentation exists, so this is a small bounded implementation that
honours the cases that matter for terminal text: combining marks, ZWJ sequences,
variation selectors, skin-tone modifiers, regional-indicator pairs and Hangul
jamo. Whitespace is released for free by the revealer, so counts here ignore
whitespace-only clusters.

The reveal unit is the cluster, not the display width; ``wcwidth`` is
deliberately not used here.
"""

from __future__ import annotations

import unicodedata

_ZWJ = "\u200d"

_REGIONAL_INDICATORS = (0x1F1E6, 0x1F200)
_SKIN_TONES = (0x1F3FB, 0x1F400)
_VS15 = (0xFE00, 0xFE10)
_VS_SUPP = (0xE0100, 0xE01F0)
_HANGUL_L = (0x1100, 0x1160)
_HANGUL_V = (0x1160, 0x11A8)
_HANGUL_T = (0x11A8, 0x1200)


def _in(cp: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= cp < rng[1]


def _is_extend(ch: str) -> bool:
    """True for code points that attach to the preceding base character."""
    cp = ord(ch)
    # Mn (combining) and Me (enclosing marks, e.g. the keycap U+20E3) attach.
    if unicodedata.combining(ch) or unicodedata.category(ch).startswith("M"):
        return True
    if _in(cp, _SKIN_TONES) or _in(cp, _VS15) or _in(cp, _VS_SUPP):
        return True
    return False


def split_clusters(text: str) -> list[str]:
    """Split ``text`` into grapheme clusters.

    Unknown scripts fall back to per-code-point boundaries, which is safe (a
    cluster is never split) though not always minimal.
    """
    if not text:
        return []

    clusters: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        start = i
        ch = text[i]
        i += 1

        # Regional indicators pair up (flags): consume one partner if present.
        if _in(ord(ch), _REGIONAL_INDICATORS):
            if i < n and _in(ord(text[i]), _REGIONAL_INDICATORS):
                i += 1
            clusters.append(text[start:i])
            continue

        # CRLF is a single cluster.
        if ch == "\r" and i < n and text[i] == "\n":
            i += 1
            clusters.append(text[start:i])
            continue

        # Hangul jamo: leading consonant + vowel + optional trailing consonant.
        if _in(ord(ch), _HANGUL_L):
            while i < n and _in(ord(text[i]), _HANGUL_V):
                i += 1
            while i < n and _in(ord(text[i]), _HANGUL_T):
                i += 1
            clusters.append(text[start:i])
            continue

        # Generic: consume trailing extends / ZWJ sequences.
        while i < n:
            nxt = text[i]
            if _is_extend(nxt):
                i += 1
                continue
            if nxt == _ZWJ:
                i += 1
                if i < n:
                    i += 1  # the joined base; further extends handled next pass
                continue
            break

        clusters.append(text[start:i])

    return clusters


def count_nonws(text: str) -> int:
    """Number of grapheme clusters in ``text`` that are not pure whitespace."""
    return sum(1 for cluster in split_clusters(text) if cluster.strip())


def advance_nonws(text: str, n: int) -> int:
    """Char offset after releasing ``n`` non-whitespace clusters.

    Whitespace before and between those clusters is included for free (it is
    never counted but always traversed). Returns ``len(text)`` when fewer than
    ``n`` non-whitespace clusters exist.
    """
    if n <= 0:
        return 0

    count = 0
    offset = 0
    for cluster in split_clusters(text):
        offset += len(cluster)
        if not cluster.strip():
            continue
        count += 1
        if count >= n:
            return offset
    return len(text)
