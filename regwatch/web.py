"""Web app: sign-up, profile management, change browser, tracking, metrics."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import db, digest, metrics, policy, users
from .profiles import ALLOWED_ATTRIBUTES, NATIONS, SECTORS, Profile
from .summarise import CATEGORY_PHRASES

app = FastAPI(title="RegWatch UK", docs_url="/api/docs")
templates = Jinja2Templates(directory=str(digest.TEMPLATES))
templates.env.globals.update(DISCLAIMER=policy.DISCLAIMER, SECTORS=SECTORS, NATIONS=NATIONS,
                             ATTRS=ALLOWED_ATTRIBUTES, CATS=CATEGORY_PHRASES, BANDS=policy.BAND_LABELS)
_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = db.connect()
    return _conn


def _attrs_from_form(form) -> dict:
    return {k: form.get(k) for k in ALLOWED_ATTRIBUTES if form.get(k)}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    recent = db.recent_changes(conn(), limit=6)
    return templates.TemplateResponse(request, "index.html", {"recent": recent, "error": None})


@app.post("/signup", response_class=HTMLResponse)
async def signup(request: Request):
    form = await request.form()
    if form.get("consent") != "yes":
        return templates.TemplateResponse(request, "index.html", {"recent": [], "error": "Please tick the consent box."},
                                          status_code=400)
    try:
        u = users.create_user(conn(), email=(form.get("email") or "").strip()[:200], sectors=form.getlist("sectors"),
                              nation=form.get("nation", ""), local_authority=form.get("local_authority"),
                              attributes=_attrs_from_form(form),
                              role="accountant" if form.get("accountant") == "yes" else "member")
    except ValueError as e:
        return templates.TemplateResponse(request, "index.html", {"recent": [], "error": str(e)}, status_code=400)
    return RedirectResponse(f"/me/{u['token']}?welcome=1", status_code=303)


def _user_or_404(token):
    u = users.by_token(conn(), token)
    if not u:
        raise HTTPException(404, "Profile not found")
    return u


@app.get("/me/{token}", response_class=HTMLResponse)
def me(request: Request, token: str, days: int = 7):
    u = _user_or_404(token)
    d = digest.build(conn(), u, days=max(1, min(days, 90)), persist=False)
    for sec in d["sections"]:
        for it in sec["items"]:
            it["links"] = {"detail": f"/changes/{it['change']['id']}", "source": it["change"]["url"]}
    clients = conn().execute("SELECT * FROM users WHERE parent_id=? ORDER BY id", (u["id"],)).fetchall()
    return templates.TemplateResponse(request, "me.html", {
        "u": u, "profile": Profile.from_row(u), "d": d, "days": days, "welcome": request.query_params.get("welcome"),
        "clients": [Profile.from_row(c) for c in clients], "sectors_sel": json.loads(u["sectors"]),
        "attrs_sel": json.loads(u["attributes"])})


@app.post("/me/{token}/profile")
async def me_update(token: str, request: Request):
    u = _user_or_404(token)
    form = await request.form()
    try:
        users.update_profile(conn(), u["id"], form.getlist("sectors"), form.get("nation", ""),
                             form.get("local_authority"), _attrs_from_form(form))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/me/{token}", status_code=303)


@app.post("/me/{token}/clients")
async def me_add_client(token: str, request: Request):
    u = _user_or_404(token)
    if u["role"] != "accountant":
        raise HTTPException(403, "Only accountant accounts can add client profiles")
    form = await request.form()
    try:
        users.create_user(conn(), email=None, sectors=form.getlist("sectors"), nation=form.get("nation", ""),
                          local_authority=form.get("local_authority"), attributes=_attrs_from_form(form),
                          role="client", parent_id=u["id"], label=form.get("label"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/me/{token}", status_code=303)


@app.post("/me/{token}/clients/{client_id}/delete")
def me_delete_client(token: str, client_id: int):
    u = _user_or_404(token)
    row = conn().execute("SELECT id FROM users WHERE id=? AND parent_id=?", (client_id, u["id"])).fetchone()
    if row:
        users.erase(conn(), client_id)
    return RedirectResponse(f"/me/{token}", status_code=303)


@app.get("/me/{token}/export")
def me_export(token: str):
    u = _user_or_404(token)
    return JSONResponse(users.export_data(conn(), u["id"]),
                        headers={"Content-Disposition": "attachment; filename=regwatch-my-data.json"})


@app.post("/me/{token}/delete", response_class=HTMLResponse)
def me_delete(request: Request, token: str):
    u = _user_or_404(token)
    users.erase(conn(), u["id"])
    return templates.TemplateResponse(request, "message.html", {
        "title": "Your data has been deleted",
        "body": "We have permanently deleted your profile, any client profiles, your digests and your feedback. "
                "You won't receive any more emails."})


@app.get("/changes", response_class=HTMLResponse)
def changes(request: Request, all: int = 0, source: str | None = None):
    rows = db.recent_changes(conn(), limit=200, material_only=not all)
    if source:
        rows = [r for r in rows if r["source"] == source]
    return templates.TemplateResponse(request, "changes.html", {"rows": rows, "all": all, "source": source})


@app.get("/changes/{change_id}", response_class=HTMLResponse)
def change_detail(request: Request, change_id: int):
    ch = db.change_row(conn(), change_id)
    if not ch:
        raise HTTPException(404)
    from .faithfulness import build_evidence, score_summary
    faith = score_summary(ch["summary"], ch["report"], 0) if ch.get("summary") else None
    return templates.TemplateResponse(request, "change.html", {
        "ch": ch, "evidence": build_evidence(ch["report"]), "faith": faith,
        "assessment": policy.assess(ch, "possible")})


@app.get("/api/changes")
def api_changes(limit: int = 50):
    return db.recent_changes(conn(), limit=min(limit, 500))


@app.get("/t/o/{token}.gif")
def track_open(token: str):
    parts = digest.verify(token)
    if parts and parts[3] == "open":
        users.record_event(conn(), int(parts[0]), int(parts[1]), None, "open")
    return Response(_PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})


@app.get("/t/c/{token}")
def track_click(token: str, u: str):
    parts = digest.verify(token)
    if not parts or parts[3] != "click":
        raise HTTPException(400, "Invalid link")
    ch = db.change_row(conn(), int(parts[2]))
    # Only redirect to the change's own source: never an open redirect.
    if not ch or ch["url"] != u:
        raise HTTPException(400, "Invalid link")
    users.record_event(conn(), int(parts[0]), int(parts[1]), int(parts[2]), "click")
    return RedirectResponse(ch["url"], status_code=302)


FEEDBACK_KINDS = {"saved_me": "Thanks! Stories like this are why we built RegWatch.",
                  "relevant": "Thanks. We'll keep sending changes like this.",
                  "not_relevant": "Thanks. We'll show you fewer changes like this.",
                  "wrong_summary": "Thanks for flagging this. A person will review the summary against the source."}


@app.get("/f/{token}", response_class=HTMLResponse)
def feedback(request: Request, token: str):
    parts = digest.verify(token)
    if not parts or parts[3] not in FEEDBACK_KINDS:
        raise HTTPException(400, "Invalid link")
    uid, did, cid, kind = int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
    users.record_event(conn(), uid, did, cid, kind)
    return templates.TemplateResponse(request, "feedback.html", {"token": token, "kind": kind, "msg": FEEDBACK_KINDS[kind]})


@app.post("/f/{token}", response_class=HTMLResponse)
def feedback_note(request: Request, token: str, note: str = Form("")):
    parts = digest.verify(token)
    if not parts or parts[3] not in FEEDBACK_KINDS:
        raise HTTPException(400, "Invalid link")
    c = conn()
    with db.tx(c):
        c.execute("UPDATE events SET note=? WHERE id=(SELECT MAX(id) FROM events WHERE user_id=? AND change_id=? "
                  "AND kind=?)", (note[:500], int(parts[0]), int(parts[2]), parts[3]))
    return templates.TemplateResponse(request, "message.html", {"title": "Thank you", "body": "Your note has been saved."})


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    return templates.TemplateResponse(request, "metrics.html", {"m": metrics.compute(conn()),
                                                                "now": datetime.now(timezone.utc)})


@app.get("/api/metrics")
def metrics_api():
    return metrics.compute(conn())


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {})


@app.get("/healthz")
def healthz():
    conn().execute("SELECT 1")
    return {"ok": True}
