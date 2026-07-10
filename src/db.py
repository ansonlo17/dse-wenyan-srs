"""SQLite schema and CRUD helpers.

Content (texts / paragraphs / vocab) is shared.
SRS progress, mastery status, review logs, and streaks are per-user (方案 A).
"""

from __future__ import annotations

import hashlib
import json
import secrets
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

PIN_SALT = "dse-wenyan-srs-v1"


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

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  pin_hash TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS user_vocab_status (
  user_id INTEGER NOT NULL,
  vocab_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'learning',
  PRIMARY KEY (user_id, vocab_id),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS srs_cards (
  user_id INTEGER NOT NULL,
  vocab_id INTEGER NOT NULL,
  ease_factor REAL DEFAULT 2.5,
  interval_days REAL DEFAULT 0,
  repetitions INTEGER DEFAULT 0,
  due_at TEXT,
  last_reviewed_at TEXT,
  lapses INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, vocab_id),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  vocab_id INTEGER,
  rating INTEGER,
  reviewed_at TEXT,
  scheduled_interval REAL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY (user_id, key),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def hash_pin(pin: str) -> str:
    raw = f"{PIN_SALT}:{pin.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def random_pin(digits: int = 4) -> str:
    """Random numeric PIN (classroom-friendly, not high security)."""
    digits = max(4, min(8, int(digits)))
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for existing installs + multi-user upgrade."""
    cols = _cols(conn, "vocab_items")
    if cols and "accepted_answers" not in cols:
        conn.execute(
            "ALTER TABLE vocab_items ADD COLUMN accepted_answers TEXT DEFAULT ''"
        )

    # Ensure multi-user tables exist (SCHEMA already creates for fresh DBs)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          pin_hash TEXT,
          created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_vocab_status (
          user_id INTEGER NOT NULL,
          vocab_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'learning',
          PRIMARY KEY (user_id, vocab_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_settings (
          user_id INTEGER NOT NULL,
          key TEXT NOT NULL,
          value TEXT,
          PRIMARY KEY (user_id, key),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    srs_cols = _cols(conn, "srs_cards")
    # Old single-user: PK was vocab_id only (no user_id)
    if srs_cols and "user_id" not in srs_cols:
        _migrate_srs_to_multiuser(conn)
    elif not srs_cols:
        # table missing somehow — SCHEMA should have created it
        pass

    log_cols = _cols(conn, "review_logs")
    if log_cols and "user_id" not in log_cols:
        _migrate_review_logs_to_multiuser(conn)


def _ensure_legacy_user(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM users WHERE name=?", ("預設用戶",)
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO users (name, pin_hash, created_at) VALUES (?, NULL, ?)",
        ("預設用戶", now_iso()),
    )
    return int(cur.lastrowid)


def _migrate_srs_to_multiuser(conn: sqlite3.Connection) -> None:
    uid = _ensure_legacy_user(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS srs_cards_mu (
          user_id INTEGER NOT NULL,
          vocab_id INTEGER NOT NULL,
          ease_factor REAL DEFAULT 2.5,
          interval_days REAL DEFAULT 0,
          repetitions INTEGER DEFAULT 0,
          due_at TEXT,
          last_reviewed_at TEXT,
          lapses INTEGER DEFAULT 0,
          PRIMARY KEY (user_id, vocab_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO srs_cards_mu
          (user_id, vocab_id, ease_factor, interval_days, repetitions,
           due_at, last_reviewed_at, lapses)
        SELECT ?, vocab_id, ease_factor, interval_days, repetitions,
               due_at, last_reviewed_at, lapses
        FROM srs_cards
        """,
        (uid,),
    )
    # copy vocab status for legacy user
    conn.execute(
        """
        INSERT OR IGNORE INTO user_vocab_status (user_id, vocab_id, status)
        SELECT ?, id, status FROM vocab_items
        WHERE status IN ('learning', 'mastered', 'ignored')
        """,
        (uid,),
    )
    conn.execute("DROP TABLE srs_cards")
    conn.execute("ALTER TABLE srs_cards_mu RENAME TO srs_cards")


def _migrate_review_logs_to_multiuser(conn: sqlite3.Connection) -> None:
    uid = _ensure_legacy_user(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_logs_mu (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          vocab_id INTEGER,
          rating INTEGER,
          reviewed_at TEXT,
          scheduled_interval REAL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(vocab_id) REFERENCES vocab_items(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO review_logs_mu
          (user_id, vocab_id, rating, reviewed_at, scheduled_interval)
        SELECT ?, vocab_id, rating, reviewed_at, scheduled_interval
        FROM review_logs
        """,
        (uid,),
    )
    conn.execute("DROP TABLE review_logs")
    conn.execute("ALTER TABLE review_logs_mu RENAME TO review_logs")


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
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )


# ── Users ──────────────────────────────────────────────────────────


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, pin_hash, created_at FROM users ORDER BY name"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "has_pin": bool(r["pin_hash"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


def create_user(
    name: str, pin: str | None = None
) -> tuple[int | None, str, str]:
    """
    Create user with a PIN (auto random 4-digit if pin not given).
    Returns (user_id, error_message, plain_pin). Plain PIN only returned once.
    """
    name = (name or "").strip()
    if not name:
        return None, "請輸入名稱", ""
    if len(name) > 32:
        return None, "名稱太長（最多 32 字）", ""
    plain = (pin or "").strip() or random_pin(4)
    if not plain.isdigit() or not (4 <= len(plain) <= 8):
        return None, "PIN 須為 4–8 位數字", ""
    pin_h = hash_pin(plain)
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (name, pin_hash, created_at) VALUES (?, ?, ?)",
                (name, pin_h, now_iso()),
            )
            uid = int(cur.lastrowid)
            for k in ("daily_limit", "new_cards_per_day"):
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (k,)
                ).fetchone()
                val = row["value"] if row else ("50" if k == "daily_limit" else "10")
                conn.execute(
                    "INSERT OR IGNORE INTO user_settings (user_id, key, value) VALUES (?,?,?)",
                    (uid, k, val),
                )
            return uid, "", plain
    except sqlite3.IntegrityError:
        return None, "此名稱已被使用", ""


def create_users_batch(
    count: int = 20,
    name_prefix: str = "同學",
    start_index: int | None = None,
) -> list[dict[str, Any]]:
    """
    Create up to `count` new accounts with random PINs.
    Names: 同學01, 同學02, … skipping names already taken.
    If start_index is None, continues after the highest existing number for prefix.
    Returns list of {id, name, pin} for newly created accounts only.
    """
    count = max(1, min(100, int(count)))
    prefix = (name_prefix or "同學").strip() or "同學"
    existing = {u["name"] for u in list_users()}

    if start_index is None:
        # find next free index after max matching prefix+digits
        import re

        pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        for name in existing:
            m = pat.match(name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        start_index = max_n + 1
    else:
        start_index = max(1, int(start_index))

    created: list[dict[str, Any]] = []
    i = start_index
    # guard against infinite loop if names collide oddly
    for _ in range(count * 5 + 50):
        if len(created) >= count:
            break
        # zero-pad to at least 2 digits
        name = f"{prefix}{i:02d}" if i < 100 else f"{prefix}{i}"
        i += 1
        if name in existing:
            continue
        uid, err, pin = create_user(name, None)
        if err or uid is None:
            continue
        existing.add(name)
        created.append({"id": uid, "name": name, "pin": pin})
    return created


def user_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


def delete_user(user_id: int) -> tuple[bool, str]:
    """
    Delete one user and all of their progress (SRS, logs, status, settings).
    Shared content (texts / vocab) is kept.
    Returns (ok, message).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return False, "找不到此用戶"
        name = row["name"]
        # Explicit cleanup (in case FK cascade is off on older DBs)
        conn.execute("DELETE FROM review_logs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM srs_cards WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_vocab_status WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return True, f"已刪除「{name}」及其進度"


def delete_users(user_ids: list[int]) -> tuple[int, list[str]]:
    """Delete multiple users. Returns (deleted_count, messages)."""
    deleted = 0
    messages: list[str] = []
    for uid in user_ids:
        ok, msg = delete_user(int(uid))
        if ok:
            deleted += 1
        messages.append(msg)
    return deleted, messages


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, pin_hash, created_at FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "has_pin": bool(row["pin_hash"]),
            "created_at": row["created_at"],
        }


def verify_user_pin(user_id: int, pin: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return False
        if not row["pin_hash"]:
            return True  # legacy no-pin accounts
        return row["pin_hash"] == hash_pin(pin or "")


def ensure_user_deck(user_id: int) -> None:
    """
    Make sure this user has status + SRS rows for all shared content cards.
    New users start with every seeded word as due 'learning' cards.
    """
    with connect() as conn:
        vids = conn.execute(
            """
            SELECT id, status FROM vocab_items
            WHERE status IN ('learning', 'mastered', 'ignored')
            """
        ).fetchall()
        now = now_iso()
        for v in vids:
            vid = v["id"]
            content_status = v["status"] or "learning"
            conn.execute(
                """
                INSERT OR IGNORE INTO user_vocab_status (user_id, vocab_id, status)
                VALUES (?, ?, ?)
                """,
                (user_id, vid, content_status if content_status != "ignored" else "learning"),
            )
            # ignored content stays available unless user ignores later
            if content_status == "ignored":
                continue
            # only create SRS for learning (not yet mastered content default)
            # but if user already has mastered status, skip
            ust = conn.execute(
                "SELECT status FROM user_vocab_status WHERE user_id=? AND vocab_id=?",
                (user_id, vid),
            ).fetchone()
            status = ust["status"] if ust else "learning"
            if status == "learning":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO srs_cards
                      (user_id, vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                    VALUES (?, ?, 2.5, 0, 0, ?, 0)
                    """,
                    (user_id, vid, now),
                )


# ── Global / user settings ─────────────────────────────────────────


def get_setting(key: str, default: str = "", user_id: int | None = None) -> str:
    with connect() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT value FROM user_settings WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()
            if row:
                return row["value"]
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, user_id: int | None = None) -> None:
    with connect() as conn:
        if user_id is not None:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
                """,
                (user_id, key, value),
            )
        else:
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


def text_status(text_id: str, user_id: int | None = None) -> dict[str, Any]:
    with connect() as conn:
        orig = conn.execute(
            "SELECT COUNT(*) AS c FROM paragraphs WHERE text_id=? AND role='original'",
            (text_id,),
        ).fetchone()["c"]
        trans = conn.execute(
            "SELECT COUNT(*) AS c FROM paragraphs WHERE text_id=? AND role='translation'",
            (text_id,),
        ).fetchone()["c"]

        if user_id is None:
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
        else:
            # content cards that exist for this chapter
            content = conn.execute(
                """
                SELECT COUNT(*) AS c FROM vocab_items
                WHERE text_id=? AND status IN ('learning','mastered')
                """,
                (text_id,),
            ).fetchone()["c"]
            mastered = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM vocab_items v
                JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
                WHERE v.text_id=? AND u.status = 'mastered'
                """,
                (user_id, text_id),
            ).fetchone()["c"]
            learning = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM vocab_items v
                LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
                WHERE v.text_id=? AND v.status IN ('learning','mastered')
                  AND COALESCE(u.status, 'learning') = 'learning'
                """,
                (user_id, text_id),
            ).fetchone()["c"]
            vocab = content

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
        for o in originals:
            if o["id"] not in used_orig:
                pairs.append(
                    {"original": o, "translation": None, "confidence": 0.0}
                )
        return pairs

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
    user_id: int | None = None,
) -> int:
    """Add/update shared vocab content. Optionally attach SRS for one user."""
    term = term.strip()
    snippet = (sentence_snippet or "")[:200]
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

        if user_id is not None and status == "learning":
            conn.execute(
                """
                INSERT OR IGNORE INTO user_vocab_status (user_id, vocab_id, status)
                VALUES (?, ?, 'learning')
                """,
                (user_id, vid),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO srs_cards
                  (user_id, vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, ?, 2.5, 0, 0, ?, 0)
                """,
                (user_id, vid, now_iso()),
            )
        return vid


def ignore_vocab_term(
    term: str,
    text_id: str,
    sentence_snippet: str = "",
    user_id: int | None = None,
) -> None:
    # keep shared content; if user_id given, only that user ignores
    if user_id is None:
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
        return
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM vocab_items
            WHERE term=? AND text_id=? AND sentence_snippet=?
            """,
            (term.strip(), text_id, (sentence_snippet or "")[:200]),
        ).fetchone()
        if not row:
            return
        vid = row["id"]
        conn.execute(
            """
            INSERT INTO user_vocab_status (user_id, vocab_id, status)
            VALUES (?, ?, 'ignored')
            ON CONFLICT(user_id, vocab_id) DO UPDATE SET status='ignored'
            """,
            (user_id, vid),
        )
        conn.execute(
            "DELETE FROM srs_cards WHERE user_id=? AND vocab_id=?",
            (user_id, vid),
        )


def list_vocab(
    text_id: str | None = None,
    statuses: tuple[str, ...] | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    if user_id is None:
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

    q = """
      SELECT v.*, t.title AS text_title,
             COALESCE(u.status, 'learning') AS status,
             s.ease_factor, s.interval_days, s.repetitions, s.due_at,
             s.last_reviewed_at, s.lapses
      FROM vocab_items v
      JOIN texts t ON t.id = v.text_id
      LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
      LEFT JOIN srs_cards s ON s.vocab_id = v.id AND s.user_id = ?
      WHERE v.status IN ('learning','mastered')
    """
    params = [user_id, user_id]
    if text_id:
        q += " AND v.text_id=?"
        params.append(text_id)
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        q += f" AND COALESCE(u.status, 'learning') IN ({placeholders})"
        params.extend(statuses)
    q += " ORDER BY v.created_at DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_vocab(vocab_id: int, user_id: int | None = None) -> Optional[dict[str, Any]]:
    with connect() as conn:
        if user_id is None:
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
        else:
            row = conn.execute(
                """
                SELECT v.*, t.title AS text_title,
                       COALESCE(u.status, 'learning') AS status,
                       s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                       s.last_reviewed_at, s.lapses
                FROM vocab_items v
                JOIN texts t ON t.id = v.text_id
                LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
                LEFT JOIN srs_cards s ON s.vocab_id = v.id AND s.user_id = ?
                WHERE v.id=?
                """,
                (user_id, user_id, vocab_id),
            ).fetchone()
        return dict(row) if row else None


def update_vocab_status(
    vocab_id: int, status: str, user_id: int | None = None
) -> None:
    with connect() as conn:
        if user_id is None:
            conn.execute(
                "UPDATE vocab_items SET status=? WHERE id=?",
                (status, vocab_id),
            )
            if status == "mastered":
                far = datetime.now(timezone.utc).replace(year=2099).isoformat()
                # without user, cannot update per-user srs cleanly; skip
                _ = far
            return

        conn.execute(
            """
            INSERT INTO user_vocab_status (user_id, vocab_id, status)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, vocab_id) DO UPDATE SET status=excluded.status
            """,
            (user_id, vocab_id, status),
        )
        if status == "mastered":
            far = datetime.now(timezone.utc).replace(year=2099).isoformat()
            conn.execute(
                """
                INSERT INTO srs_cards
                  (user_id, vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, ?, 2.5, 365, 5, ?, 0)
                ON CONFLICT(user_id, vocab_id) DO UPDATE SET
                  due_at=excluded.due_at, interval_days=365, repetitions=5
                """,
                (user_id, vocab_id, far),
            )
        elif status == "learning":
            conn.execute(
                """
                INSERT OR IGNORE INTO srs_cards
                  (user_id, vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, ?, 2.5, 0, 0, ?, 0)
                """,
                (user_id, vocab_id, now_iso()),
            )
        elif status == "ignored":
            conn.execute(
                "DELETE FROM srs_cards WHERE user_id=? AND vocab_id=?",
                (user_id, vocab_id),
            )


def update_srs(
    vocab_id: int,
    ease_factor: float,
    interval_days: float,
    repetitions: int,
    due_at: str,
    lapses: int,
    rating: int,
    user_id: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO srs_cards
              (user_id, vocab_id, ease_factor, interval_days, repetitions,
               due_at, last_reviewed_at, lapses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, vocab_id) DO UPDATE SET
              ease_factor=excluded.ease_factor,
              interval_days=excluded.interval_days,
              repetitions=excluded.repetitions,
              due_at=excluded.due_at,
              last_reviewed_at=excluded.last_reviewed_at,
              lapses=excluded.lapses
            """,
            (
                user_id,
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
            INSERT INTO review_logs
              (user_id, vocab_id, rating, reviewed_at, scheduled_interval)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, vocab_id, rating, now_iso(), interval_days),
        )


def due_cards(
    limit: int = 50,
    text_id: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    if user_id is None:
        return []
    now = now_iso()
    q = """
            SELECT v.*, t.title AS text_title,
                   COALESCE(u.status, 'learning') AS status,
                   s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                   s.last_reviewed_at, s.lapses
            FROM srs_cards s
            JOIN vocab_items v ON v.id = s.vocab_id
            JOIN texts t ON t.id = v.text_id
            LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = s.user_id
            WHERE s.user_id = ?
              AND v.status IN ('learning','mastered')
              AND COALESCE(u.status, 'learning') = 'learning'
              AND s.due_at <= ?
    """
    params: list[Any] = [user_id, now]
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    q += " ORDER BY s.due_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def new_cards(
    limit: int = 10,
    text_id: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Cards in learning with never reviewed / zero interval ready as new."""
    if user_id is None:
        return []
    q = """
            SELECT v.*, t.title AS text_title,
                   COALESCE(u.status, 'learning') AS status,
                   s.ease_factor, s.interval_days, s.repetitions, s.due_at,
                   s.last_reviewed_at, s.lapses
            FROM vocab_items v
            JOIN texts t ON t.id = v.text_id
            LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
            LEFT JOIN srs_cards s ON s.vocab_id = v.id AND s.user_id = ?
            WHERE v.status IN ('learning','mastered')
              AND COALESCE(u.status, 'learning') = 'learning'
              AND (s.vocab_id IS NULL OR (s.repetitions = 0 AND s.last_reviewed_at IS NULL))
    """
    params: list[Any] = [user_id, user_id]
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    q += " ORDER BY v.created_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def count_due(text_id: str | None = None, user_id: int | None = None) -> int:
    if user_id is None:
        return 0
    now = now_iso()
    q = """
            SELECT COUNT(*) AS c
            FROM srs_cards s
            JOIN vocab_items v ON v.id = s.vocab_id
            LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = s.user_id
            WHERE s.user_id = ?
              AND v.status IN ('learning','mastered')
              AND COALESCE(u.status, 'learning') = 'learning'
              AND s.due_at <= ?
    """
    params: list[Any] = [user_id, now]
    if text_id:
        q += " AND v.text_id = ?"
        params.append(text_id)
    with connect() as conn:
        return conn.execute(q, params).fetchone()["c"]


def count_vocab(text_id: str | None = None, user_id: int | None = None) -> int:
    with connect() as conn:
        if text_id:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM vocab_items WHERE text_id=? AND status IN ('learning','mastered')",
                (text_id,),
            ).fetchone()["c"]
        return conn.execute(
            "SELECT COUNT(*) AS c FROM vocab_items WHERE status IN ('learning','mastered')"
        ).fetchone()["c"]


def review_count_since(iso_date: str, user_id: int | None = None) -> int:
    with connect() as conn:
        if user_id is None:
            return conn.execute(
                """
                SELECT COUNT(*) AS c FROM review_logs
                WHERE reviewed_at >= ?
                """,
                (iso_date,),
            ).fetchone()["c"]
        return conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_logs
            WHERE reviewed_at >= ? AND user_id = ?
            """,
            (iso_date, user_id),
        ).fetchone()["c"]


def reset_user_text_progress(user_id: int, text_id: str) -> None:
    """Reset only this user's SRS/mastery for a chapter (keep shared content)."""
    with connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM vocab_items WHERE text_id=?", (text_id,)
            ).fetchall()
        ]
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        conn.execute(
            f"DELETE FROM review_logs WHERE user_id=? AND vocab_id IN ({ph})",
            [user_id, *ids],
        )
        conn.execute(
            f"DELETE FROM srs_cards WHERE user_id=? AND vocab_id IN ({ph})",
            [user_id, *ids],
        )
        conn.execute(
            f"DELETE FROM user_vocab_status WHERE user_id=? AND vocab_id IN ({ph})",
            [user_id, *ids],
        )
        now = now_iso()
        for vid in ids:
            conn.execute(
                """
                INSERT INTO user_vocab_status (user_id, vocab_id, status)
                VALUES (?, ?, 'learning')
                """,
                (user_id, vid),
            )
            conn.execute(
                """
                INSERT INTO srs_cards
                  (user_id, vocab_id, ease_factor, interval_days, repetitions, due_at, lapses)
                VALUES (?, ?, 2.5, 0, 0, ?, 0)
                """,
                (user_id, vid, now),
            )


def reset_text_progress(text_id: str, user_id: int | None = None) -> None:
    """
    If user_id given: reset only that user's progress for the chapter.
    If None: wipe shared content for that chapter (admin / seed reload).
    """
    if user_id is not None:
        reset_user_text_progress(user_id, text_id)
        return
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
            conn.execute(
                f"DELETE FROM user_vocab_status WHERE vocab_id IN ({ph})", ids
            )
        conn.execute("DELETE FROM vocab_items WHERE text_id=?", (text_id,))


def export_backup() -> dict[str, Any]:
    with connect() as conn:
        tables = [
            "texts",
            "source_documents",
            "paragraphs",
            "alignments",
            "vocab_items",
            "users",
            "user_vocab_status",
            "srs_cards",
            "review_logs",
            "user_settings",
            "settings",
        ]
        data: dict[str, Any] = {"version": 2, "exported_at": now_iso()}
        for table in tables:
            if not _table_exists(conn, table):
                data[table] = []
                continue
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]
        return data


def import_backup(data: dict[str, Any]) -> None:
    tables_clear = [
        "review_logs",
        "srs_cards",
        "user_vocab_status",
        "user_settings",
        "users",
        "vocab_items",
        "alignments",
        "paragraphs",
        "source_documents",
        "settings",
    ]
    with connect() as conn:
        for table in tables_clear:
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table}")
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
            "users",
            "source_documents",
            "paragraphs",
            "alignments",
            "vocab_items",
            "user_vocab_status",
            "srs_cards",
            "review_logs",
            "user_settings",
            "settings",
        ]
        for table in order:
            if not _table_exists(conn, table):
                continue
            for row in data.get(table, []):
                cols = list(row.keys())
                placeholders = ",".join("?" * len(cols))
                col_names = ",".join(cols)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )


def known_terms_for_text(text_id: str, user_id: int | None = None) -> dict[str, str]:
    """term -> mastery bucket for highlighting."""
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT v.term, v.status, s.interval_days, s.lapses
                FROM vocab_items v
                LEFT JOIN srs_cards s ON s.vocab_id = v.id
                WHERE v.text_id=? AND v.status IN ('learning','mastered')
                """,
                (text_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT v.term,
                       COALESCE(u.status, 'learning') AS status,
                       s.interval_days, s.lapses
                FROM vocab_items v
                LEFT JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
                LEFT JOIN srs_cards s ON s.vocab_id = v.id AND s.user_id = ?
                WHERE v.text_id=? AND v.status IN ('learning','mastered')
                  AND COALESCE(u.status, 'learning') IN ('learning','mastered')
                """,
                (user_id, user_id, text_id),
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


def ignored_terms(text_id: str, user_id: int | None = None) -> set[str]:
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT term FROM vocab_items
                WHERE text_id=? AND status='ignored'
                """,
                (text_id,),
            ).fetchall()
            return {r["term"] for r in rows}
        rows = conn.execute(
            """
            SELECT v.term
            FROM vocab_items v
            JOIN user_vocab_status u ON u.vocab_id = v.id AND u.user_id = ?
            WHERE v.text_id=? AND u.status='ignored'
            """,
            (user_id, text_id),
        ).fetchall()
        return {r["term"] for r in rows}
