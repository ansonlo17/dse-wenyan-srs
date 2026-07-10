"""SQLite schema and CRUD helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
SEED_DIR = DATA_DIR / "seed"
DB_PATH = DATA_DIR / "app.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS texts (
  id TEXT PRIMARY KEY,
  order_index INTEGER,
  title TEXT NOT NULL,
  author TEXT,
  genre TEXT,
  parent_id TEXT
);

CREATE TABLE IF NOT EXISTS source_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('original','translation')),
  filename TEXT,
  file_hash TEXT,
  uploaded_at TEXT,
  FOREIGN KEY(text_id) REFERENCES texts(id)
);

CREATE TABLE IF NOT EXISTS paragraphs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('original','translation')),
  idx INTEGER NOT NULL,
  content TEXT NOT NULL,
  FOREIGN KEY(text_id) REFERENCES texts(id)
);

CREATE TABLE IF NOT EXISTS alignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text_id TEXT NOT NULL,
  original_para_id INTEGER,
  translation_para_id INTEGER,
  confidence REAL,
  FOREIGN KEY(text_id) REFERENCES texts(id),
  FOREIGN KEY(original_para_id) REFERENCES paragraphs(id),
  FOREIGN KEY(translation_para_id) REFERENCES paragraphs(id)
);

CREATE TABLE IF NOT EXISTS vocab_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL,
  text_id TEXT NOT NULL,
  paragraph_id INTEGER,
  sentence_snippet TEXT NOT NULL DEFAULT '',
  translation_gloss TEXT,
  dse_usage TEXT,
  accepted_answers TEXT DEFAULT '',
  difficulty INTEGER DEFAULT 3,
  category TEXT,
  status TEXT DEFAULT 'learning',
  created_at TEXT,
  FOREIGN KEY(text_id) REFERENCES texts(id),
  FOREIGN KEY(paragraph_id) REFERENCES paragraphs(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vocab_unique
  ON vocab_items(term, text_id, sentence_snippet);

CREATE TABLE IF NOT EXISTS srs_cards (
  vocab_id INTEGER PRIMARY KEY,
  ease_factor REAL DEFAULT 2.5,
  interval_days REAL DEFAULT 0,
  repetitions INTEGER DEFAULT 0,
  due_at TEXT,
  last_reviewed_at TEXT,
  lapses INTEGER DEFAULT 0,
  FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vocab_id INTEGER,
  rating INTEGER,
  reviewed_at TEXT,
  scheduled_interval REAL,
  FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for existing installs."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vocab_items)").fetchall()}
    if "accepted_answers" not in cols:
        conn.execute(
            "ALTER TABLE vocab_items ADD COLUMN accepted_answers TEXT DEFAULT ''"
        )


def init_db(db_path: Path | None = None) -> None:
    ensure_dirs()
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        seed_texts(conn)
        seed_default_settings(conn)


def seed_texts(conn: sqlite3.Connection) -> None:
    seed_file = SEED_DIR / "texts.json"
    if not seed_file.exists():
        return
    items = json.loads(seed_file.read_text(encoding="utf-8"))
    for t in items:
        conn.execute(
            """
            INSERT OR IGNORE INTO texts (id, order_index, title, author, genre, parent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                t["id"],
                t.get("order_index"),
                t["title"],
                t.get("author"),
                t.get("genre"),
                t.get("parent_id"),
            ),
        )


def seed_default_settings(conn: sqlite3.Connection) -> None:
    defaults = {
        "daily_limit": "50",
        "new_cards_per_day": "10",
        "streak_last_date": "",
        "streak_count": "0",
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def list_texts() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM texts ORDER BY order_index"
        ).fetchall()
        return [dict(r) for r in rows]


def get_text(text_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM texts WHERE id = ?", (text_id,)
        ).fetchone()
        return dict(row) if row else None


def text_status(text_id: str) -> dict[str, Any]:
    with connect() as conn:
        orig = conn.execute(
            "SELECT COUNT(*) AS c FROM paragraphs WHERE text_id=? AND role='original'",
            (text_id,),
        ).fetchone()["c"]
        trans = conn.execute(
            "SELECT COUNT(*) AS c FROM paragraphs WHERE text_id=? AND role='translation'",
            (text_id,),
        ).fetchone()["c"]
        vocab = conn.execute(
            """
            SELECT COUNT(*) AS c FROM vocab_items
            WHERE text_id=? AND status IN ('learning','mastered')
            """,
            (text_id,),
        ).fetchone()["c"]
        mastered = conn.execute(
            """
            SELECT COUNT(*) AS c FROM vocab_items
            WHERE text_id=? AND status='mastered'
            """,
            (text_id,),
        ).fetchone()["c"]
        learning = conn.execute(
            """
            SELECT COUNT(*) AS c FROM vocab_items
            WHERE text_id=? AND status='learning'
            """,
            (text_id,),
        ).fetchone()["c"]
    return {
        "has_original": orig > 0,
        "has_translation": trans > 0,
        "original_paras": orig,
        "translation_paras": trans,
        "vocab_count": vocab,
        "mastered": mastered,
        "learning": learning,
        "mastery_pct": round(100 * mastered / vocab, 1) if vocab else 0.0,
    }


def clear_role_paragraphs(conn: sqlite3.Connection, text_id: str, role: str) -> None:
    # Remove alignments involving these paragraphs
    para_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM paragraphs WHERE text_id=? AND role=?",
            (text_id, role),
        ).fetchall()
    ]
    if para_ids:
        placeholders = ",".join("?" * len(para_ids))
        conn.execute(
            f"""
            DELETE FROM alignments
            WHERE original_para_id IN ({placeholders})
               OR translation_para_id IN ({placeholders})
            """,
            para_ids + para_ids,
        )
    conn.execute(
        "DELETE FROM paragraphs WHERE text_id=? AND role=?",
        (text_id, role),
    )
    conn.execute(
        "DELETE FROM source_documents WHERE text_id=? AND role=?",
        (text_id, role),
    )


def import_paragraphs(
    text_id: str,
    role: str,
    paragraphs: list[str],
    filename: str = "",
    file_hash: str = "",
) -> int:
    with connect() as conn:
        clear_role_paragraphs(conn, text_id, role)
        conn.execute(
            """
            INSERT INTO source_documents (text_id, role, filename, file_hash, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (text_id, role, filename, file_hash, now_iso()),
        )
        for i, content in enumerate(paragraphs):
            conn.execute(
                """
                INSERT INTO paragraphs (text_id, role, idx, content)
                VALUES (?, ?, ?, ?)
                """,
                (text_id, role, i, content),
            )
        return len(paragraphs)


def get_paragraphs(text_id: str, role: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM paragraphs
            WHERE text_id=? AND role=?
            ORDER BY idx
            """,
            (text_id, role),
        ).fetchall()
        return [dict(r) for r in rows]


def save_alignments(
    text_id: str, pairs: list[tuple[int | None, int | None, float]]
) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM alignments WHERE text_id=?", (text_id,))
        for orig_id, trans_id, conf in pairs:
            conn.execute(
                """
                INSERT INTO alignments
                  (text_id, original_para_id, translation_para_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (text_id, orig_id, trans_id, conf),
            )


def get_aligned_pairs(text_id: str) -> list[dict[str, Any]]:
    """Return display pairs: original + optional translation text."""
    originals = get_paragraphs(text_id, "original")
    translations = get_paragraphs(text_id, "translation")
    if not originals and not translations:
        return []

    with connect() as conn:
        aligns = conn.execute(
            "SELECT * FROM alignments WHERE text_id=?", (text_id,)
        ).fetchall()

    if aligns:
        trans_map = {t["id"]: t for t in translations}
        orig_map = {o["id"]: o for o in originals}
        pairs = []
        used_orig = set()
        used_trans = set()
        for a in aligns:
            o = orig_map.get(a["original_para_id"])
            t = trans_map.get(a["translation_para_id"])
            if o:
                used_orig.add(o["id"])
            if t:
                used_trans.add(t["id"])
            pairs.append(
                {
                    "original": o,
                    "translation": t,
                    "confidence": a["confidence"],
                }
            )
        # append unaligned leftovers
        for o in originals:
            if o["id"] not in used_orig:
                pairs.append(
                    {"original": o, "translation": None, "confidence": 0.0}
                )
        return pairs

    # fallback index pairing
    n = max(len(originals), len(translations))
    pairs = []
    for i in range(n):
        pairs.append(
            {
                "original": originals[i] if i < len(originals) else None,
                "translation": translations[i] if i < len(translations) else None,
                "confidence": 0.8 if i < len(originals) and i < len(translations) else 0.0,
            }
        )
    return pairs


def add_vocab(
    term: str,
    text_id: str,
    paragraph_id: int | None = None,
    sentence_snippet: str = "",
    translation_gloss: str = "",
    dse_usage: str = "",
    accepted_answers: str = "",
    difficulty: int = 3,
    category: str = "實詞",
    status: str = "learning",
) -> int:
    term = term.strip()
    snippet = (sentence_snippet or "")[:200]
    # auto-build accept list if not provided: "窮困／貧困／困苦"
    if not accepted_answers:
        from .answers import merge_acceptables

        accepted_answers = "／".join(
            merge_acceptables(None, translation_gloss, dse_usage)
        )
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM vocab_items
            WHERE term=? AND text_id=? AND sentence_snippet=?
            """,
            (term, text_id, snippet),
        ).fetchone()
        if existing:
            vid = existing["id"]
            conn.execute(
                """
                UPDATE vocab_items SET
                  translation_gloss=COALESCE(NULLIF(?,''), translation_gloss),
                  dse_usage=COALESCE(NULLIF(?,''), dse_usage),
                  accepted_answers=COALESCE(NULLIF(?,''), accepted_answers),
                  difficulty=?,
                  category=?,
                  status=?,
                  paragraph_id=COALESCE(?, paragraph_id)
                WHERE id=?
                """,
                (
                    translation_gloss,
                    dse_usage,
                    accepted_answers,
                    difficulty,
                    category,
                    status,
                    paragraph_id,
                    vid,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO vocab_items
                  (term, text_id, paragraph_id, sentence_snippet, translation_gloss,
                   dse_usage, accepted_answers, difficulty, category, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    term,
                    text_id,
                    paragraph_id,
                    snippet,
                    translation_gloss,
                    dse_usage,
                    accepted_answers,
                    difficulty,
                    category,
                    status,
                    now_iso(),
                ),
            )
            vid = int(cur.lastrowid)

        # ensure SRS card for learning items
        if status == "learning":
            conn.execute(
                """
                INSERT OR IGNORE INTO srs_cards
                  (vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, 2.5, 0, 0, ?, 0)
                """,
                (vid, now_iso()),
            )
        return vid


def ignore_vocab_term(term: str, text_id: str, sentence_snippet: str = "") -> None:
    add_vocab(
        term=term,
        text_id=text_id,
        sentence_snippet=sentence_snippet,
        status="ignored",
        difficulty=1,
        category="忽略",
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE vocab_items SET status='ignored'
            WHERE term=? AND text_id=? AND sentence_snippet=?
            """,
            (term.strip(), text_id, (sentence_snippet or "")[:200]),
        )


def list_vocab(
    text_id: str | None = None,
    statuses: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    q = """
      SELECT v.*, t.title AS text_title,
             s.ease_factor, s.interval_days, s.repetitions, s.due_at,
             s.last_reviewed_at, s.lapses
      FROM vocab_items v
      JOIN texts t ON t.id = v.text_id
      LEFT JOIN srs_cards s ON s.vocab_id = v.id
      WHERE 1=1
    """
    params: list[Any] = []
    if text_id:
        q += " AND v.text_id=?"
        params.append(text_id)
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        q += f" AND v.status IN ({placeholders})"
        params.extend(statuses)
    q += " ORDER BY v.created_at DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_vocab(vocab_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT v.*, t.title AS text_title,
                   s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                   s.last_reviewed_at, s.lapses
            FROM vocab_items v
            JOIN texts t ON t.id = v.text_id
            LEFT JOIN srs_cards s ON s.vocab_id = v.id
            WHERE v.id=?
            """,
            (vocab_id,),
        ).fetchone()
        return dict(row) if row else None


def update_vocab_status(vocab_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE vocab_items SET status=? WHERE id=?",
            (status, vocab_id),
        )
        if status == "mastered":
            # push due far away
            far = datetime.now(timezone.utc).replace(year=2099).isoformat()
            conn.execute(
                """
                INSERT INTO srs_cards (vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, 2.5, 365, 5, ?, 0)
                ON CONFLICT(vocab_id) DO UPDATE SET due_at=excluded.due_at, interval_days=365
                """,
                (vocab_id, far),
            )
        elif status == "learning":
            conn.execute(
                """
                INSERT OR IGNORE INTO srs_cards
                  (vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, 2.5, 0, 0, ?, 0)
                """,
                (vocab_id, now_iso()),
            )


def update_srs(
    vocab_id: int,
    ease_factor: float,
    interval_days: float,
    repetitions: int,
    due_at: str,
    lapses: int,
    rating: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO srs_cards
              (vocab_id, ease_factor, interval_days, repetitions, due_at, last_reviewed_at, lapses)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vocab_id) DO UPDATE SET
              ease_factor=excluded.ease_factor,
              interval_days=excluded.interval_days,
              repetitions=excluded.repetitions,
              due_at=excluded.due_at,
              last_reviewed_at=excluded.last_reviewed_at,
              lapses=excluded.lapses
            """,
            (
                vocab_id,
                ease_factor,
                interval_days,
                repetitions,
                due_at,
                now_iso(),
                lapses,
            ),
        )
        conn.execute(
            """
            INSERT INTO review_logs (vocab_id, rating, reviewed_at, scheduled_interval)
            VALUES (?, ?, ?, ?)
            """,
            (vocab_id, rating, now_iso(), interval_days),
        )


def due_cards(limit: int = 50, text_id: str | None = None) -> list[dict[str, Any]]:
    now = now_iso()
    q = """
            SELECT v.*, t.title AS text_title,
                   s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                   s.last_reviewed_at, s.lapses
            FROM srs_cards s
            JOIN vocab_items v ON v.id = s.vocab_id
            JOIN texts t ON t.id = v.text_id
            WHERE v.status = 'learning'
              AND s.due_at <= ?
    """
    params: list[Any] = [now]
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    q += " ORDER BY s.due_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def new_cards(limit: int = 10, text_id: str | None = None) -> list[dict[str, Any]]:
    """Cards in learning with never reviewed / zero interval ready as new."""
    q = """
            SELECT v.*, t.title AS text_title,
                   s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                   s.last_reviewed_at, s.lapses
            FROM vocab_items v
            JOIN texts t ON t.id = v.text_id
            LEFT JOIN srs_cards s ON s.vocab_id = v.id
            WHERE v.status = 'learning'
              AND (s.vocab_id IS NULL OR (s.repetitions = 0 AND s.last_reviewed_at IS NULL))
    """
    params: list[Any] = []
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    q += " ORDER BY v.created_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def count_due(text_id: str | None = None) -> int:
    now = now_iso()
    q = """
            SELECT COUNT(*) AS c
            FROM srs_cards s
            JOIN vocab_items v ON v.id = s.vocab_id
            WHERE v.status='learning' AND s.due_at <= ?
    """
    params: list[Any] = [now]
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    with connect() as conn:
        return conn.execute(q, params).fetchone()["c"]


def count_vocab(text_id: str | None = None) -> int:
    with connect() as conn:
        if text_id:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM vocab_items WHERE text_id=? AND status IN ('learning','mastered')",
                (text_id,),
            ).fetchone()["c"]
        return conn.execute(
            "SELECT COUNT(*) AS c FROM vocab_items WHERE status IN ('learning','mastered')"
        ).fetchone()["c"]


def review_count_since(iso_date: str) -> int:
    with connect() as conn:
        return conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_logs
            WHERE reviewed_at >= ?
            """,
            (iso_date,),
        ).fetchone()["c"]


def reset_text_progress(text_id: str) -> None:
    with connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM vocab_items WHERE text_id=?", (text_id,)
            ).fetchall()
        ]
        if ids:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM review_logs WHERE vocab_id IN ({ph})", ids)
            conn.execute(f"DELETE FROM srs_cards WHERE vocab_id IN ({ph})", ids)
        conn.execute("DELETE FROM vocab_items WHERE text_id=?", (text_id,))


def export_backup() -> dict[str, Any]:
    with connect() as conn:
        tables = [
            "texts",
            "source_documents",
            "paragraphs",
            "alignments",
            "vocab_items",
            "srs_cards",
            "review_logs",
            "settings",
        ]
        data: dict[str, Any] = {"version": 1, "exported_at": now_iso()}
        for table in tables:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]
        return data


def import_backup(data: dict[str, Any]) -> None:
    tables = [
        "review_logs",
        "srs_cards",
        "vocab_items",
        "alignments",
        "paragraphs",
        "source_documents",
        "settings",
    ]
    with connect() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
        # texts: upsert seed + backup
        for row in data.get("texts", []):
            conn.execute(
                """
                INSERT OR REPLACE INTO texts
                  (id, order_index, title, author, genre, parent_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row.get("order_index"),
                    row["title"],
                    row.get("author"),
                    row.get("genre"),
                    row.get("parent_id"),
                ),
            )
        order = [
            "source_documents",
            "paragraphs",
            "alignments",
            "vocab_items",
            "srs_cards",
            "review_logs",
            "settings",
        ]
        for table in order:
            for row in data.get(table, []):
                cols = list(row.keys())
                placeholders = ",".join("?" * len(cols))
                col_names = ",".join(cols)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )


def known_terms_for_text(text_id: str) -> dict[str, str]:
    """term -> mastery bucket for highlighting."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT v.term, v.status, s.interval_days, s.lapses
            FROM vocab_items v
            LEFT JOIN srs_cards s ON s.vocab_id = v.id
            WHERE v.text_id=? AND v.status IN ('learning','mastered')
            """,
            (text_id,),
        ).fetchall()
    result: dict[str, str] = {}
    for r in rows:
        if r["status"] == "mastered":
            result[r["term"]] = "mastered"
        elif (r["lapses"] or 0) >= 2 or (r["interval_days"] or 0) < 3:
            result[r["term"]] = "weak"
        else:
            result[r["term"]] = "learning"
    return result


def ignored_terms(text_id: str) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT term FROM vocab_items
            WHERE text_id=? AND status='ignored'
            """,
            (text_id,),
        ).fetchall()
        return {r["term"] for r in rows}
