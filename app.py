"""
HKDSE 文言文精華複習 — Streamlit app
指定文言經典（12 篇）字詞對照 × 半自動難詞 × SM-2 間隔重複
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import analytics, db  # noqa: E402
from src.align import auto_align_text  # noqa: E402
from src.parsers import parse_pdf_bytes, parse_text_bytes, parse_text_string  # noqa: E402
from src.sm2 import RATING_LABELS, apply_review_to_db, touch_streak  # noqa: E402
from src.suggest import (  # noqa: E402
    gloss_from_translation,
    highlight_html,
    suggest_for_paragraph,
)

st.set_page_config(
    page_title="文言精華 · DSE",
    page_icon="📜",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_css() -> None:
    css_path = ROOT / "assets" / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def bootstrap() -> None:
    db.init_db()
    inject_css()
    if "page" not in st.session_state:
        st.session_state.page = "首頁"
    if "review_queue" not in st.session_state:
        st.session_state.review_queue = []
    if "review_idx" not in st.session_state:
        st.session_state.review_idx = 0
    if "show_answer" not in st.session_state:
        st.session_state.show_answer = False
    if "session_stats" not in st.session_state:
        st.session_state.session_stats = {"done": 0, "again": 0, "good": 0}


def nav() -> str:
    pages = ["首頁", "文庫", "閱讀", "難字審核", "複習", "統計", "設定"]
    with st.sidebar:
        st.markdown("### 📜 文言精華")
        st.caption("HKDSE 指定文言 · 字詞複習")
        choice = st.radio(
            "導覽",
            pages,
            index=pages.index(st.session_state.page)
            if st.session_state.page in pages
            else 0,
            label_visibility="collapsed",
        )
        st.session_state.page = choice
        due = db.count_due()
        if due:
            st.info(f"今日到期 **{due}** 張")
    # mobile top select
    choice2 = st.selectbox(
        "頁面",
        pages,
        index=pages.index(st.session_state.page),
        label_visibility="collapsed",
        key="top_nav",
    )
    st.session_state.page = choice2
    return choice2


def file_to_paragraphs(uploaded, pasted: str) -> tuple[list[str], str | None]:
    if pasted and pasted.strip():
        return parse_text_string(pasted), None
    if uploaded is None:
        return [], "請上傳檔案或貼上文字。"
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".pdf"):
        return parse_pdf_bytes(data)
    if name.endswith((".txt", ".md", ".text")):
        return parse_text_bytes(data, uploaded.name), None
    # try text first, then pdf
    try:
        paras = parse_text_bytes(data, uploaded.name)
        if paras:
            return paras, None
    except Exception:  # noqa: BLE001
        pass
    return parse_pdf_bytes(data)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# ── Pages ──────────────────────────────────────────────────────────


def page_home() -> None:
    s = analytics.home_summary()
    st.markdown(
        f"""
        <div class="wy-hero">
          <h1>今日文言</h1>
          <p>十二篇字詞 · 對照閱讀 · 聰明複習</p>
        </div>
        <div class="wy-stat-row">
          <div class="wy-stat"><div class="num">{s['due']}</div><div class="label">到期</div></div>
          <div class="wy-stat"><div class="num">{s['streak']}</div><div class="label">連續天</div></div>
          <div class="wy-stat"><div class="num">{s['overall_pct']}%</div><div class="label">總掌握</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="wy-tip">{s["tip"]}</div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ 開始複習", type="primary", use_container_width=True):
            st.session_state.page = "複習"
            st.session_state.review_queue = []
            st.rerun()
    with c2:
        if st.button("📚 打開文庫", use_container_width=True):
            st.session_state.page = "文庫"
            st.rerun()

    st.markdown("#### 本週進度")
    st.caption(f"本週已複習 {s['week_reviews']} 次 · 字庫 {s['total_vocab']} 詞 · 已掌握 {s['total_mastered']}")

    st.markdown("#### 十二篇")
    for p in s["progress"]:
        flags = []
        if p["has_original"]:
            flags.append("原文")
        if p["has_translation"]:
            flags.append("語譯")
        flag_txt = " · ".join(flags) if flags else "尚未上傳"
        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-progress-label">
                <span><strong>{p['title']}</strong></span>
                <span>{p['mastery_pct']}%</span>
              </div>
              <div class="wy-muted">{flag_txt} · {p['vocab_count']} 詞</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.progress(min(100, int(p["mastery_pct"])) / 100.0)


def page_library() -> None:
    st.markdown("### 📚 文庫")
    st.caption("上傳教育局原文／你的語譯檔（PDF 或 TXT）。內容來源標示為使用者上傳。")

    texts = db.list_texts()
    labels = {f"{t['order_index']:02d} · {t['title']}": t["id"] for t in texts}
    selected_label = st.selectbox("選擇篇章", list(labels.keys()))
    text_id = labels[selected_label]
    text = db.get_text(text_id)
    status = db.text_status(text_id)

    st.markdown(
        f"""
        <div class="wy-card">
          <div class="wy-card-title">{text.get('genre') or ''} · {text.get('author') or ''}</div>
          <div style="font-size:1.15rem;font-weight:700;">{text['title']}</div>
          <div class="wy-muted" style="margin-top:0.4rem;">
            原文段落 {status['original_paras']} · 語譯段落 {status['translation_paras']} ·
            字詞 {status['vocab_count']} · 掌握 {status['mastery_pct']}%
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("##### 匯入原文")
    orig_file = st.file_uploader("原文檔（PDF / TXT）", type=["pdf", "txt", "md"], key="orig_up")
    orig_paste = st.text_area("或貼上原文", height=120, key="orig_paste")
    if st.button("解析並匯入原文", use_container_width=True):
        paras, warn = file_to_paragraphs(orig_file, orig_paste)
        if warn and not paras:
            st.error(warn)
        elif not paras:
            st.warning("沒有解析到段落。")
        else:
            fname = orig_file.name if orig_file else "pasted_original.txt"
            fhash = hash_bytes(orig_file.getvalue()) if orig_file else hash_bytes(orig_paste.encode())
            if orig_file:
                dest = db.UPLOADS_DIR / f"{text_id}_original_{fname}"
                dest.write_bytes(orig_file.getvalue())
            n = db.import_paragraphs(text_id, "original", paras, fname, fhash)
            auto_align_text(text_id)
            st.success(f"已匯入原文 {n} 段。" + (f" 提示：{warn}" if warn else ""))
            st.rerun()

    st.markdown("##### 匯入語譯")
    st.caption("語譯來源：使用者上傳（非程式內建）")
    trans_file = st.file_uploader("語譯檔（PDF / TXT）", type=["pdf", "txt", "md"], key="trans_up")
    trans_paste = st.text_area("或貼上語譯", height=120, key="trans_paste")
    if st.button("解析並匯入語譯", use_container_width=True):
        paras, warn = file_to_paragraphs(trans_file, trans_paste)
        if warn and not paras:
            st.error(warn)
        elif not paras:
            st.warning("沒有解析到段落。")
        else:
            fname = trans_file.name if trans_file else "pasted_translation.txt"
            fhash = hash_bytes(trans_file.getvalue()) if trans_file else hash_bytes(trans_paste.encode())
            if trans_file:
                dest = db.UPLOADS_DIR / f"{text_id}_translation_{fname}"
                dest.write_bytes(trans_file.getvalue())
            n = db.import_paragraphs(text_id, "translation", paras, fname, fhash)
            auto_align_text(text_id)
            st.success(f"已匯入語譯 {n} 段。" + (f" 提示：{warn}" if warn else ""))
            st.rerun()

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("重新對齊", use_container_width=True):
            n = auto_align_text(text_id)
            st.success(f"已對齊 {n} 組")
    with c2:
        if st.button("去閱讀", type="primary", use_container_width=True):
            st.session_state.reader_text_id = text_id
            st.session_state.page = "閱讀"
            st.rerun()
    with c3:
        if st.button("難字審核", use_container_width=True):
            st.session_state.reader_text_id = text_id
            st.session_state.page = "難字審核"
            st.rerun()

    with st.expander("載入示範：《魚我所欲也》"):
        if st.button("一鍵載入 samples", use_container_width=True):
            o = (ROOT / "samples" / "魚我所欲也_原文.txt").read_text(encoding="utf-8")
            t = (ROOT / "samples" / "魚我所欲也_語譯.txt").read_text(encoding="utf-8")
            db.import_paragraphs(
                "02_yuwosuo",
                "original",
                parse_text_string(o),
                "魚我所欲也_原文.txt",
                hash_bytes(o.encode()),
            )
            db.import_paragraphs(
                "02_yuwosuo",
                "translation",
                parse_text_string(t),
                "魚我所欲也_語譯.txt",
                hash_bytes(t.encode()),
            )
            auto_align_text("02_yuwosuo")
            st.success("已載入《魚我所欲也》原文＋語譯示範。")
            st.rerun()


def page_reader() -> None:
    st.markdown("### 📖 對照閱讀")
    texts = db.list_texts()
    labels = {f"{t['order_index']:02d} · {t['title']}": t["id"] for t in texts}
    default_id = st.session_state.get("reader_text_id") or texts[0]["id"]
    keys = list(labels.keys())
    default_label = next((k for k, v in labels.items() if v == default_id), keys[0])
    selected_label = st.selectbox("篇章", keys, index=keys.index(default_label))
    text_id = labels[selected_label]
    st.session_state.reader_text_id = text_id

    status = db.text_status(text_id)
    st.progress(min(100, int(status["mastery_pct"])) / 100.0)
    st.caption(f"掌握 {status['mastery_pct']}% · {status['vocab_count']} 詞")

    pairs = db.get_aligned_pairs(text_id)
    if not pairs:
        st.markdown(
            '<div class="wy-empty"><div class="emoji">📭</div>尚未上傳原文或語譯<br/>請到「文庫」匯入</div>',
            unsafe_allow_html=True,
        )
        return

    levels = db.known_terms_for_text(text_id)

    for i, pair in enumerate(pairs):
        orig = pair.get("original")
        trans = pair.get("translation")
        orig_text = orig["content"] if orig else "（無原文）"
        trans_text = trans["content"] if trans else "（無對應語譯）"
        conf = pair.get("confidence") or 0
        hl = highlight_html(orig_text, levels) if orig else orig_text

        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-card-title">第 {i+1} 段 · 對齊 {conf:.0%}</div>
              <div class="wy-original">{hl}</div>
              <div class="wy-translation"><span class="wy-chip">語譯</span><br/>{_esc(trans_text)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        suggestions = (
            suggest_for_paragraph(
                orig_text,
                trans_text if trans else None,
                text_id=text_id,
                top_n=6,
            )
            if orig
            else []
        )
        if suggestions:
            st.caption("建議難詞（點下方加入）")
            cols = st.columns(min(3, len(suggestions)))
            for j, sug in enumerate(suggestions):
                with cols[j % len(cols)]:
                    if st.button(
                        sug["term"],
                        key=f"sug_{text_id}_{i}_{sug['term']}",
                        use_container_width=True,
                    ):
                        db.add_vocab(
                            term=sug["term"],
                            text_id=text_id,
                            paragraph_id=orig["id"] if orig else None,
                            sentence_snippet=orig_text[:80],
                            translation_gloss=gloss_from_translation(
                                trans_text if trans else None
                            ),
                            dse_usage=sug.get("dse_usage", ""),
                            difficulty=sug.get("difficulty", 3),
                            category=sug.get("category", "實詞"),
                            status="learning",
                        )
                        st.toast(f"已加入：{sug['term']}")
                        st.rerun()

        with st.expander(f"手動加詞 · 第 {i+1} 段"):
            term = st.text_input("字／詞", key=f"manual_term_{i}")
            gloss = st.text_input(
                "白話解釋",
                value=gloss_from_translation(trans_text if trans else None),
                key=f"manual_gloss_{i}",
            )
            usage = st.text_input("DSE 常見用法（可留空）", key=f"manual_usage_{i}")
            diff = st.slider("難度", 1, 5, 3, key=f"manual_diff_{i}")
            cat = st.selectbox(
                "類型",
                ["虛詞", "異義", "實詞", "活用", "結構"],
                key=f"manual_cat_{i}",
            )
            if st.button("加入複習", key=f"manual_add_{i}"):
                if term.strip():
                    db.add_vocab(
                        term=term.strip(),
                        text_id=text_id,
                        paragraph_id=orig["id"] if orig else None,
                        sentence_snippet=orig_text[:80],
                        translation_gloss=gloss,
                        dse_usage=usage,
                        difficulty=diff,
                        category=cat,
                        status="learning",
                    )
                    st.success(f"已加入 {term.strip()}")
                    st.rerun()
                else:
                    st.warning("請輸入字詞")


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def page_review_queue() -> None:
    st.markdown("### ✍️ 難字審核")
    st.caption("半自動：系統建議 → 你確認加入或略過")

    texts = db.list_texts()
    labels = {f"{t['order_index']:02d} · {t['title']}": t["id"] for t in texts}
    default_id = st.session_state.get("reader_text_id") or texts[0]["id"]
    keys = list(labels.keys())
    default_label = next((k for k, v in labels.items() if v == default_id), keys[0])
    selected_label = st.selectbox("篇章", keys, index=keys.index(default_label), key="queue_text")
    text_id = labels[selected_label]
    st.session_state.reader_text_id = text_id

    pairs = db.get_aligned_pairs(text_id)
    if not pairs:
        st.info("請先在文庫上傳原文。")
        return

    # build full suggestion list
    all_sugs: list[dict] = []
    for pair in pairs:
        orig = pair.get("original")
        if not orig:
            continue
        trans = pair.get("translation")
        for sug in suggest_for_paragraph(
            orig["content"],
            trans["content"] if trans else None,
            text_id=text_id,
            top_n=8,
        ):
            all_sugs.append(
                {
                    **sug,
                    "paragraph_id": orig["id"],
                    "snippet": orig["content"][:80],
                    "gloss": gloss_from_translation(
                        trans["content"] if trans else None
                    ),
                }
            )

    # de-dupe terms
    seen = set()
    unique = []
    for s in all_sugs:
        if s["term"] in seen:
            continue
        # skip already learning/mastered
        seen.add(s["term"])
        unique.append(s)

    existing = {
        v["term"]
        for v in db.list_vocab(text_id=text_id, statuses=("learning", "mastered", "ignored"))
    }
    unique = [u for u in unique if u["term"] not in existing]

    st.markdown(f"待確認建議：**{len(unique)}**")

    if st.button("一鍵加入全部建議", use_container_width=True):
        for sug in unique:
            db.add_vocab(
                term=sug["term"],
                text_id=text_id,
                paragraph_id=sug["paragraph_id"],
                sentence_snippet=sug["snippet"],
                translation_gloss=sug["gloss"],
                dse_usage=sug.get("dse_usage", ""),
                difficulty=sug.get("difficulty", 3),
                category=sug.get("category", "實詞"),
                status="learning",
            )
        st.success(f"已加入 {len(unique)} 詞")
        st.rerun()

    for idx, sug in enumerate(unique[:40]):
        st.markdown(
            f"""
            <div class="wy-card">
              <div style="font-size:1.25rem;font-weight:700;color:#2f5d50;">{sug['term']}</div>
              <div class="wy-muted">出處：{_esc(sug['snippet'])}</div>
              <div style="margin-top:0.35rem;">
                <span class="wy-chip">{sug.get('category','')}</span>
                <span class="wy-chip wy-chip-accent">難度 {sug.get('difficulty',3)}</span>
              </div>
              <div class="wy-muted" style="margin-top:0.4rem;">{_esc(sug.get('dse_usage') or '')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("加入", key=f"q_add_{idx}", type="primary", use_container_width=True):
                db.add_vocab(
                    term=sug["term"],
                    text_id=text_id,
                    paragraph_id=sug["paragraph_id"],
                    sentence_snippet=sug["snippet"],
                    translation_gloss=sug["gloss"],
                    dse_usage=sug.get("dse_usage", ""),
                    difficulty=sug.get("difficulty", 3),
                    category=sug.get("category", "實詞"),
                    status="learning",
                )
                st.rerun()
        with c2:
            if st.button("略過", key=f"q_skip_{idx}", use_container_width=True):
                db.ignore_vocab_term(sug["term"], text_id, sug["snippet"])
                st.rerun()
        with c3:
            with st.popover("編輯"):
                g = st.text_input("解釋", value=sug.get("gloss") or "", key=f"q_g_{idx}")
                u = st.text_input("用法", value=sug.get("dse_usage") or "", key=f"q_u_{idx}")
                d = st.slider("難度", 1, 5, int(sug.get("difficulty") or 3), key=f"q_d_{idx}")
                if st.button("儲存並加入", key=f"q_save_{idx}"):
                    db.add_vocab(
                        term=sug["term"],
                        text_id=text_id,
                        paragraph_id=sug["paragraph_id"],
                        sentence_snippet=sug["snippet"],
                        translation_gloss=g,
                        dse_usage=u,
                        difficulty=d,
                        category=sug.get("category", "實詞"),
                        status="learning",
                    )
                    st.rerun()

    st.markdown("---")
    st.markdown("##### 手動新增")
    t = st.text_input("字詞", key="queue_manual_term")
    g = st.text_input("白話解釋", key="queue_manual_gloss")
    u = st.text_input("DSE 用法", key="queue_manual_usage")
    if st.button("新增到字庫", use_container_width=True):
        if t.strip():
            db.add_vocab(
                term=t.strip(),
                text_id=text_id,
                translation_gloss=g,
                dse_usage=u,
                status="learning",
            )
            st.success("已新增")
            st.rerun()


def _build_review_queue() -> list[dict]:
    daily = int(db.get_setting("daily_limit", "50") or 50)
    new_n = int(db.get_setting("new_cards_per_day", "10") or 10)
    due = db.due_cards(limit=daily)
    have_ids = {c["id"] for c in due}
    # fill with new if room
    remaining = max(0, daily - len(due))
    news = []
    if remaining and new_n:
        for c in db.new_cards(limit=new_n):
            if c["id"] not in have_ids:
                news.append(c)
            if len(news) >= min(new_n, remaining):
                break
        # ensure srs rows
        for c in news:
            db.add_vocab(
                term=c["term"],
                text_id=c["text_id"],
                paragraph_id=c.get("paragraph_id"),
                sentence_snippet=c.get("sentence_snippet") or "",
                translation_gloss=c.get("translation_gloss") or "",
                dse_usage=c.get("dse_usage") or "",
                difficulty=c.get("difficulty") or 3,
                category=c.get("category") or "實詞",
                status="learning",
            )
    return due + news


def page_review() -> None:
    st.markdown("### 🔁 今日複習")

    if st.button("重新整理佇列", use_container_width=True):
        st.session_state.review_queue = _build_review_queue()
        st.session_state.review_idx = 0
        st.session_state.show_answer = False
        st.session_state.session_stats = {"done": 0, "again": 0, "good": 0}
        st.rerun()

    if not st.session_state.review_queue:
        st.session_state.review_queue = _build_review_queue()
        st.session_state.review_idx = 0
        st.session_state.show_answer = False

    queue = st.session_state.review_queue
    idx = st.session_state.review_idx

    if not queue:
        st.markdown(
            '<div class="wy-empty"><div class="emoji">☕</div>暫時沒有要複習的卡片<br/>去閱讀頁加幾個字詞吧</div>',
            unsafe_allow_html=True,
        )
        return

    if idx >= len(queue):
        stats = st.session_state.session_stats
        st.success("本輪完成！")
        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-stat-row">
                <div class="wy-stat"><div class="num">{stats['done']}</div><div class="label">完成</div></div>
                <div class="wy-stat"><div class="num">{stats['good']}</div><div class="label">尚可+</div></div>
                <div class="wy-stat"><div class="num">{stats['again']}</div><div class="label">再來</div></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("再來一輪", type="primary", use_container_width=True):
            st.session_state.review_queue = _build_review_queue()
            st.session_state.review_idx = 0
            st.session_state.show_answer = False
            st.session_state.session_stats = {"done": 0, "again": 0, "good": 0}
            st.rerun()
        return

    # refresh card from db
    card_meta = queue[idx]
    card = db.get_vocab(card_meta["id"]) or card_meta
    total = len(queue)
    st.caption(f"{idx + 1} / {total}")
    st.progress((idx) / max(1, total))

    stars = "★" * int(card.get("difficulty") or 3) + "☆" * (5 - int(card.get("difficulty") or 3))
    st.markdown(
        f"""
        <div class="wy-card">
          <div class="wy-flash-meta">{_esc(card.get('text_title') or '')} · {stars}</div>
          <div class="wy-flash-term">{_esc(card['term'])}</div>
          <div class="wy-muted" style="text-align:center;">{_esc(card.get('sentence_snippet') or '')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not st.session_state.show_answer:
        if st.button("顯示解釋", type="primary", use_container_width=True):
            st.session_state.show_answer = True
            st.rerun()
    else:
        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-card-title">白話解釋</div>
              <div class="wy-original" style="font-size:1.05rem;">
                {_esc(card.get('translation_gloss') or '（尚未填寫解釋）')}
              </div>
              <div class="wy-translation">
                <span class="wy-chip">{_esc(card.get('category') or '')}</span>
                {_esc(card.get('dse_usage') or '')}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        cols = st.columns(4)
        for rating, col in zip(range(4), cols):
            with col:
                if st.button(RATING_LABELS[rating], key=f"rate_{rating}", use_container_width=True):
                    apply_review_to_db(card["id"], card, rating)
                    touch_streak()
                    st.session_state.session_stats["done"] += 1
                    if rating == 0:
                        st.session_state.session_stats["again"] += 1
                        # re-queue again cards at end
                        st.session_state.review_queue.append(card)
                    else:
                        st.session_state.session_stats["good"] += 1
                    st.session_state.review_idx += 1
                    st.session_state.show_answer = False
                    st.rerun()

        if st.button("標記已掌握", use_container_width=True):
            db.update_vocab_status(card["id"], "mastered")
            st.session_state.review_idx += 1
            st.session_state.show_answer = False
            st.session_state.session_stats["done"] += 1
            st.session_state.session_stats["good"] += 1
            touch_streak()
            st.rerun()


def page_stats() -> None:
    st.markdown("### 📊 統計與弱點")
    report = analytics.weakness_report()
    summary = analytics.home_summary()

    c1, c2, c3 = st.columns(3)
    c1.metric("字庫", summary["total_vocab"])
    c2.metric("已掌握", summary["total_mastered"])
    c3.metric("總掌握率", f"{summary['overall_pct']}%")

    if report["by_text"]:
        st.markdown("#### 各篇掌握")
        import pandas as pd

        df = pd.DataFrame(report["by_text"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.bar_chart(df.set_index("篇名")["掌握%"])

    if report["by_category"]:
        st.markdown("#### 類型分佈")
        import pandas as pd

        df2 = pd.DataFrame(report["by_category"])
        st.bar_chart(df2.set_index("類型")["數量"])

    st.markdown("#### 高風險字詞")
    if not report["risky"]:
        st.caption("暫無高 lapse 詞，繼續保持！")
    else:
        for r in report["risky"]:
            st.markdown(
                f"""
                <div class="wy-card">
                  <strong>{_esc(r['term'])}</strong>
                  <span class="wy-chip">{_esc(r['category'])}</span>
                  <div class="wy-muted">《{_esc(r['title'])}》 · 遺忘 {r['lapses']} 次 · 間隔 {r['interval_days']:.1f} 日</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def page_settings() -> None:
    st.markdown("### ⚙️ 設定")

    daily = st.number_input(
        "每日複習上限",
        min_value=5,
        max_value=200,
        value=int(db.get_setting("daily_limit", "50") or 50),
    )
    new_n = st.number_input(
        "每日新卡上限",
        min_value=0,
        max_value=50,
        value=int(db.get_setting("new_cards_per_day", "10") or 10),
    )
    if st.button("儲存設定", type="primary", use_container_width=True):
        db.set_setting("daily_limit", str(int(daily)))
        db.set_setting("new_cards_per_day", str(int(new_n)))
        st.success("已儲存")

    st.markdown("#### 重置某篇進度")
    texts = db.list_texts()
    labels = {f"{t['order_index']:02d} · {t['title']}": t["id"] for t in texts}
    lab = st.selectbox("選擇篇章", list(labels.keys()), key="reset_sel")
    if st.button("清除該篇字詞與 SRS", use_container_width=True):
        db.reset_text_progress(labels[lab])
        st.warning("已清除")

    st.markdown("#### 備份")
    if st.button("匯出 JSON 備份", use_container_width=True):
        data = db.export_backup()
        st.download_button(
            "下載 backup.json",
            data=json.dumps(data, ensure_ascii=False, indent=2),
            file_name="dse_wenyan_backup.json",
            mime="application/json",
            use_container_width=True,
        )

    up = st.file_uploader("匯入 JSON 備份", type=["json"])
    if up and st.button("確認匯入（會覆寫字詞與進度）", use_container_width=True):
        data = json.loads(up.getvalue().decode("utf-8"))
        db.import_backup(data)
        st.success("匯入完成")

    st.markdown("#### 資源連結")
    st.markdown(
        "- [教育局：指定文言經典學習材料](https://www.edb.gov.hk/tc/curriculum-development/kla/chi-edu/nss-lang/settext-index.html)\n"
        "- [十二篇原文 PDF](https://www.edb.gov.hk/attachment/tc/curriculum-development/kla/chi-edu/nss-lang/Set_text_12.pdf)"
    )


def main() -> None:
    bootstrap()
    page = nav()
    if page == "首頁":
        page_home()
    elif page == "文庫":
        page_library()
    elif page == "閱讀":
        page_reader()
    elif page == "難字審核":
        page_review_queue()
    elif page == "複習":
        page_review()
    elif page == "統計":
        page_stats()
    elif page == "設定":
        page_settings()


if __name__ == "__main__":
    main()
