"""Weekly digest: rank the week's material changes per profile, render, deliver.

Accountants receive one digest covering all their client profiles, grouped
by client, so one accountant can look after dozens of clients without
sharing those clients' emails with us.

Links in the digest are signed tracking links. They measure opens, clicks
and feedback ("this saved me", "not relevant", "summary was wrong") for the
impact metrics, and carry nothing but ids.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import db, policy, rank
from .config import settings
from .profiles import Profile

TEMPLATES = Path(__file__).with_name("templates")
env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def sign(*parts) -> str:
    msg = ".".join(str(p) for p in parts)
    sig = hmac.new(settings.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{msg}.{sig}"


def verify(token: str) -> list[str] | None:
    try:
        msg, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    good = hmac.new(settings.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
    return msg.split(".") if hmac.compare_digest(sig, good) else None


def _items_for(conn, profile: Profile, changes: list[dict], limit: int) -> list[dict]:
    fb = rank.feedback_for(conn, profile.id) if profile.id else {}
    items = []
    for r in rank.rank(changes, profile, fb, limit=limit):
        ch = r.change
        items.append({"change": ch, "score": r.score, "band": r.band, "reasons": r.reasons,
                      "policy": policy.assess(ch, r.band), "summary": ch.get("summary")})
    return items


def build(conn, user_row, period_end: datetime | None = None, days: int = 7, limit: int = 12,
          persist: bool = True) -> dict:
    """Assemble one user's digest. For accountants, one section per client profile."""
    end = period_end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    changes = db.changes_between(conn, start.replace(microsecond=0).isoformat(), end.replace(microsecond=0).isoformat())
    owner = Profile.from_row(user_row)
    sections = []
    if user_row["role"] == "accountant":
        clients = conn.execute("SELECT * FROM users WHERE parent_id=? AND active=1 ORDER BY id", (user_row["id"],)).fetchall()
        for c in clients:
            p = Profile.from_row(c)
            items = _items_for(conn, p, changes, limit)
            if items:
                sections.append({"profile": p, "title": p.label or f"Client {c['id']}", "items": items})
    else:
        items = _items_for(conn, owner, changes, limit)
        sections.append({"profile": owner, "title": None, "items": items})

    digest_id = None
    if persist:
        with db.tx(conn):
            digest_id = conn.execute(
                "INSERT INTO digests (user_id, created_at, period_start, period_end) VALUES (?,?,?,?)",
                (user_row["id"], db.now(), start.date().isoformat(), end.date().isoformat())).lastrowid
            for sec in sections:
                for i, it in enumerate(sec["items"], 1):
                    conn.execute("INSERT OR IGNORE INTO digest_items (digest_id, user_id, change_id, rank, score, band, reasons) "
                                 "VALUES (?,?,?,?,?,?,?)", (digest_id, sec["profile"].id or user_row["id"],
                                                            it["change"]["id"], i, it["score"], it["band"],
                                                            json.dumps(it["reasons"])))
    return {"id": digest_id, "user": dict(user_row), "owner": owner, "sections": sections,
            "period_start": start.date(), "period_end": end.date(),
            "count": sum(len(s["items"]) for s in sections)}


def _links(digest: dict, item: dict, profile_id: int) -> dict:
    did, cid = digest["id"] or 0, item["change"]["id"]
    base = settings.base_url
    return {
        "source": f"{base}/t/c/{sign(profile_id, did, cid, 'click')}?u={quote(item['change']['url'], safe='')}",
        "detail": f"{base}/changes/{cid}",
        "saved_me": f"{base}/f/{sign(profile_id, did, cid, 'saved_me')}",
        "relevant": f"{base}/f/{sign(profile_id, did, cid, 'relevant')}",
        "not_relevant": f"{base}/f/{sign(profile_id, did, cid, 'not_relevant')}",
        "wrong_summary": f"{base}/f/{sign(profile_id, did, cid, 'wrong_summary')}",
    }


def render(digest: dict) -> tuple[str, str, str]:
    """Returns (subject, html, text)."""
    for sec in digest["sections"]:
        for it in sec["items"]:
            it["links"] = _links(digest, it, sec["profile"].id or digest["user"]["id"])
    n = digest["count"]
    subject = (f"RegWatch: {n} change{'s' if n != 1 else ''} that may affect you "
               f"({digest['period_start']:%d %b}–{digest['period_end']:%d %b})") if n else \
        f"RegWatch: no relevant changes this week ({digest['period_end']:%d %b})"
    uid = digest["user"]["id"]
    ctx = {"d": digest, "disclaimer": policy.DISCLAIMER, "base": settings.base_url,
           "open_pixel": f"{settings.base_url}/t/o/{sign(uid, digest['id'] or 0, 0, 'open')}.gif",
           "manage": f"{settings.base_url}/me/{digest['user']['token']}"}
    return subject, env.get_template("digest_email.html").render(**ctx), env.get_template("digest_email.txt").render(**ctx)


def deliver(conn, digest: dict, subject: str, html: str, text: str) -> str:
    """Send via SMTP when configured; otherwise write .eml/.html to the outbox. Returns the channel used."""
    to = digest["user"].get("email")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.mail_from, to or "undisclosed"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if settings.smtp_host and to:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
            s.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password or "")
            s.send_message(msg)
        channel = "smtp"
    else:
        out = settings.outbox_dir
        out.mkdir(parents=True, exist_ok=True)
        stem = f"digest-{digest['id']}-user{digest['user']['id']}"
        (out / f"{stem}.eml").write_bytes(bytes(msg))
        (out / f"{stem}.html").write_text(html)
        channel = "outbox"
    if digest["id"]:
        with db.tx(conn):
            conn.execute("UPDATE digests SET sent_at=?, channel=? WHERE id=?", (db.now(), channel, digest["id"]))
    return channel


def run_weekly(conn, send: bool = True, days: int = 7) -> list[dict]:
    """Build and deliver digests for every active top-level user (not accountants' clients)."""
    results = []
    users = conn.execute("SELECT * FROM users WHERE active=1 AND parent_id IS NULL").fetchall()
    for u in users:
        d = build(conn, u, days=days)
        subject, html, text = render(d)
        channel = deliver(conn, d, subject, html, text) if send else "dry-run"
        results.append({"user_id": u["id"], "digest_id": d["id"], "items": d["count"], "channel": channel})
    return results
