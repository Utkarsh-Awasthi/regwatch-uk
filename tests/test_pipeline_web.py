import json

from fastapi.testclient import TestClient

from regwatch import db, digest, users
from regwatch.crawl import CrawlStats, ingest
from regwatch.sources.govuk import FetchedDoc, parse_content_item
from regwatch.sources.legislation import parse_clml
from regwatch.text import renumber


def doc(text, updated="2026-01-01", withdrawn=False):
    return FetchedDoc(id="govuk:x", source="govuk", url="https://www.gov.uk/x", title="Deposits for landlords",
                      doc_type="guidance", nations=["england"], topics=["Private renting"],
                      paragraphs=renumber([{"heading": "", "text": text, "kind": "body"}]),
                      public_updated_at=updated, withdrawn=withdrawn)


def test_ingest_is_idempotent_and_diffs_versions(conn):
    s = CrawlStats()
    assert ingest(conn, doc("Landlords must protect deposits within 30 days."), s) is None  # baseline
    assert ingest(conn, doc("Landlords must protect deposits within 30 days."), s) is None  # unchanged
    cid = ingest(conn, doc("Landlords must protect deposits within 14 days.", "2026-02-01"), s)
    assert cid and s.material == 1
    assert ingest(conn, doc("Landlords must protect deposits within 14 days.", "2026-02-01"), s) is None
    assert conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2


def test_parse_govuk_content_item():
    item = {"content_id": "abc", "base_path": "/guide", "title": "Guide", "document_type": "guidance", "schema_name": "publication",
            "description": "About deposits.", "public_updated_at": "2026-01-01",
            "details": {"body": "<p>Landlords must protect deposits.</p>", "national_applicability": {"england": {"applicable": True}},
                        "change_history": [{"note": "Updated the deposit period", "public_timestamp": "2026-01-01"}]},
            "links": {"taxons": [{"title": "Private renting"}]}}
    d = parse_content_item(item)
    assert d.id == "govuk:abc" and d.nations == ["england"] and d.change_note == "Updated the deposit period"
    assert [p["text"] for p in d.paragraphs] == ["About deposits.", "Landlords must protect deposits."]
    assert parse_content_item({"schema_name": "redirect", "content_id": "x"}) is None


def test_parse_clml():
    xml = """<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation" xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <ukm:Metadata><dc:title>The Test Regulations 2026</dc:title><dc:subject>HOUSING, ENGLAND</dc:subject><dc:description>These Regulations change fees.</dc:description><ukm:SecondaryMetadata><ukm:Made Date="2026-10-01"/></ukm:SecondaryMetadata></ukm:Metadata>
    <Secondary><Body><P1group><Title>Fees</Title><P1><Pnumber>2</Pnumber><P1para><P2><Pnumber>1</Pnumber><P2para><Text>The fee is £50.</Text></P2para></P2></P1para></P1></P1group></Body></Secondary></Legislation>"""
    d = parse_clml(xml, "uksi", "2026", "1")
    assert d.title == "The Test Regulations 2026" and d.nations == ["england"]
    body = [p for p in d.paragraphs if p.get("label")]
    assert body[0]["label"] == "2(1)" and body[0]["heading"] == "Fees" and body[0]["text"] == "The fee is £50."


def test_signed_links_reject_tampering():
    t = digest.sign(1, 2, 3, "saved_me")
    assert digest.verify(t) == ["1", "2", "3", "saved_me"]
    assert digest.verify(t.replace("saved_me", "relevant")) is None


def test_end_to_end_web_flow(conn, monkeypatch, tmp_path):
    from regwatch import web
    monkeypatch.setattr(web, "_conn", conn)
    monkeypatch.setattr(digest.settings.__class__, "outbox_dir", tmp_path, raising=False)
    s = CrawlStats()
    ingest(conn, doc("Landlords must protect deposits within 30 days."), s)
    cid = ingest(conn, doc("Landlords must protect deposits within 14 days.", "2026-02-01"), s)
    from regwatch.summarise import summarise_pending
    summarise_pending(conn, use_llm=False)

    c = TestClient(web.app)
    r = c.post("/signup", data={"email": "a@example.com", "sectors": ["landlord"], "nation": "england", "consent": "yes"},
               follow_redirects=False)
    assert r.status_code == 303
    token = r.headers["location"].split("/me/")[1].split("?")[0]
    page = c.get(f"/me/{token}")
    assert page.status_code == 200 and "Deposits for landlords" in page.text and "Likely to affect you" in page.text
    assert c.get(f"/changes/{cid}").status_code == 200
    assert c.post("/signup", data={"email": "b@example.com", "sectors": ["landlord"], "nation": "england"}).status_code == 400

    u = users.by_token(conn, token)
    d = digest.build(conn, u)
    assert d["count"] == 1
    fb = c.get(f"/f/{digest.sign(u['id'], d['id'], cid, 'saved_me')}")
    assert fb.status_code == 200
    click = c.get(f"/t/c/{digest.sign(u['id'], d['id'], cid, 'click')}", params={"u": "https://www.gov.uk/x"}, follow_redirects=False)
    assert click.status_code == 302 and click.headers["location"] == "https://www.gov.uk/x"
    evil = c.get(f"/t/c/{digest.sign(u['id'], d['id'], cid, 'click')}", params={"u": "https://evil.example"}, follow_redirects=False)
    assert evil.status_code == 400
    assert c.get(f"/t/o/{digest.sign(u['id'], d['id'], 0, 'open')}.gif").status_code == 200
    m = c.get("/api/metrics").json()
    assert m["saved_me"] == 1 and m["online_precision"] == 1.0

    exported = c.get(f"/me/{token}/export").json()
    assert exported["profile"]["email"] == "a@example.com" and "token" not in exported["profile"]
    assert c.post(f"/me/{token}/delete").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_accountant_digest_groups_clients(conn):
    s = CrawlStats()
    ingest(conn, doc("Landlords must protect deposits within 30 days."), s)
    ingest(conn, doc("Landlords must protect deposits within 14 days.", "2026-02-01"), s)
    acct = users.create_user(conn, email="acc@example.com", sectors=["sole_trader"], nation="england", role="accountant")
    users.create_user(conn, email=None, sectors=["landlord"], nation="england", role="client", parent_id=acct["id"], label="C-1")
    users.create_user(conn, email=None, sectors=["landlord"], nation="scotland", role="client", parent_id=acct["id"], label="C-2")
    row = conn.execute("SELECT * FROM users WHERE id=?", (acct["id"],)).fetchone()
    d = digest.build(conn, row)
    assert [sec["title"] for sec in d["sections"]] == ["C-1"]
    subject, html, text = digest.render(d)
    assert "C-1" in html and "not legal" in text
    client_emails = conn.execute("SELECT email FROM users WHERE parent_id=?", (acct["id"],)).fetchall()
    assert all(r["email"] is None for r in client_emails)
