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
from src.sm2 import (  # noqa: E402
    RATING_BUTTON_ORDER,
    RATING_LABELS,
    apply_review_to_db,
    touch_streak,
)
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


PAGES = ["首頁", "文庫", "閱讀", "難字審核", "複習", "統計", "設定"]


def go_page(name: str) -> None:
    """Navigate without fighting widget state."""
    if name not in PAGES:
        name = "首頁"
    st.session_state.page = name
    # Keep top selectbox (if keyed) in sync
    st.session_state["nav_select"] = name


def bootstrap() -> None:
    db.init_db()
    inject_css()
    # Auto-load all prepared chapters — user never needs to upload
    _ensure_content_seeded()
    if "page" not in st.session_state or st.session_state.page not in PAGES:
        st.session_state.page = "首頁"
    if "review_queue" not in st.session_state:
        st.session_state.review_queue = []
    if "review_idx" not in st.session_state:
        st.session_state.review_idx = 0
    if "show_answer" not in st.session_state:
        st.session_state.show_answer = False
    if "user_answer" not in st.session_state:
        st.session_state.user_answer = ""
    if "answer_feedback" not in st.session_state:
        st.session_state.answer_feedback = None  # None | "exact" | "partial" | "miss" | "skip"
    if "session_stats" not in st.session_state:
        st.session_state.session_stats = {"done": 0, "again": 0, "good": 0, "typed_ok": 0}
    # Which chapter the student is focusing on (None = 全部已載入篇章)
    if "study_text_id" not in st.session_state:
        st.session_state.study_text_id = None


def nav() -> str:
    """
    Single source of truth: st.session_state.page
    Only ONE selectbox controls navigation (avoids dual-widget overwrite bug
    that made buttons appear unclickable / immediately reset).
    """
    with st.sidebar:
        st.markdown("### 📜 文言精華")
        st.caption("HKDSE 指定文言 · 字詞複習")
        # Sidebar uses buttons so it never fights the top selectbox key
        for p in PAGES:
            is_here = st.session_state.page == p
            if st.button(
                f"{'● ' if is_here else '○ '}{p}",
                key=f"side_btn_{p}",
                use_container_width=True,
                type="primary" if is_here else "secondary",
            ):
                go_page(p)
                st.rerun()
        due = db.count_due(st.session_state.get("study_text_id"))
        if due:
            st.info(f"到期 **{due}** 張")
        focus = st.session_state.get("study_text_id")
        if focus:
            t = db.get_text(focus)
            if t:
                st.caption(f"目前溫習：{t['title']}")

    # Top mobile nav: no sticky key conflict — drive purely from session_state.page
    # (button navigation previously lost because a keyed selectbox overwrote page).
    choice = st.selectbox(
        "頁面",
        PAGES,
        index=PAGES.index(st.session_state.page),
        label_visibility="collapsed",
    )
    if choice != st.session_state.page:
        go_page(choice)
    return st.session_state.page


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
          <h1>文言精華</h1>
          <p>指定文言 · 字詞對照 · 間隔複習</p>
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

    # ── Choose which chapter to study ──
    st.markdown("#### 選擇要溫習的篇章")
    ready = [p for p in s["progress"] if p.get("has_original") and p.get("vocab_count", 0) > 0]
    if not ready:
        st.caption("尚無已載入篇章。")
    else:
        options = ["全部已載入篇章"] + [p["title"] for p in ready]
        id_by_title = {p["title"]: p["id"] for p in ready}
        current = st.session_state.study_text_id
        if current and any(p["id"] == current for p in ready):
            cur_title = next(p["title"] for p in ready if p["id"] == current)
            default_idx = options.index(cur_title)
        else:
            default_idx = 0
        choice = st.selectbox(
            "溫習範圍",
            options,
            index=default_idx,
            key="home_study_select",
            label_visibility="collapsed",
        )
        new_id = None if choice == "全部已載入篇章" else id_by_title[choice]
        if new_id != st.session_state.study_text_id:
            st.session_state.study_text_id = new_id
            st.session_state.review_queue = []
            st.session_state.review_idx = 0
            _reset_card_ui()

        focus_id = st.session_state.study_text_id
        due_n = db.count_due(focus_id)
        vocab_n = db.count_vocab(focus_id)
        scope = choice if choice == "全部已載入篇章" else f"《{choice}》"
        st.caption(f"目前範圍：{scope} · 字詞 {vocab_n} · 到期 {due_n}")

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("▶ 複習此篇", type="primary", use_container_width=True, key="home_review"):
                st.session_state.review_queue = []
                st.session_state.review_idx = 0
                _reset_card_ui()
                go_page("複習")
                st.rerun()
        with b2:
            if st.button("📖 閱讀", use_container_width=True, key="home_read"):
                if focus_id:
                    st.session_state.reader_text_id = focus_id
                elif ready:
                    st.session_state.reader_text_id = ready[0]["id"]
                go_page("閱讀")
                st.rerun()
        with b3:
            if st.button("📚 文庫", use_container_width=True, key="home_library"):
                go_page("文庫")
                st.rerun()

        st.markdown("#### 各篇進度")
        for p in ready:
            active = focus_id == p["id"] or (
                focus_id is None and choice == "全部已載入篇章"
            )
            mark = "● " if st.session_state.study_text_id == p["id"] else ""
            st.markdown(
                f"""
                <div class="wy-card">
                  <div class="wy-progress-label">
                    <span><strong>{mark}{p['title']}</strong></span>
                    <span>{p['mastery_pct']}%</span>
                  </div>
                  <div class="wy-muted">字詞 {p['vocab_count']} · 到期 {db.count_due(p['id'])}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.progress(min(100, int(p["mastery_pct"])) / 100.0)
            if st.button(
                f"溫習《{p['title']}》",
                key=f"pick_{p['id']}",
                use_container_width=True,
            ):
                st.session_state.study_text_id = p["id"]
                st.session_state.reader_text_id = p["id"]
                st.session_state.review_queue = []
                st.session_state.review_idx = 0
                _reset_card_ui()
                go_page("複習")
                st.rerun()


# Bundled sample packs: (text_id, display name, original path, translation path, vocab json path)
SAMPLE_PACKS = [
    (
        "01_lunyu",
        "論仁、論孝、論君子",
        "01_論仁論孝論君子_原文.txt",
        "01_論仁論孝論君子_語譯.txt",
        "01_論仁論孝論君子_難詞.json",
    ),
    (
        "02_yuwosuo",
        "魚我所欲也",
        "02_魚我所欲也_原文.txt",
        "02_魚我所欲也_語譯.txt",
        "02_魚我所欲也_難詞.json",
    ),
    (
        "03_xiaoyaoyou",
        "逍遙遊（節錄）",
        "03_逍遙遊_原文.txt",
        "03_逍遙遊_語譯.txt",
        "03_逍遙遊_難詞.json",
    ),
    (
        "04_quanxue",
        "勸學（節錄）",
        "04_勸學_原文.txt",
        "04_勸學_語譯.txt",
        "04_勸學_難詞.json",
    ),
]


def _load_sample_pack(text_id: str, orig_name: str, trans_name: str, vocab_name: str) -> str:
    """Load original + translation + vocab from samples/. Returns status message."""
    base = ROOT / "samples"
    op = base / orig_name
    tp = base / trans_name
    vp = base / vocab_name
    if not op.exists() or not tp.exists():
        return f"找不到示範檔：{orig_name}"
    o = op.read_text(encoding="utf-8")
    t = tp.read_text(encoding="utf-8")
    db.import_paragraphs(
        text_id, "original", parse_text_string(o), orig_name, hash_bytes(o.encode())
    )
    db.import_paragraphs(
        text_id, "translation", parse_text_string(t), trans_name, hash_bytes(t.encode())
    )
    auto_align_text(text_id)
    n_vocab = 0
    if vp.exists():
        items = json.loads(vp.read_text(encoding="utf-8"))
        for w in items:
            db.add_vocab(
                term=w["term"],
                text_id=text_id,
                sentence_snippet=w.get("sentence_snippet", ""),
                translation_gloss=w.get("dse_usage", ""),
                dse_usage=w.get("dse_usage", ""),
                accepted_answers="／".join(w.get("accepted") or []),
                difficulty=int(w.get("difficulty", 3)),
                category=w.get("category", "實詞"),
                status="learning",
            )
            n_vocab += 1
    return f"已載入原文＋語譯＋{n_vocab} 詞"


def _ensure_content_seeded() -> None:
    """
    Auto-import all prepared packs when a pack is missing content.
    Users do not upload — content ships with the app under samples/.
    """
    for text_id, _title, on, tn, vn in SAMPLE_PACKS:
        st_info = db.text_status(text_id)
        # (Re)load if no original yet, or original exists but no vocab cards
        if not st_info["has_original"] or st_info["vocab_count"] == 0:
            _load_sample_pack(text_id, on, tn, vn)


def page_library() -> None:
    st.markdown("### 📚 文庫")
    st.caption("篇章已由系統自動載入，無需自行上傳。點篇章即可閱讀／複習。")

    texts = db.list_texts()
    ready_ids = {p[0] for p in SAMPLE_PACKS}

    for t in texts:
        status = db.text_status(t["id"])
        ready = t["id"] in ready_ids and status["has_original"]
        flag = "已載入" if ready else "準備中"
        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-card-title">{t.get('genre') or ''} · {t.get('author') or ''} · {flag}</div>
              <div class="wy-term-title">{t['order_index']:02d} · {t['title']}</div>
              <div class="wy-muted" style="margin-top:0.4rem;">
                原文 {status['original_paras']} 段 · 語譯 {status['translation_paras']} 段 ·
                字詞 {status['vocab_count']} · 掌握 {status['mastery_pct']}%
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if ready:
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button(
                    "閱讀",
                    key=f"lib_read_{t['id']}",
                    use_container_width=True,
                    type="primary",
                ):
                    st.session_state.reader_text_id = t["id"]
                    st.session_state.study_text_id = t["id"]
                    go_page("閱讀")
                    st.rerun()
            with b2:
                if st.button(
                    "複習",
                    key=f"lib_rev_{t['id']}",
                    use_container_width=True,
                ):
                    st.session_state.study_text_id = t["id"]
                    st.session_state.review_queue = []
                    st.session_state.review_idx = 0
                    _reset_card_ui()
                    go_page("複習")
                    st.rerun()
            with b3:
                if st.button(
                    "難字",
                    key=f"lib_q_{t['id']}",
                    use_container_width=True,
                ):
                    st.session_state.reader_text_id = t["id"]
                    st.session_state.study_text_id = t["id"]
                    go_page("難字審核")
                    st.rerun()
        else:
            st.caption("此篇內容稍後會由系統加入。")


def page_reader() -> None:
    st.markdown("### 📖 對照閱讀")
    texts = db.list_texts()
    # Prefer chapters that already have content
    content_ids = [
        t["id"] for t in texts if db.text_status(t["id"])["has_original"]
    ]
    labels = {f"{t['order_index']:02d} · {t['title']}": t["id"] for t in texts}
    # Default: study focus → reader_text_id → first with content
    default_id = (
        st.session_state.get("reader_text_id")
        or st.session_state.get("study_text_id")
        or (content_ids[0] if content_ids else texts[0]["id"])
    )
    keys = list(labels.keys())
    default_label = next((k for k, v in labels.items() if v == default_id), keys[0])
    selected_label = st.selectbox(
        "選擇篇章",
        keys,
        index=keys.index(default_label),
        key="reader_chapter_select",
    )
    text_id = labels[selected_label]
    st.session_state.reader_text_id = text_id
    st.session_state.study_text_id = text_id  # keep study focus in sync

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
              <div class="wy-term-title">{sug['term']}</div>
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
    focus = st.session_state.get("study_text_id")
    due = db.due_cards(limit=daily, text_id=focus)
    have_ids = {c["id"] for c in due}
    # fill with new if room
    remaining = max(0, daily - len(due))
    news = []
    if remaining and new_n:
        for c in db.new_cards(limit=new_n, text_id=focus):
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


def _card_acceptables(card: dict) -> list[str]:
    from src.answers import merge_acceptables

    return merge_acceptables(
        card.get("accepted_answers") or "",
        card.get("translation_gloss") or "",
        card.get("dse_usage") or "",
    )


def _grade_typed_answer(
    user: str,
    correct: str,
    usage: str = "",
    accepted_answers: str = "",
) -> dict:
    """Multi-answer grade: 窮困 OR 困苦 both OK."""
    from src.answers import grade_answer, merge_acceptables

    accepted = merge_acceptables(accepted_answers, correct, usage)
    return grade_answer(user, accepted, full_gloss=f"{correct} {usage}")


def _reset_card_ui() -> None:
    st.session_state.show_answer = False
    st.session_state.user_answer = ""
    st.session_state.answer_feedback = None
    # clear text input widget state for next card
    if "typed_answer_box" in st.session_state:
        del st.session_state["typed_answer_box"]


def page_review() -> None:
    st.markdown("### 🔁 今日複習")

    # Chapter filter on review page
    ready_texts = [
        t
        for t in db.list_texts()
        if db.text_status(t["id"])["has_original"]
        and db.text_status(t["id"])["vocab_count"] > 0
    ]
    if ready_texts:
        labels = ["全部已載入篇章"] + [
            f"{t['order_index']:02d} · {t['title']}" for t in ready_texts
        ]
        id_map = {
            f"{t['order_index']:02d} · {t['title']}": t["id"] for t in ready_texts
        }
        cur = st.session_state.get("study_text_id")
        if cur and any(t["id"] == cur for t in ready_texts):
            t0 = next(t for t in ready_texts if t["id"] == cur)
            idx0 = labels.index(f"{t0['order_index']:02d} · {t0['title']}")
        else:
            idx0 = 0
        pick = st.selectbox("溫習篇章", labels, index=idx0, key="review_chapter_select")
        new_focus = None if pick == "全部已載入篇章" else id_map[pick]
        if new_focus != st.session_state.get("study_text_id"):
            st.session_state.study_text_id = new_focus
            st.session_state.review_queue = []
            st.session_state.review_idx = 0
            _reset_card_ui()
            st.rerun()

    if st.button("重新整理佇列", use_container_width=True, key="review_rebuild"):
        st.session_state.review_queue = _build_review_queue()
        st.session_state.review_idx = 0
        _reset_card_ui()
        st.session_state.session_stats = {"done": 0, "again": 0, "good": 0, "typed_ok": 0}
        st.rerun()

    if not st.session_state.review_queue:
        st.session_state.review_queue = _build_review_queue()
        st.session_state.review_idx = 0
        _reset_card_ui()

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
                <div class="wy-stat"><div class="num">{stats.get('typed_ok', 0)}</div><div class="label">輸入接近</div></div>
                <div class="wy-stat"><div class="num">{stats['again']}</div><div class="label">再來</div></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("再來一輪", type="primary", use_container_width=True, key="review_again_round"):
            st.session_state.review_queue = _build_review_queue()
            st.session_state.review_idx = 0
            _reset_card_ui()
            st.session_state.session_stats = {"done": 0, "again": 0, "good": 0, "typed_ok": 0}
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

    correct = card.get("translation_gloss") or ""
    usage = card.get("dse_usage") or ""
    acceptables = _card_acceptables(card)

    if not st.session_state.show_answer:
        st.text_input(
            "你的答案",
            key="typed_answer_box",
            placeholder="",
            label_visibility="collapsed",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✓ 核對答案", type="primary", use_container_width=True, key="check_ans"):
                typed = st.session_state.get("typed_answer_box", "")
                st.session_state.user_answer = typed
                result = _grade_typed_answer(
                    typed,
                    correct,
                    usage,
                    card.get("accepted_answers") or "",
                )
                st.session_state.answer_feedback = result
                st.session_state.show_answer = True
                if result.get("status") in ("exact", "partial"):
                    st.session_state.session_stats["typed_ok"] = (
                        st.session_state.session_stats.get("typed_ok", 0) + 1
                    )
                st.rerun()
        with c2:
            if st.button("直接看答案", use_container_width=True, key="skip_type"):
                st.session_state.user_answer = ""
                st.session_state.answer_feedback = {
                    "status": "skip",
                    "matched": None,
                    "accepted": acceptables,
                }
                st.session_state.show_answer = True
                st.rerun()
    else:
        fb = st.session_state.answer_feedback or {}
        if isinstance(fb, str):
            # backward compat
            fb = {"status": fb, "matched": None, "accepted": acceptables}
        status = fb.get("status")
        matched = fb.get("matched")
        accepted = fb.get("accepted") or acceptables
        user = st.session_state.user_answer or ""

        from src.answers import format_accepted_display

        if status == "exact":
            msg = f"✅ 正確！「{_esc(user)}」算對"
            if matched:
                msg += f"（對上可接受答案：{_esc(matched)}）"
            st.success(msg)
        elif status == "partial":
            st.info("🟡 有點接近，但還不夠準。請看下方可接受答案。")
        elif status == "miss":
            st.warning("❌ 未命中可接受答案。先記住其中一個意思即可。")
        elif status == "empty":
            st.warning("你沒有輸入答案。請對照可接受答案後自評。")
        else:
            st.caption("已顯示參考答案（未輸入）。")

        if user.strip():
            st.markdown(
                f"""
                <div class="wy-card">
                  <div class="wy-card-title">你的輸入</div>
                  <div class="wy-original" style="font-size:1.05rem;">{_esc(user)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown(
            f"""
            <div class="wy-card">
              <div class="wy-card-title">可接受答案（答中其中一個即可）</div>
              <div class="wy-original wy-accept">
                {_esc(format_accepted_display(accepted))}
              </div>
              <div class="wy-translation">
                <div class="wy-card-title">補充說明</div>
                {_esc(correct or usage or '（無）')}<br/>
                <span class="wy-chip">{_esc(card.get('category') or '')}</span>
                {_esc(usage)}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Left → right: 簡單 | 尚可 | 困難 | 再來一次
        cols = st.columns(4)
        for col, rating in zip(cols, RATING_BUTTON_ORDER):
            with col:
                if st.button(
                    RATING_LABELS[rating],
                    key=f"rate_{rating}",
                    use_container_width=True,
                ):
                    apply_review_to_db(card["id"], card, rating)
                    touch_streak()
                    st.session_state.session_stats["done"] += 1
                    if rating == 0:
                        st.session_state.session_stats["again"] += 1
                        st.session_state.review_queue.append(card)
                    else:
                        st.session_state.session_stats["good"] += 1
                    st.session_state.review_idx += 1
                    _reset_card_ui()
                    st.rerun()

        if st.button("標記已掌握", use_container_width=True, key="review_master"):
            db.update_vocab_status(card["id"], "mastered")
            st.session_state.review_idx += 1
            _reset_card_ui()
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
