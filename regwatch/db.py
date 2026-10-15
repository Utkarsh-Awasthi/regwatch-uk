"""SQLite storage. One file, WAL mode, safe for a nightly writer plus a web reader.

Schema notes
------------
* ``versions`` keeps every distinct version of a document (content-hash
  deduplicated), so any change can be re-diffed or re-summarised later.
* ``users`` is deliberately minimal (GDPR data minimisation): an email, a
  handful of coarse profile tags, and consent time. No names, no postcodes,
  no addresses. Local authority is optional and is the finest geography held.
* ``events`` records opens, clicks and feedback for the impact metrics.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,               -- govuk:<content_id> | leg:<type>/<year>/<number>
    source TEXT NOT NULL,              -- govuk | legislation
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    doc_type TEXT,
    nations TEXT NOT NULL DEFAULT '[]',   -- JSON list; empty means UK-wide / unknown
    topics TEXT NOT NULL DEFAULT '[]',    -- JSON list of taxons / organisations / subjects
    first_seen TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    current_version INTEGER,
    withdrawn INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    content_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    public_updated_at TEXT,
    change_note TEXT,
    paragraphs TEXT NOT NULL,          -- JSON list of {id, heading, text, kind}
    UNIQUE(document_id, content_hash)
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    old_version INTEGER REFERENCES versions(id),
    new_version INTEGER NOT NULL REFERENCES versions(id),
    detected_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    materiality REAL NOT NULL,
    material INTEGER NOT NULL,
    categories TEXT NOT NULL,
    report TEXT NOT NULL,              -- JSON ChangeReport
    deadline TEXT,
    summary TEXT,                      -- JSON Summary
    summary_method TEXT,               -- llm | extractive
    faithfulness REAL,
    UNIQUE(document_id, new_version)
);
CREATE INDEX IF NOT EXISTS ix_changes_detected ON changes(detected_at);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'member',   -- member | accountant | client
    parent_id INTEGER REFERENCES users(id),-- accountant who manages this client profile
    label TEXT,                            -- accountant's own label for a client, e.g. "Client 12"
    sectors TEXT NOT NULL,
    nation TEXT NOT NULL,
    local_authority TEXT,
    attributes TEXT NOT NULL DEFAULT '{}',
    token TEXT NOT NULL UNIQUE,
    consent_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    sent_at TEXT,
    channel TEXT
);
CREATE TABLE IF NOT EXISTS digest_items (
    digest_id INTEGER NOT NULL REFERENCES digests(id),
    user_id INTEGER NOT NULL REFERENCES users(id),  -- the profile the item was ranked for
    change_id INTEGER NOT NULL REFERENCES changes(id),
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    band TEXT NOT NULL,                -- likely | possible | check
    reasons TEXT NOT NULL,
    PRIMARY KEY (digest_id, user_id, change_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    digest_id INTEGER REFERENCES digests(id),
    change_id INTEGER REFERENCES changes(id),
    kind TEXT NOT NULL,                -- open | click | saved_me | relevant | not_relevant | wrong_summary
    note TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_state (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or settings.db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def j(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def unj(v, default=None):
    if v is None:
        return default
    return json.loads(v)


# --- documents & versions -------------------------------------------------

def upsert_document(conn, *, id, source, url, title, doc_type, nations, topics, withdrawn=False) -> bool:
    """Insert or refresh document metadata. Returns True when the document is new."""
    row = conn.execute("SELECT id FROM documents WHERE id=?", (id,)).fetchone()
    ts = now()
    if row:
        conn.execute("UPDATE documents SET url=?, title=?, doc_type=?, nations=?, topics=?, last_checked=?, withdrawn=? "
                     "WHERE id=?", (url, title, doc_type, j(nations), j(topics), ts, int(withdrawn), id))
        return False
    conn.execute("INSERT INTO documents (id, source, url, title, doc_type, nations, topics, first_seen, last_checked, withdrawn) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (id, source, url, title, doc_type, j(nations), j(topics), ts, ts, int(withdrawn)))
    return True


def add_version(conn, document_id, content_hash, paragraphs, public_updated_at=None, change_note=None):
    """Store a version if its content is new. Returns (new_version_id, previous_version_id) or None."""
    prev = conn.execute("SELECT current_version FROM documents WHERE id=?", (document_id,)).fetchone()
    prev_id = prev["current_version"] if prev else None
    if prev_id:
        cur = conn.execute("SELECT content_hash FROM versions WHERE id=?", (prev_id,)).fetchone()
        if cur and cur["content_hash"] == content_hash:
            return None
    existing = conn.execute("SELECT id FROM versions WHERE document_id=? AND content_hash=?",
                            (document_id, content_hash)).fetchone()
    if existing:  # reverted to an earlier version
        vid = existing["id"]
    else:
        vid = conn.execute("INSERT INTO versions (document_id, content_hash, fetched_at, public_updated_at, change_note, paragraphs) "
                           "VALUES (?,?,?,?,?,?)",
                           (document_id, content_hash, now(), public_updated_at, change_note, j(paragraphs))).lastrowid
    conn.execute("UPDATE documents SET current_version=? WHERE id=?", (vid, document_id))
    return vid, prev_id


def get_version(conn, vid):
    if vid is None:
        return None
    row = conn.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["paragraphs"] = unj(d["paragraphs"], [])
    return d


def get_document(conn, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["nations"] = unj(d["nations"], [])
    d["topics"] = unj(d["topics"], [])
    return d


# --- changes ----------------------------------------------------------------

def add_change(conn, document_id, old_version, new_version, report: dict) -> int | None:
    try:
        return conn.execute(
            "INSERT INTO changes (document_id, old_version, new_version, detected_at, kind, materiality, material, "
            "categories, report, deadline) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (document_id, old_version, new_version, now(), report["kind"], report["materiality"],
             int(report["material"]), j(report["categories"]), j(report), report.get("deadline"))).lastrowid
    except sqlite3.IntegrityError:
        return None


def change_row(conn, change_id) -> dict | None:
    row = conn.execute(
        "SELECT c.*, d.title, d.url, d.source, d.doc_type, d.nations, d.topics FROM changes c "
        "JOIN documents d ON d.id=c.document_id WHERE c.id=?", (change_id,)).fetchone()
    return _hydrate_change(row) if row else None


def _hydrate_change(row) -> dict:
    d = dict(row)
    for k, default in (("categories", []), ("report", {}), ("summary", None), ("nations", []), ("topics", [])):
        if k in d:
            d[k] = unj(d[k], default)
    return d


def changes_between(conn, start: str, end: str, material_only=True) -> list[dict]:
    q = ("SELECT c.*, d.title, d.url, d.source, d.doc_type, d.nations, d.topics FROM changes c "
         "JOIN documents d ON d.id=c.document_id WHERE c.detected_at>=? AND c.detected_at<=?")
    if material_only:
        q += " AND c.material=1"
    q += " ORDER BY c.materiality DESC"
    return [_hydrate_change(r) for r in conn.execute(q, (start, end))]


def recent_changes(conn, limit=50, material_only=True) -> list[dict]:
    q = ("SELECT c.*, d.title, d.url, d.source, d.doc_type, d.nations, d.topics FROM changes c "
         "JOIN documents d ON d.id=c.document_id")
    if material_only:
        q += " WHERE c.material=1"
    q += " ORDER BY c.detected_at DESC, c.materiality DESC LIMIT ?"
    return [_hydrate_change(r) for r in conn.execute(q, (limit,))]


def set_summary(conn, change_id, summary: dict, method: str, faithfulness: float):
    conn.execute("UPDATE changes SET summary=?, summary_method=?, faithfulness=? WHERE id=?",
                 (j(summary), method, faithfulness, change_id))


# --- crawl cursors ------------------------------------------------------------

def get_cursor(conn, source):
    row = conn.execute("SELECT cursor FROM crawl_state WHERE source=?", (source,)).fetchone()
    return row["cursor"] if row else None


def set_cursor(conn, source, cursor):
    conn.execute("INSERT INTO crawl_state (source, cursor, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
                 (source, cursor, now()))
