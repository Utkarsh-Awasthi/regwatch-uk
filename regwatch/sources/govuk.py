"""GOV.UK via its open Search API (discovery) and Content API (full text).

Discovery is incremental: we ask Search for guidance and regulation pages
whose ``public_timestamp`` moved since the last cursor, newest first, so a
nightly run touches only pages that changed rather than all ~700k.
GOV.UK only bumps ``public_updated_at`` for *major* edits, which is a useful
publisher-side materiality signal that we pass to the diff engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from ..text import clean_text, html_to_paragraphs, renumber
from .http import Fetcher

SEARCH_URL = "https://www.gov.uk/api/search.json"
CONTENT_URL = "https://www.gov.uk/api/content"
SITE = "https://www.gov.uk"

# Content that is regulatory or tells businesses what to do.
# "services" (transactions, status pages) changes are operational, not rule changes.
SUPERGROUPS = ["guidance_and_regulation"]
EXCLUDED_TYPES = {
    "travel_advice", "foi_release", "transparency", "national_statistics", "official_statistics",
    "research", "corporate_report", "correspondence", "news_story", "press_release", "speech",
    "world_location_news", "case_study", "fatality_notice", "service_standard_report",
    "aaib_report", "maib_report", "raib_report", "cma_case", "drug_safety_update",
    "medical_safety_alert", "employment_tribunal_decision", "employment_appeal_tribunal_decision",
    "tax_tribunal_decision", "residential_property_tribunal_decision", "asylum_support_decision",
    "utaac_decision", "traffic_commissioner_regulatory_decision",
}


# Operational pages (service status, waterway conditions, release logs) change daily but aren't rules.
EXCLUDED_TITLE_RE = re.compile(r"experiencing issues|service availability|planned (maintenance|downtime)|current river "
                               r"conditions|applications and decisions for|\brelease \d+|weekly bulletin|statistics", re.I)


@dataclass
class FetchedDoc:
    id: str
    source: str
    url: str
    title: str
    doc_type: str
    nations: list[str]
    topics: list[str]
    paragraphs: list[dict]
    public_updated_at: str | None = None
    change_note: str | None = None
    withdrawn: bool = False
    extra: dict = field(default_factory=dict)


def discover(fetcher: Fetcher, since: str, limit: int = 500, page_size: int = 100) -> Iterator[dict]:
    """Yield search hits updated on or after ``since`` (YYYY-MM-DD), newest first."""
    yielded, start = 0, 0
    while yielded < limit:
        params = [
            ("count", min(page_size, limit - yielded)), ("start", start), ("order", "-public_timestamp"),
            ("filter_public_timestamp", f"from:{since}"),
            ("fields", "content_id"), ("fields", "link"), ("fields", "title"),
            ("fields", "public_timestamp"), ("fields", "content_store_document_type"),
        ] + [("filter_content_purpose_supergroup", g) for g in SUPERGROUPS]
        data = fetcher.json(SEARCH_URL, params=params)
        results = (data or {}).get("results", [])
        if not results:
            return
        for hit in results:
            if hit.get("content_store_document_type") in EXCLUDED_TYPES or EXCLUDED_TITLE_RE.search(hit.get("title", "")):
                continue
            if not hit.get("link", "").startswith("/"):
                continue
            yield hit
            yielded += 1
            if yielded >= limit:
                return
        start += len(results)


def _body_html(body) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, list):  # multi-format bodies: [{"content_type": "text/html", "content": "..."}]
        for item in body:
            if item.get("content_type") == "text/html":
                return item.get("content", "")
    return ""


def parse_content_item(item: dict) -> FetchedDoc | None:
    """Turn a Content API item into paragraphs plus metadata. Returns None for redirects/placeholders."""
    schema = item.get("schema_name", "")
    if schema in ("redirect", "gone", "vanish", "special_route") or not item.get("content_id"):
        return None
    details = item.get("details") or {}
    paras: list[dict] = []
    if details.get("parts"):  # mainstream guides, e.g. /vat-registration
        for part in details["parts"]:
            paras.append({"heading": part.get("title", ""), "text": clean_text(part.get("title", "")), "kind": "heading"})
            paras.extend(html_to_paragraphs(_body_html(part.get("body")), heading=part.get("title", "")))
    else:
        if item.get("description"):
            paras.append({"heading": "Summary", "text": clean_text(item["description"]), "kind": "body"})
        paras.extend(html_to_paragraphs(_body_html(details.get("body"))))
        for key in ("introductory_paragraph", "more_information", "need_to_know", "introduction"):
            if details.get(key):
                paras.extend(html_to_paragraphs(_body_html(details[key])))
    paras = renumber(paras)

    applicability = details.get("national_applicability") or {}
    nations = sorted(k for k, v in applicability.items() if isinstance(v, dict) and v.get("applicable"))
    links = item.get("links") or {}
    topics = sorted({clean_text(t.get("title", "")) for key in ("taxons", "organisations", "parent")
                     for t in links.get(key, [])} - {""})
    history = details.get("change_history") or []
    note = None
    if history:
        latest = max(history, key=lambda h: h.get("public_timestamp", ""))
        note = latest.get("note")
    withdrawn = bool(item.get("withdrawn_notice") and item["withdrawn_notice"].get("explanation"))
    if withdrawn:
        note = clean_text(" ".join(p["text"] for p in html_to_paragraphs(item["withdrawn_notice"]["explanation"]))) or note

    return FetchedDoc(
        id=f"govuk:{item['content_id']}",
        source="govuk",
        url=SITE + item.get("base_path", ""),
        title=clean_text(item.get("title", "")),
        doc_type=item.get("document_type", ""),
        nations=nations,
        topics=topics,
        paragraphs=paras,
        public_updated_at=item.get("public_updated_at"),
        change_note=note,
        withdrawn=withdrawn,
        extra={"first_published_at": item.get("first_published_at")},
    )


def fetch(fetcher: Fetcher, link: str) -> FetchedDoc | None:
    item = fetcher.json(CONTENT_URL + link)
    return parse_content_item(item) if item else None
