"""Progress and weakness analytics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db


def home_summary() -> dict[str, Any]:
    due = db.count_due()
    new_n = len(db.new_cards(limit=100))
    texts = db.list_texts()
    progress = []
    total_v = total_m = 0
    for t in texts:
        st = db.text_status(t["id"])
        total_v += st["vocab_count"]
        total_m += st["mastered"]
        progress.append(
            {
                "id": t["id"],
                "title": t["title"],
                "mastery_pct": st["mastery_pct"],
                "vocab_count": st["vocab_count"],
                "has_original": st["has_original"],
                "has_translation": st["has_translation"],
            }
        )

    today = datetime.now(timezone.utc).date().isoformat()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    today_reviews = db.review_count_since(today)
    week_reviews = db.review_count_since(week_ago)
    streak = int(db.get_setting("streak_count", "0") or 0)

    tip = _tip(due, progress)

    return {
        "due": due,
        "new_available": new_n,
        "today_reviews": today_reviews,
        "week_reviews": week_reviews,
        "streak": streak,
        "total_vocab": total_v,
        "total_mastered": total_m,
        "overall_pct": round(100 * total_m / total_v, 1) if total_v else 0.0,
        "progress": progress,
        "tip": tip,
    }


def _tip(due: int, progress: list[dict[str, Any]]) -> str:
    if due > 0:
        mins = max(3, min(25, due))
        return f"今日有 {due} 張到期 · 大約 {mins} 分鐘可以清完。"
    lagging = sorted(
        [p for p in progress if p["vocab_count"] > 0],
        key=lambda x: x["mastery_pct"],
    )
    if lagging and lagging[0]["mastery_pct"] < 50:
        return f"《{lagging[0]['title']}》掌握度較低，可到閱讀頁多加幾個字眼。"
    no_content = [p for p in progress if not p["has_original"]]
    if no_content:
        return "先到文庫上傳一篇原文＋語譯，就可以開始對照與挑難詞。"
    return "沒有到期卡片。可以閱讀範文，或手動加幾個新詞。"


def weakness_report() -> dict[str, Any]:
    vocab = db.list_vocab(statuses=("learning", "mastered"))
    by_text: dict[str, Counter] = defaultdict(Counter)
    by_cat: Counter = Counter()
    risky: list[dict[str, Any]] = []

    for v in vocab:
        title = v.get("text_title") or v["text_id"]
        by_text[title]["total"] += 1
        if v["status"] == "mastered":
            by_text[title]["mastered"] += 1
        else:
            by_text[title]["learning"] += 1
        cat = v.get("category") or "其他"
        by_cat[cat] += 1
        lapses = int(v.get("lapses") or 0)
        interval = float(v.get("interval_days") or 0)
        if v["status"] == "learning" and (lapses >= 2 or interval < 1):
            risky.append(
                {
                    "term": v["term"],
                    "title": title,
                    "lapses": lapses,
                    "interval_days": interval,
                    "category": cat,
                }
            )

    risky.sort(key=lambda x: (-x["lapses"], x["interval_days"]))
    text_rows = []
    for title, c in by_text.items():
        total = c["total"]
        mastered = c["mastered"]
        text_rows.append(
            {
                "篇名": title,
                "字詞數": total,
                "已掌握": mastered,
                "學習中": c["learning"],
                "掌握%": round(100 * mastered / total, 1) if total else 0,
            }
        )
    text_rows.sort(key=lambda x: x["掌握%"])

    cat_rows = [{"類型": k, "數量": v} for k, v in by_cat.most_common()]

    return {
        "by_text": text_rows,
        "by_category": cat_rows,
        "risky": risky[:15],
    }
