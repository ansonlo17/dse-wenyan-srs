"""Hard-word suggestion engine (rule + lexicon based)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .db import SEED_DIR, ignored_terms

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@lru_cache(maxsize=1)
def load_hints() -> dict[str, Any]:
    path = SEED_DIR / "hard_word_hints.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _lexicon_entries() -> list[tuple[str, dict[str, Any], int]]:
    """Return (term, meta, priority_score_bonus) sorted by term length desc."""
    hints = load_hints()
    entries: list[tuple[str, dict[str, Any], int]] = []
    for key, bonus in (
        ("structures", 4),
        ("classical_senses", 3),
        ("common_hard", 2),
        ("particles", 2),
    ):
        bucket = hints.get(key) or {}
        for term, meta in bucket.items():
            if not term or not isinstance(meta, dict):
                continue
            entries.append((term, meta, bonus))
    entries.sort(key=lambda x: len(x[0]), reverse=True)
    return entries


def longest_match_scan(text: str) -> list[dict[str, Any]]:
    """Greedy longest-match over lexicon."""
    skip = set(load_hints().get("modern_skip") or [])
    entries = _lexicon_entries()
    n = len(text)
    i = 0
    found: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()

    while i < n:
        ch = text[i]
        if not CJK_RE.match(ch):
            i += 1
            continue
        matched = None
        for term, meta, bonus in entries:
            L = len(term)
            if L == 0 or i + L > n:
                continue
            if text[i : i + L] == term:
                matched = (term, meta, bonus, L)
                break
        if matched:
            term, meta, bonus, L = matched
            if term not in skip and (i, i + L) not in seen_spans:
                seen_spans.add((i, i + L))
                found.append(
                    {
                        "term": term,
                        "start": i,
                        "end": i + L,
                        "category": meta.get("category", "實詞"),
                        "dse_usage": meta.get("dse_usage", ""),
                        "difficulty": int(meta.get("difficulty", 3)),
                        "score": bonus + int(meta.get("difficulty", 3)),
                    }
                )
            i += L
        else:
            # single char not in lexicon: light score if rare-looking
            i += 1
    return found


def suggest_for_paragraph(
    original: str,
    translation: str | None = None,
    text_id: str | None = None,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    ignored = ignored_terms(text_id) if text_id else set()
    raw = longest_match_scan(original)
    # de-dupe by term keeping highest score
    best: dict[str, dict[str, Any]] = {}
    for item in raw:
        term = item["term"]
        if term in ignored:
            continue
        # boost if char not in translation (rough classical signal)
        if translation and term not in translation:
            item = {**item, "score": item["score"] + 1}
        prev = best.get(term)
        if not prev or item["score"] > prev["score"]:
            best[term] = item

    ranked = sorted(best.values(), key=lambda x: (-x["score"], -len(x["term"])))
    return ranked[:top_n]


def highlight_html(text: str, term_levels: dict[str, str]) -> str:
    """Wrap known terms with mark spans. Longest-first replace."""
    if not text:
        return ""
    if not term_levels:
        return _escape(text)

    terms = sorted(term_levels.keys(), key=len, reverse=True)
    # mask approach
    spans: list[tuple[int, int, str]] = []
    occupied = [False] * len(text)
    for term in terms:
        if not term:
            continue
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            end = idx + len(term)
            if not any(occupied[idx:end]):
                for j in range(idx, end):
                    occupied[j] = True
                spans.append((idx, end, term_levels[term]))
            start = idx + 1
    spans.sort(key=lambda x: x[0])

    out: list[str] = []
    cursor = 0
    for start, end, level in spans:
        if cursor < start:
            out.append(_escape(text[cursor:start]))
        cls = {
            "weak": "hl-weak",
            "learning": "hl-learning",
            "mastered": "hl-mastered",
        }.get(level, "hl-learning")
        out.append(f'<mark class="{cls}">{_escape(text[start:end])}</mark>')
        cursor = end
    if cursor < len(text):
        out.append(_escape(text[cursor:]))
    return "".join(out)


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def gloss_from_translation(translation: str | None, max_len: int = 80) -> str:
    if not translation:
        return ""
    t = translation.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"
