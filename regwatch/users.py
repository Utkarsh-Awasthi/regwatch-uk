"""Sign-up, accountant client profiles, and GDPR rights (access, erasure)."""
from __future__ import annotations

import json
import secrets

from . import db
from .profiles import validate


def create_user(conn, *, email: str | None, sectors, nation, local_authority=None, attributes=None,
                role: str = "member", parent_id: int | None = None, label: str | None = None) -> dict:
    sectors, nation, la, attrs = validate(sectors, nation, local_authority, attributes or {})
    if role not in ("member", "accountant", "client"):
        raise ValueError("bad role")
    if role != "client" and not email:
        raise ValueError("An email address is needed to send your digest.")
    token = secrets.token_urlsafe(24)
    ts = db.now()
    with db.tx(conn):
        uid = conn.execute(
            "INSERT INTO users (email, role, parent_id, label, sectors, nation, local_authority, attributes, token, "
            "consent_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ((email or None) if role != "client" else None, role, parent_id, (label or "")[:60] or None,
             json.dumps(sectors), nation, la, json.dumps(attrs), token, ts, ts)).lastrowid
    return dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def by_token(conn, token: str):
    return conn.execute("SELECT * FROM users WHERE token=? AND active=1", (token,)).fetchone()


def update_profile(conn, user_id: int, sectors, nation, local_authority, attributes):
    sectors, nation, la, attrs = validate(sectors, nation, local_authority, attributes or {})
    with db.tx(conn):
        conn.execute("UPDATE users SET sectors=?, nation=?, local_authority=?, attributes=? WHERE id=?",
                     (json.dumps(sectors), nation, la, json.dumps(attrs), user_id))


def export_data(conn, user_id: int) -> dict:
    """Right of access: everything we hold about this person, including their clients' profiles."""
    u = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    u.pop("token", None)
    clients = [dict(r) for r in conn.execute("SELECT id, label, sectors, nation, local_authority, attributes, created_at "
                                             "FROM users WHERE parent_id=?", (user_id,))]
    ids = [user_id] + [c["id"] for c in clients]
    q = ",".join("?" * len(ids))
    events = [dict(r) for r in conn.execute(f"SELECT kind, change_id, digest_id, at FROM events WHERE user_id IN ({q})", ids)]
    digests = [dict(r) for r in conn.execute("SELECT id, created_at, period_start, period_end, sent_at FROM digests "
                                             "WHERE user_id=?", (user_id,))]
    return {"profile": u, "client_profiles": clients, "digests": digests, "events": events}


def erase(conn, user_id: int) -> None:
    """Right to erasure: hard-delete the person, their client profiles, digests and events.

    Aggregate metrics computed earlier are not personal data and are kept.
    """
    with db.tx(conn):
        ids = [user_id] + [r["id"] for r in conn.execute("SELECT id FROM users WHERE parent_id=?", (user_id,))]
        q = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM events WHERE user_id IN ({q})", ids)
        conn.execute(f"DELETE FROM digest_items WHERE user_id IN ({q}) OR digest_id IN "
                     f"(SELECT id FROM digests WHERE user_id IN ({q}))", ids + ids)
        conn.execute(f"DELETE FROM digests WHERE user_id IN ({q})", ids)
        conn.execute("DELETE FROM users WHERE parent_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def record_event(conn, user_id, digest_id, change_id, kind, note=None):
    with db.tx(conn):
        conn.execute("INSERT INTO events (user_id, digest_id, change_id, kind, note, at) VALUES (?,?,?,?,?,?)",
                     (user_id, digest_id or None, change_id or None, kind, (note or "")[:500] or None, db.now()))
