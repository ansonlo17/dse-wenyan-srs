"""Paragraph alignment between original and translation."""

from __future__ import annotations

from typing import Any


def align_by_index(
    originals: list[dict[str, Any]],
    translations: list[dict[str, Any]],
) -> list[tuple[int | None, int | None, float]]:
    """
    Pair paragraphs by index.
    Returns list of (original_para_id, translation_para_id, confidence).
    """
    n_o, n_t = len(originals), len(translations)
    if n_o == 0 and n_t == 0:
        return []

    diff = abs(n_o - n_t)
    base_conf = 0.9 if diff <= 1 else (0.6 if diff <= 3 else 0.35)

    pairs: list[tuple[int | None, int | None, float]] = []
    n = max(n_o, n_t)
    for i in range(n):
        o_id = originals[i]["id"] if i < n_o else None
        t_id = translations[i]["id"] if i < n_t else None
        conf = base_conf if o_id and t_id else 0.0
        pairs.append((o_id, t_id, conf))
    return pairs


def auto_align_text(text_id: str) -> int:
    """Load paragraphs for text_id, align, save. Returns pair count."""
    from . import db

    originals = db.get_paragraphs(text_id, "original")
    translations = db.get_paragraphs(text_id, "translation")
    pairs = align_by_index(originals, translations)
    db.save_alignments(text_id, pairs)
    return len(pairs)
