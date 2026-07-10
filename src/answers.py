"""Multi-answer grading: any one acceptable meaning counts as correct."""

from __future__ import annotations

import re
from typing import Iterable


_SEP = re.compile(r"[／/、,，;；|｜\n]+")
_NON_WORD = re.compile(r"[^\w\u4e00-\u9fff]+", flags=re.UNICODE)


def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    return _NON_WORD.sub("", s)


def _clean_piece(raw: str) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    # drop parenthetical notes
    t = re.split(r"[（(]", t, maxsplit=1)[0].strip()
    t = t.strip("「」『』\"'。．.、，,；;：: ")
    if not t or len(t) > 20:
        return None
    if t in {"一說", "或", "指", "見", "即", "通"}:
        return None
    # refuse leftover separators
    if _SEP.search(t):
        return None
    n = normalize(t)
    if not n or len(n) > 16:
        return None
    return t


def split_acceptables(*blobs: str) -> list[str]:
    """
    Extract short acceptable answers from free text.

    Examples:
      "窮困、貧困（久處約）" -> 窮困, 貧困
      "窮困／困苦／貧困" -> 窮困, 困苦, 貧困
      "通「智」" -> 智
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        cleaned = _clean_piece(raw)
        if not cleaned:
            return
        n = normalize(cleaned)
        if n not in seen:
            seen.add(n)
            found.append(cleaned)

    for blob in blobs:
        if not blob:
            continue
        text = str(blob)
        # quoted 「智」
        for m in re.findall(r"[「『\"']([^」』\"']{1,12})[」』\"']", text):
            add(m)
        # split separators then 或
        for part in _SEP.split(text):
            part = part.strip()
            if not part:
                continue
            if "或" in part and len(part) <= 24:
                for sub in part.split("或"):
                    add(sub)
            else:
                add(part)
    return found


def merge_acceptables(
    explicit: str | Iterable[str] | None = None,
    *sources: str,
) -> list[str]:
    """Combine explicit list with auto-split sources; de-dupe."""
    items: list[str] = []
    seen: set[str] = set()

    def push_one(v: str) -> None:
        cleaned = _clean_piece(v)
        if not cleaned:
            # try splitting multi-answer string
            for p in split_acceptables(v):
                n = normalize(p)
                if n and n not in seen:
                    seen.add(n)
                    items.append(p)
            return
        n = normalize(cleaned)
        if n not in seen:
            seen.add(n)
            items.append(cleaned)

    if explicit:
        if isinstance(explicit, str):
            for p in split_acceptables(explicit):
                push_one(p)
        else:
            for p in explicit:
                push_one(str(p))

    for p in split_acceptables(*sources):
        push_one(p)

    return items


def grade_answer(
    user: str,
    accepted: Iterable[str],
    *,
    full_gloss: str = "",
) -> dict:
    """
    Grade student input against multiple acceptable answers.

    Any single hit counts as correct. Example for 約:
      窮困 / 貧困 / 困苦  -> any one is exact.
    """
    accepted_list = merge_acceptables(
        list(accepted) if accepted else None,
        full_gloss,
    )

    u = normalize(user)
    if not u:
        return {"status": "empty", "matched": None, "accepted": accepted_list}

    if not accepted_list:
        return {"status": "partial", "matched": None, "accepted": []}

    # exact match
    for a in accepted_list:
        if u == normalize(a):
            return {"status": "exact", "matched": a, "accepted": accepted_list}

    # containment for multi-char answers (user wrote slightly longer/shorter)
    for a in accepted_list:
        an = normalize(a)
        if len(an) >= 2 and len(u) >= 2:
            if an in u or u in an:
                return {"status": "exact", "matched": a, "accepted": accepted_list}

    # soft partial against full gloss blob only
    gloss_n = normalize(full_gloss)
    if gloss_n and len(u) >= 2 and u in gloss_n:
        return {"status": "partial", "matched": None, "accepted": accepted_list}

    return {"status": "miss", "matched": None, "accepted": accepted_list}


def format_accepted_display(accepted: list[str]) -> str:
    if not accepted:
        return "（未設定可接受答案）"
    return "　或　".join(accepted)
