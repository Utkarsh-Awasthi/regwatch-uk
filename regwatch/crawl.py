"""Nightly crawl: discover → fetch → version → diff → record change.

Idempotent: re-running a crawl over the same window stores nothing new,
because versions are content-hash deduplicated and changes are unique per
(document, new_version).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import db
from .diff import diff_versions, note_report
from .sources import govuk, legislation
from .sources.govuk import FetchedDoc
from .sources.http import Fetcher
from .text import content_hash

log = logging.getLogger("regwatch.crawl")
WATCHLIST = Path(__file__).with_name("watchlist.json")


@dataclass
class CrawlStats:
    seen: int = 0
    new_documents: int = 0
    new_versions: int = 0
    changes: int = 0
    material: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "CrawlStats"):
        for k in ("seen", "new_documents", "new_versions", "changes", "material"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.errors += other.errors


def ingest(conn, doc: FetchedDoc, stats: CrawlStats, recent_since: str | None = None) -> int | None:
    """Store a fetched document; diff against the previous version. Returns change id if one was recorded.

    ``recent_since`` (YYYY-MM-DD) lets a first sighting still produce a change when
    the page was first published, or carries a change note, inside the window.
    """
    stats.seen += 1
    with db.tx(conn):
        prev_doc = db.get_document(conn, doc.id)
        is_new = db.upsert_document(conn, id=doc.id, source=doc.source, url=doc.url, title=doc.title,
                                    doc_type=doc.doc_type, nations=doc.nations, topics=doc.topics,
                                    withdrawn=doc.withdrawn)
        stats.new_documents += int(is_new)
        h = content_hash(doc.paragraphs, {"withdrawn": doc.withdrawn})
        stored = db.add_version(conn, doc.id, h, doc.paragraphs, doc.public_updated_at, doc.change_note)
        if not stored:
            return None
        new_vid, old_vid = stored
        stats.new_versions += 1
        old = db.get_version(conn, old_vid)
        report = None
        if old is None and doc.source == "govuk" and is_new:
            # First sighting of an existing GOV.UK page: no baseline to diff against.
            # New pages are changes in themselves; older pages with a fresh change
            # note get a capped, note-only change; anything else is just a baseline.
            # (New legislation is different: its first sighting *is* the change.)
            window = recent_since or ""
            if (doc.extra.get("first_published_at") or "")[:10] >= window and window:
                report = diff_versions(None, doc.paragraphs).to_dict()
            elif doc.change_note and (doc.public_updated_at or "")[:10] >= window and window:
                report = note_report(doc.change_note).to_dict()
            else:
                return None
        major = None
        if old is not None and doc.source == "govuk":
            major = (old.get("public_updated_at") or "") != (doc.public_updated_at or "")
        withdrawn_now = doc.withdrawn and not (prev_doc or {}).get("withdrawn")
        if report is None:
            report = diff_versions(old["paragraphs"] if old else None, doc.paragraphs,
                                   major_update=major, withdrawn=withdrawn_now).to_dict()
        if report["kind"] == "unchanged":
            return None
        report["change_note"] = doc.change_note
        cid = db.add_change(conn, doc.id, old_vid, new_vid, report)
        if cid:
            stats.changes += 1
            stats.material += int(report["material"])
        return cid


def crawl_govuk(conn, fetcher: Fetcher, since: str | None = None, limit: int = 500) -> CrawlStats:
    stats = CrawlStats()
    since = since or db.get_cursor(conn, "govuk") or (date.today() - timedelta(days=1)).isoformat()
    newest = since
    for hit in govuk.discover(fetcher, since, limit=limit):
        try:
            doc = govuk.fetch(fetcher, hit["link"])
            if doc:
                ingest(conn, doc, stats, recent_since=since)
            newest = max(newest, (hit.get("public_timestamp") or "")[:10])
        except Exception as e:  # one bad page must not stop the nightly run
            log.warning("govuk %s failed: %s", hit.get("link"), e)
            stats.errors.append(f"govuk {hit.get('link')}: {e}")
    with db.tx(conn):
        db.set_cursor(conn, "govuk", newest)
    return stats


def crawl_legislation(conn, fetcher: Fetcher, since: str | None = None, limit: int = 200,
                      watchlist: bool = True) -> CrawlStats:
    stats = CrawlStats()
    since = since or db.get_cursor(conn, "legislation") or (date.today() - timedelta(days=7)).isoformat()
    newest = since
    for item in legislation.discover_new(fetcher, since, limit=limit):
        try:
            doc = legislation.fetch(fetcher, item["type"], item["year"], item["number"], "made")
            if doc:
                ingest(conn, doc, stats)
            newest = max(newest, item["published"])
        except Exception as e:
            log.warning("legislation %s failed: %s", item, e)
            stats.errors.append(f"legislation {item['type']}/{item['year']}/{item['number']}: {e}")
    if watchlist:
        for w in json.loads(WATCHLIST.read_text())["legislation"]:
            try:
                doc = legislation.fetch(fetcher, w["type"], w["year"], w["number"], "latest")
                if doc:
                    # Watched consolidated texts get their own id so they are diffed version-to-version.
                    doc.id += ":latest"
                    first = db.get_document(conn, doc.id) is None
                    cid = ingest(conn, doc, stats)
                    if first and cid:  # first sighting is a baseline, not a change
                        with db.tx(conn):
                            conn.execute("DELETE FROM changes WHERE id=?", (cid,))
                        stats.changes -= 1
                        stats.material -= 1
            except Exception as e:
                log.warning("watchlist %s failed: %s", w, e)
                stats.errors.append(f"watchlist {w['type']}/{w['year']}/{w['number']}: {e}")
    with db.tx(conn):
        db.set_cursor(conn, "legislation", newest)
    return stats


def run(conn, sources=("govuk", "legislation"), since: str | None = None, limit: int = 500,
        watchlist: bool = True, fetcher: Fetcher | None = None) -> CrawlStats:
    fetcher = fetcher or Fetcher()
    total = CrawlStats()
    if "govuk" in sources:
        total.merge(crawl_govuk(conn, fetcher, since, limit))
    if "legislation" in sources:
        total.merge(crawl_legislation(conn, fetcher, since, min(limit, 200), watchlist))
    return total
