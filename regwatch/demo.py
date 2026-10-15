"""Offline demo: synthetic before/after versions of realistic pages, plus demo users.

The documents below are ILLUSTRATIVE ONLY. They are written to look like the
kinds of pages RegWatch tracks, but the figures and dates are made up for
the demo and are not current UK rules. Their ids start with ``demo:`` and
their pages say so.
"""
from __future__ import annotations

from . import db, users
from .digest import render, build, deliver
from .sources.govuk import FetchedDoc
from .crawl import CrawlStats, ingest
from .summarise import summarise_pending
from .text import renumber

DEMO_NOTE = "DEMO DATA: illustrative example, not current UK rules."


def P(heading, text, kind="body"):
    return {"heading": heading, "text": text, "kind": kind}


DOCS = [
    {
        "id": "demo:vat-thresholds", "title": "VAT registration thresholds (demo)", "doc_type": "guidance",
        "nations": [], "topics": ["VAT", "Business tax", "HM Revenue & Customs"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Registration threshold", "You must register for VAT if your VAT taxable turnover is more than £85,000 in a rolling 12-month period."),
               P("Registration threshold", "You must register within 30 days of the end of the month when you went over the threshold."),
               P("Deregistration", "You can ask HMRC to cancel your registration if your taxable turnover falls below £83,000.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Registration threshold", "From 1 April 2027 you must register for VAT if your VAT taxable turnover is more than £95,000 in a rolling 12-month period."),
               P("Registration threshold", "You must register within 30 days of the end of the month when you went over the threshold."),
               P("Deregistration", "You can ask HMRC to cancel your registration if your taxable turnover falls below £93,000.")],
    },
    {
        "id": "demo:tenancy-deposits", "title": "Deposit protection for landlords in England (demo)", "doc_type": "guidance",
        "nations": ["england"], "topics": ["Rented housing sector", "Private renting"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Protecting a deposit", "Landlords must put a tenant's deposit in a government-approved tenancy deposit scheme within 30 days of receiving it."),
               P("Prescribed information", "Landlords must give tenants the prescribed information about the scheme within 30 days."),
               P("Penalties", "If you do not protect the deposit, the tenant can apply to court and you may have to pay up to 3 times the deposit.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Protecting a deposit", "Landlords must put a tenant's deposit in a government-approved tenancy deposit scheme within 14 days of receiving it."),
               P("Prescribed information", "Landlords must give tenants the prescribed information about the scheme within 14 days."),
               P("Penalties", "If you do not protect the deposit, the tenant can apply to court and you may have to pay up to 3 times the deposit."),
               P("Penalties", "Local councils may also issue a civil penalty of up to £7,000 for each unprotected deposit.")],
    },
    {
        "id": "demo:hmo-licensing-plymouth", "title": "Additional HMO licensing scheme: Plymouth City Council (demo)",
        "doc_type": "guidance", "nations": ["england"], "topics": ["Rented housing sector"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Who needs a licence", "Plymouth City Council requires a licence for houses in multiple occupation with 5 or more tenants.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Who needs a licence", "Plymouth City Council requires a licence for houses in multiple occupation with 3 or more tenants."),
               P("Fees", "The licence fee is £1,100 per property for a 5-year licence."),
               P("Dates", "The scheme comes into force on 1 March 2027.")],
    },
    {
        "id": "demo:allergen-labelling", "title": "Allergen labelling for food businesses (demo)", "doc_type": "detailed_guide",
        "nations": ["england", "wales", "northern_ireland"], "topics": ["Food labelling and safety", "Food Standards Agency"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Prepacked for direct sale", "Food prepacked for direct sale must show the name of the food and a full ingredients list with the 14 allergens emphasised."),
               P("Non-prepacked food", "Businesses selling non-prepacked food should provide allergen information, which can be given verbally.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Prepacked for direct sale", "Food prepacked for direct sale must show the name of the food and a full ingredients list with the 14 allergens emphasised."),
               P("Non-prepacked food", "Businesses selling non-prepacked food must provide allergen information in writing. Verbal information alone is no longer allowed from 1 January 2028.")],
    },
    {
        "id": "demo:scottish-short-lets", "title": "Short-term let licensing in Scotland (demo)", "doc_type": "guidance",
        "nations": ["scotland"], "topics": ["Rented housing sector"],
        "v1": [P("Summary", DEMO_NOTE), P("Licences", "Hosts must have a short-term let licence. The licence fee is set by each council.")],
        "v2": [P("Summary", DEMO_NOTE), P("Licences", "Hosts must have a short-term let licence. The licence fee is set by each council and must not exceed £600.")],
    },
    {
        "id": "demo:minimum-wage", "title": "National Minimum Wage and National Living Wage rates (demo)", "doc_type": "guidance",
        "nations": [], "topics": ["Pay and contracts", "Employing people"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Current rates", "The National Living Wage for workers aged 21 and over is £12.21 an hour."),
               P("Apprentices", "The apprentice rate is £7.55 an hour.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Current rates", "The National Living Wage for workers aged 21 and over is £12.71 an hour from 1 April 2027."),
               P("Apprentices", "The apprentice rate is £8.00 an hour from 1 April 2027.")],
    },
    {
        "id": "demo:gift-aid-typo", "title": "Claiming Gift Aid as a charity (demo)", "doc_type": "guidance",
        "nations": [], "topics": ["Gift Aid", "Charities and social enterprises"],
        "v1": [P("Summary", DEMO_NOTE),
               P("Making a claim", "Charities can claim Gift Aid on donations from UK taxpayers. Claims must be made within 4 years of the end of the accounting period.")],
        "v2": [P("Summary", DEMO_NOTE),
               P("Making a claim", "Charities can claim Gift Aid on donations from UK taxpayers.  Claims must be made within 4 years of the end of the accounting  period .")],
    },
]


def _doc(d, version) -> FetchedDoc:
    return FetchedDoc(id=d["id"], source="govuk", url=f"https://www.gov.uk/#{d['id']}", title=d["title"],
                      doc_type=d["doc_type"], nations=d["nations"], topics=d["topics"],
                      paragraphs=renumber(d[version]), public_updated_at=f"2026-0{1 if version == 'v1' else 2}-01T00:00:00Z",
                      change_note=None)


def load_demo(conn) -> dict:
    stats = CrawlStats()
    for d in DOCS:
        if db.get_document(conn, d["id"]):
            continue  # already loaded: keep the demo idempotent
        ingest(conn, _doc(d, "v1"), stats)  # baseline
        ingest(conn, _doc(d, "v2"), stats)  # the change
    summarised = summarise_pending(conn, use_llm=False)

    existing = conn.execute("SELECT COUNT(*) FROM users WHERE email='landlord@example.com'").fetchone()[0]
    if not existing:
        users.create_user(conn, email="landlord@example.com", sectors=["landlord"], nation="england",
                          local_authority="Plymouth", attributes={"properties_band": "2-5", "hmo": "yes"})
        users.create_user(conn, email="cafe@example.com", sectors=["food_business", "employer"], nation="wales",
                          attributes={"employees_band": "1-9", "vat_registered": "yes"})
        acct = users.create_user(conn, email="accountant@example.com", sectors=["sole_trader"], nation="england",
                                 role="accountant")
        users.create_user(conn, email=None, sectors=["landlord"], nation="scotland", role="client",
                          parent_id=acct["id"], label="C-101 (Edinburgh letting)")
        users.create_user(conn, email=None, sectors=["sole_trader"], nation="england", role="client",
                          parent_id=acct["id"], label="C-102 (freelance designer)", attributes={"vat_registered": "no"})
        users.create_user(conn, email=None, sectors=["charity"], nation="england", role="client",
                          parent_id=acct["id"], label="C-103 (village hall charity)")

    out = []
    for u in conn.execute("SELECT * FROM users WHERE parent_id IS NULL AND email LIKE '%@example.com'").fetchall():
        dg = build(conn, u)
        subject, html, text = render(dg)
        channel = deliver(conn, dg, subject, html, text)
        out.append({"user": u["email"], "token_url": f"/me/{u['token']}", "items": dg["count"], "subject": subject,
                    "channel": channel})
    return {"changes": stats.changes, "material": stats.material, "summarised": summarised, "digests": out}
