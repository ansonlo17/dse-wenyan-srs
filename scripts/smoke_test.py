#!/usr/bin/env python3
"""Offline smoke test (no Streamlit server)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.align import auto_align_text  # noqa: E402
from src.parsers import parse_text_string  # noqa: E402
from src.sm2 import review, state_from_card  # noqa: E402
from src.suggest import suggest_for_paragraph  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    # point db at temp
    db.DB_PATH = tmp
    db.init_db(tmp)

    o = (ROOT / "samples" / "魚我所欲也_原文.txt").read_text(encoding="utf-8")
    t = (ROOT / "samples" / "魚我所欲也_語譯.txt").read_text(encoding="utf-8")
    op = parse_text_string(o)
    tp = parse_text_string(t)
    assert op, "original paragraphs empty"
    assert tp, "translation paragraphs empty"
    db.import_paragraphs("02_yuwosuo", "original", op, "o.txt", "h1")
    db.import_paragraphs("02_yuwosuo", "translation", tp, "t.txt", "h2")
    n = auto_align_text("02_yuwosuo")
    assert n > 0, "align failed"

    sugs = suggest_for_paragraph(op[0], tp[0], text_id="02_yuwosuo", top_n=10)
    terms = {s["term"] for s in sugs}
    assert "所欲" in terms or "甚" in terms or "舍" in terms or "之" in terms, terms

    vid = db.add_vocab(
        term="舍",
        text_id="02_yuwosuo",
        sentence_snippet=op[0][:40],
        translation_gloss="放棄",
        dse_usage="捨棄",
        difficulty=3,
        category="實詞",
        status="learning",
    )
    card = db.get_vocab(vid)
    assert card is not None
    st = review(state_from_card(card), 2)
    assert st.interval_days >= 1
    assert st.due_at

    status = db.text_status("02_yuwosuo")
    assert status["has_original"] and status["has_translation"]
    assert status["vocab_count"] >= 1

    print("SMOKE OK")
    print(f"  paragraphs original={len(op)} translation={len(tp)} align={n}")
    print(f"  suggestions sample={[s['term'] for s in sugs[:8]]}")
    print(f"  sm2 after good: interval={st.interval_days} ef={st.ease_factor}")


if __name__ == "__main__":
    main()
