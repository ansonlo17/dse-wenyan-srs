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
from src.sm2 import apply_review_to_db, review, state_from_card  # noqa: E402
from src.suggest import suggest_for_paragraph  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db.DB_PATH = tmp
    db.init_db(tmp)

    sample_o = ROOT / "samples" / "02_魚我所欲也_原文.txt"
    sample_t = ROOT / "samples" / "02_魚我所欲也_語譯.txt"
    if not sample_o.exists():
        # fallback older names
        sample_o = ROOT / "samples" / "魚我所欲也_原文.txt"
        sample_t = ROOT / "samples" / "魚我所欲也_語譯.txt"
    o = sample_o.read_text(encoding="utf-8")
    t = sample_t.read_text(encoding="utf-8")
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

    uid_a, err, pin_a = db.create_user("同學A", "1234")
    assert not err and uid_a and pin_a == "1234"
    uid_b, err, pin_b = db.create_user("同學B", None)
    assert not err and uid_b and pin_b and len(pin_b) == 4
    batch = db.create_users_batch(count=3, name_prefix="測")
    assert len(batch) == 3

    vid = db.add_vocab(
        term="舍",
        text_id="02_yuwosuo",
        sentence_snippet=op[0][:40],
        translation_gloss="放棄",
        dse_usage="捨棄",
        difficulty=3,
        category="實詞",
        status="learning",
        user_id=uid_a,
    )
    db.ensure_user_deck(uid_a)
    db.ensure_user_deck(uid_b)

    card_a = db.get_vocab(vid, user_id=uid_a)
    assert card_a is not None
    st = review(state_from_card(card_a), 2)
    assert st.interval_days >= 1
    assert st.due_at

    apply_review_to_db(vid, card_a, 2, user_id=uid_a)
    # A progressed; B still fresh
    due_a = db.count_due(user_id=uid_a)
    due_b = db.count_due(user_id=uid_b)
    assert due_b >= 1 or db.count_vocab(user_id=uid_b) >= 1
    # pin check
    assert db.verify_user_pin(uid_a, "1234")
    assert not db.verify_user_pin(uid_a, "0000")
    assert db.verify_user_pin(uid_b, pin_b)
    assert not db.verify_user_pin(uid_b, "0000")

    status = db.text_status("02_yuwosuo", user_id=uid_a)
    assert status["has_original"] and status["has_translation"]
    assert status["vocab_count"] >= 1

    print("SMOKE OK (multi-user)")
    print(f"  paragraphs original={len(op)} translation={len(tp)} align={n}")
    print(f"  users A={uid_a} B={uid_b} due_a={due_a} due_b={due_b}")
    print(f"  sm2 after good: interval={st.interval_days} ef={st.ease_factor}")


if __name__ == "__main__":
    main()
