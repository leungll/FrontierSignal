"""Deterministic Chinese typography normalization ("pangu" / 盘古之白).

The LLM prompts ASK for correct CJK typography, but models are inconsistent about
it — a half-width comma slips into a Chinese clause, a stray space wraps a Chinese
phrase. Prompt tweaks can't guarantee it. This module ENFORCES it in code, after
generation, so the output is 100% consistent regardless of what the model emitted.

Scope is deliberately conservative — only transforms that are unambiguously safe:
  1. Insert a space between a CJK char and an adjacent ASCII letter/digit
     (盘古之白: 「agent系统」→「agent 系统」).
  2. Convert half-width , ; : ! ? to full-width ONLY when touching a CJK char,
     so English fragments like "RLVR, RLHF" and ratios like "8:1" are untouched.
  3. Collapse a space sitting between two CJK chars (「反思 内化」→「反思内化」).

It is applied to prose fields (summary / why) — never to URLs, code spans, or the
markdown link line, so link syntax and inline `code` can't be corrupted.
"""

from __future__ import annotations

import re

# CJK Unified Ideographs + common CJK punctuation range we treat as "Chinese".
CJK = r"一-鿿㐀-䶿"

_CJK_THEN_ASCII = re.compile(rf"([{CJK}])([A-Za-z0-9])")
_ASCII_THEN_CJK = re.compile(rf"([A-Za-z0-9])([{CJK}])")
_SPACE_BETWEEN_CJK = re.compile(rf"([{CJK}]) +([{CJK}])")

# Half-width -> full-width, applied only adjacent to a CJK char (see below).
_PUNCT = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
# comma/semicolon/colon/bang/question touching CJK on either side
_HALF_PUNCT_AFTER_CJK = re.compile(rf"([{CJK}])\s*([,;:!?])")
_HALF_PUNCT_BEFORE_CJK = re.compile(rf"([,;:!?])\s*([{CJK}])")


def _fullwidth_after(m: re.Match[str]) -> str:
    return m.group(1) + _PUNCT[m.group(2)]


def _fullwidth_before(m: re.Match[str]) -> str:
    return _PUNCT[m.group(1)] + m.group(2)


def normalize(text: str) -> str:
    """Apply conservative CJK typography fixes. Idempotent."""
    if not text:
        return text

    # Full-width punctuation first (before we start inserting spaces), both sides.
    text = _HALF_PUNCT_AFTER_CJK.sub(_fullwidth_after, text)
    text = _HALF_PUNCT_BEFORE_CJK.sub(_fullwidth_before, text)

    # Drop spaces that ended up between two CJK chars.
    text = _SPACE_BETWEEN_CJK.sub(r"\1\2", text)

    # Insert the pangu space between CJK and ASCII. Run twice: a single char
    # sandwiched (「A中B」) needs two passes to space both sides.
    for _ in range(2):
        text = _CJK_THEN_ASCII.sub(r"\1 \2", text)
        text = _ASCII_THEN_CJK.sub(r"\1 \2", text)

    return text
