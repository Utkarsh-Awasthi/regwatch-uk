"""legislation.gov.uk via its Atom feeds and Crown Legislation Markup (CLML) XML.

Two kinds of change come from here:

* **New legislation** (statutory instruments, Acts) from the ``/new`` feed.
  Each item is a new document; its Explanatory Note is usually the best
  plain-English source the government publishes.
* **Revised legislation** on a watchlist of key Acts and regulations. The
  editorial team consolidates amendments into the "latest available"
  version, and we diff successive versions provision by provision.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterator

from ..text import clean_text, comparable, renumber
from .govuk import FetchedDoc
from .http import Fetcher

BASE = "https://www.legislation.gov.uk"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "leg": "http://www.legislation.gov.uk/namespaces/legislation",
    "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    "dc": "http://purl.org/dc/elements/1.1/",
}
# Types that create or change rules for businesses. Excludes local acts, church measures etc.
TYPES = {"ukpga", "uksi", "ssi", "wsi", "nisr", "asp", "asc", "anaw", "nia", "ukci"}
TYPE_NATION = {"ssi": ["scotland"], "asp": ["scotland"], "wsi": ["wales"], "asc": ["wales"], "anaw": ["wales"],
               "nisr": ["northern_ireland"], "nia": ["northern_ireland"]}
EXTENT = {"E": "england", "W": "wales", "S": "scotland", "N": "northern_ireland", "NI": "northern_ireland"}
# Provisions under these parts are commencement or citation boilerplate.
_BOILERPLATE = re.compile(r"^(citation|commencement|citation,? commencement|interpretation|extent|signature)", re.I)

_ID_RE = re.compile(r"/id/([a-z]+)/(\d{4})/(\d+)")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def discover_new(fetcher: Fetcher, since: str, limit: int = 200, max_pages: int = 15) -> Iterator[dict]:
    """Yield new items from the /new feed published on or after ``since`` (YYYY-MM-DD)."""
    yielded = 0
    for page in range(1, max_pages + 1):
        xml = fetcher.text(f"{BASE}/new/data.feed", params={"page": page})
        if not xml:
            return
        root = ET.fromstring(xml.encode())
        entries = root.findall("atom:entry", NS)
        if not entries:
            return
        for e in entries:
            published = (e.findtext("atom:published", "", NS) or e.findtext("atom:updated", "", NS))[:10]
            if published and published < since:
                return
            m = _ID_RE.search(e.findtext("atom:id", "", NS))
            if not m or m.group(1) not in TYPES:
                continue
            yield {"type": m.group(1), "year": m.group(2), "number": m.group(3),
                   "title": clean_text(e.findtext("atom:title", "", NS)), "published": published}
            yielded += 1
            if yielded >= limit:
                return


def _provision_label(ancestors: list[ET.Element]) -> str:
    nums = []
    for el in ancestors:
        if _local(el.tag) in ("P1", "P2", "P3", "P4", "P5"):
            pn = el.find("leg:Pnumber", NS)
            if pn is not None and (pn.text or "").strip():
                n = pn.text.strip()
                nums.append(n if _local(el.tag) == "P1" else f"({n})")
    return "".join(nums)


def parse_clml(xml: str, leg_type: str, year: str, number: str, version: str = "made") -> FetchedDoc:
    root = ET.fromstring(xml.encode())
    title = clean_text(root.findtext(".//dc:title", "", NS)) or f"{leg_type}/{year}/{number}"
    description = clean_text(root.findtext(".//dc:description", "", NS))
    subjects = [clean_text(s.text or "") for s in root.iterfind(".//dc:subject", NS) if s.text]

    nations = set(TYPE_NATION.get(leg_type, []))
    for el in root.iter():
        ext = el.attrib.get("RestrictExtent")
        if ext:
            nations |= {EXTENT[x] for x in re.split(r"[+\s]", ext) if x in EXTENT}
            break
    blob = " ".join([title] + subjects).lower()
    for name, key in (("england", "england"), ("wales", "wales"), ("scotland", "scotland"),
                      ("northern ireland", "northern_ireland")):
        if re.search(rf"\b{name}\b", blob):
            nations.add(key)

    made = root.find(".//ukm:Made", NS)
    cif = root.find(".//leg:ComingIntoForce", NS)
    cif_text = clean_text("".join(cif.itertext())) if cif is not None else ""

    parent = {c: p for p in root.iter() for c in p}

    def ancestors(el):
        out = []
        while el in parent:
            el = parent[el]
            out.append(el)
        return list(reversed(out))

    paras: list[dict] = []
    if description:
        paras.append({"heading": "Summary", "text": description, "kind": "body"})
    if cif_text:
        paras.append({"heading": "Coming into force", "text": cif_text, "kind": "body"})

    seen = {comparable(description)} if description else set()
    sections = [root.find(".//leg:Body", NS), root.find(".//leg:Schedules", NS), root.find(".//leg:ExplanatoryNotes", NS)]
    for sec in sections:
        if sec is None:
            continue
        sec_name = {"Body": "", "Schedules": "Schedule", "ExplanatoryNotes": "Explanatory Note"}[_local(sec.tag)]
        heading = sec_name
        for el in sec.iter():
            tag = _local(el.tag)
            if tag in ("Footnote", "FootnoteText", "Commentary"):
                continue
            if tag == "Title" and _local(parent.get(el, el).tag) in ("P1group", "Pblock", "Part", "Chapter", "Schedule"):
                heading = clean_text("".join(el.itertext())) or heading
                continue
            if tag != "Text":
                continue
            anc = ancestors(el)
            if any(_local(a.tag) in ("Footnote", "Commentary") for a in anc):
                continue
            text = clean_text("".join(el.itertext()))
            if not text:
                continue
            if sec_name != "Explanatory Note" and _BOILERPLATE.match(heading or ""):
                continue
            if sec_name == "Explanatory Note" and comparable(text) in seen:
                continue  # the first Explanatory Note paragraph usually repeats dc:description
            seen.add(comparable(text))
            label = _provision_label(anc)
            paras.append({"heading": heading, "text": text, "kind": "body", "label": label})

    if not any(p.get("label") is not None or p["heading"] == "Explanatory Note" for p in paras):
        # Some items (often NI rules) are published as PDF only: keep the title as citable evidence.
        paras.insert(0, {"heading": "Title", "text": title, "kind": "body"})
        paras.append({"heading": "Note", "text": "The full text is not yet available as structured data on legislation.gov.uk.",
                      "kind": "body"})
    doc_id = f"leg:{leg_type}/{year}/{number}"
    return FetchedDoc(
        id=doc_id,
        source="legislation",
        url=f"{BASE}/{leg_type}/{year}/{number}" + ("" if version == "latest" else f"/{version}"),
        title=title,
        doc_type=leg_type,
        nations=sorted(nations),
        topics=subjects,
        paragraphs=renumber(paras),
        public_updated_at=(made.attrib.get("Date") if made is not None else None),
        change_note=description or None,
        extra={"coming_into_force": cif_text},
    )


def fetch(fetcher: Fetcher, leg_type: str, year: str, number: str, version: str = "made") -> FetchedDoc | None:
    path = f"{BASE}/{leg_type}/{year}/{number}" + ("" if version == "latest" else f"/{version}") + "/data.xml"
    xml = fetcher.text(path)
    if not xml:
        return None
    return parse_clml(xml, leg_type, year, number, version)
