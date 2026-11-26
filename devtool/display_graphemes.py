"""Dependency-free conservative display cluster boundaries.

Keep combining/spacing marks, emoji modifiers, flags, keycaps, tags, Hangul,
joiner sequences and virama sequences together. This is a display safeguard,
not a Unicode segmentation API. Extra joining is safe for bounded previews.
"""
from __future__ import annotations
import unicodedata as ud
from collections.abc import Iterator


def _ri(c: str) -> bool:
    return 0x1F1E6 <= ord(c) <= 0x1F1FF


def _hangul(c: str) -> str:
    n = ord(c)
    if 0x1100 <= n <= 0x115F or 0xA960 <= n <= 0xA97C: return 'L'
    if 0x1160 <= n <= 0x11A7 or 0xD7B0 <= n <= 0xD7C6: return 'V'
    if 0x11A8 <= n <= 0x11FF or 0xD7CB <= n <= 0xD7FB: return 'T'
    if 0xAC00 <= n <= 0xD7A3: return 'LV' if (n-0xAC00)%28 == 0 else 'LVT'
    return ''


def _prepend(c: str) -> bool:
    n = ord(c)
    return (0x600 <= n <= 0x605 or n in (0x6DD,0x70F,0x890,0x891,0x8E2,0xD4E,
            0x110BD,0x110CD,0x1193F,0x11941,0x11A3A,0x11D46,0x11F02) or
            0x111C2 <= n <= 0x111C3 or 0x11A84 <= n <= 0x11A89)


def display_clusters(text: str) -> Iterator[str]:
    if not text:
        return
    if text.isascii():
        yield from text
        return
    start = 0
    ri_count = int(_ri(text[0]))
    join_next = ud.combining(text[0]) == 9 or text[0] == '\u200d'
    for i in range(1, len(text)):
        prev, cur = text[i-1], text[i]
        n = ord(cur)
        h1, h2 = _hangul(prev), _hangul(cur)
        extending = (ud.category(cur).startswith('M') or cur in ('\u200c','\u200d') or
                     0x1F3FB <= n <= 0x1F3FF or 0xE0020 <= n <= 0xE007F)
        hangul = ((h1=='L' and h2 in ('L','V','LV','LVT')) or
                  (h1 in ('LV','V') and h2 in ('V','T')) or
                  (h1 in ('LVT','T') and h2=='T'))
        same = extending or join_next or _prepend(prev) or hangul or (_ri(cur) and ri_count%2==1)
        if not same:
            yield text[start:i]
            start = i
            ri_count = 0
        ri_count = ri_count+1 if _ri(cur) else (ri_count if extending else 0)
        # Keep the linker through intervening marks, including ZWJ.
        join_next = cur=='\u200d' or ud.combining(cur)==9 or (join_next and extending)
    yield text[start:]


def cluster_prefix(text: str, limit: int) -> str:
    """A bounded prefix without cutting a display cluster at the limit."""
    if len(text) <= limit:
        return text
    if text.isascii():
        return text[:limit]
    size = 0
    for cluster in display_clusters(text):
        if size + len(cluster) > limit:
            break
        size += len(cluster)
        if size == limit:
            break
    return text[:size]
