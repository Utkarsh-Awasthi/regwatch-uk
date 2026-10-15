"""Relevance ranking: how likely is this change to matter to this profile?

The ranking is deliberately transparent. Every score comes with the reasons
that produced it, so the digest can say *why* an item is there and users can
tell us when we're wrong. Signals:

* **Geography** (hard filter): a Scotland-only rule never reaches a landlord in
  Plymouth. When the page doesn't say where it applies we treat it as UK-wide
  with a small penalty.
* **Vocabulary**: weighted sector terms found in the title, the changed
  paragraphs and the change note. Saturating, so repetition doesn't dominate.
* **Taxonomy**: GOV.UK taxons/organisations and legislation subject headings
  that belong to the user's sectors.
* **Profile attributes**: e.g. HMO rules matter less to someone with no HMO.
* **Materiality** from the diff engine.
* **Feedback**: topics this user repeatedly marked "not relevant" are damped.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .diff import MATERIAL_THRESHOLD
from .profiles import NATIONS, SECTORS, Profile

BAND_LIKELY = 0.55
BAND_POSSIBLE = 0.30
BAND_CHECK = 0.18
ANCHOR_WEIGHT = 2.5
OTHER_COUNCIL = 0.15  # another council's local scheme
UNANCHORED_CAP = 0.15

_PLACE_RE = re.compile(r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+)*) (?:City |County |Borough |District )?Council\b")


@dataclass
class Ranked:
    change: dict
    score: float
    band: str
    reasons: list[str] = field(default_factory=list)
    sector: str | None = None


def _term_score(text: str, terms: dict[str, float]) -> tuple[float, list[str]]:
    low = text.lower()
    s, hits = 0.0, []
    for term, w in terms.items():
        n = len(re.findall(rf"(?<![a-z]){re.escape(term)}(?![a-z])", low))
        if n:
            s += w * (1 + math.log(n))
            hits.append(term)
    return s, hits


def _change_text(change: dict) -> tuple[str, str]:
    rep = change.get("report") or {}
    changed = " ".join((p.get("new_text") or p.get("old_text") or "") for p in rep.get("paragraphs", [])[:15])
    note = rep.get("change_note") or ""
    return changed, note


def score(change: dict, profile: Profile, feedback: dict | None = None) -> Ranked:
    reasons: list[str] = []
    if float(change.get("materiality") or 0) < MATERIAL_THRESHOLD:
        return Ranked(change, 0.0, "excluded", ["Change judged immaterial"])
    nations = change.get("nations") or []
    if nations and profile.nation not in nations:
        where = ", ".join(NATIONS.get(n, n) for n in nations)
        return Ranked(change, 0.0, "excluded", [f"Applies in {where} only"])
    geo = 1.0 if nations else 0.85
    if nations:
        reasons.append(f"Applies in {NATIONS.get(profile.nation)}")

    title = change.get("title", "")
    changed, note = _change_text(change)
    topics = set(change.get("topics") or [])

    # Another council's local scheme is unlikely to be the user's.
    place = 1.0
    councils = set(_PLACE_RE.findall(title + " " + changed[:2000]))
    if councils:
        la = (profile.local_authority or "").lower()
        if la and any(la in c.lower() or c.lower() in la for c in councils):
            place = 1.3
            reasons.append(f"Mentions {profile.local_authority}")
        else:
            place = OTHER_COUNCIL

    best, best_sector, best_hits, best_topic = 0.0, None, [], None
    for sector in profile.sectors:
        spec = SECTORS.get(sector)
        if not spec:
            continue
        s_title, h1 = _term_score(title, spec["terms"])
        s_body, h2 = _term_score(changed, spec["terms"])
        s_note, h3 = _term_score(note, spec["terms"])
        raw = 2.0 * s_title + 1.0 * min(s_body, 12) + 1.5 * s_note
        rel = 1 - math.exp(-raw / 8)
        # GOV.UK taxons match exactly; legislation subject headings (upper case,
        # e.g. "HOUSING, ENGLAND") match on their leading term.
        topic_hit = next((t for t in topics if t in spec["topics"] or
                          (t.isupper() and any(t == sub or t.startswith(sub + ",") for sub in spec["subjects"]))), None)
        # Generic words ("licensing", "property") alone are weak evidence: without a
        # sector-specific anchor term or a matching topic, cap the relevance.
        anchored = any(spec["terms"][h] >= ANCHOR_WEIGHT for h in h1 + h2 + h3)
        if not anchored and not topic_hit:
            rel = min(rel, UNANCHORED_CAP)
        if topic_hit:
            rel = min(1.0, rel + 0.35)
        if rel > best:
            best, best_sector, best_hits, best_topic = rel, sector, list(dict.fromkeys(h1 + h3 + h2)), topic_hit

    if best_hits:
        reasons.append("Mentions " + ", ".join(best_hits[:4]))
    if best_topic:
        reasons.append(f"Filed under '{best_topic}'")

    attr = 1.0
    blob = (title + " " + changed + " " + note).lower()
    a = profile.attributes
    if a.get("hmo") == "no" and ("multiple occupation" in blob or re.search(r"\bhmos?\b", blob)) \
            and not re.search(r"\btenan|\blandlord", title.lower()):
        attr *= 0.45
    if a.get("employees_band") == "0" and best_sector == "employer":
        attr *= 0.5
    if a.get("sells_prepacked_food") == "no" and ("prepacked" in blob or "pre-packed" in blob):
        attr *= 0.6
    if a.get("vat_registered") == "no" and "vat registration threshold" in blob:
        attr *= 1.2
        reasons.append("Relevant to your VAT status")

    fb = 1.0
    if feedback:
        damped = [t for t in topics if feedback.get(t, 0) <= -2]
        if damped:
            fb = 0.6
            reasons.append("You marked similar items as not relevant")

    mat = float(change.get("materiality") or 0)
    final = best * (0.5 + 0.5 * mat) * geo * place * attr * fb
    final = round(min(1.0, final), 3)

    cats = change.get("categories") or []
    if "threshold_or_rate" in cats:
        reasons.append("Changes a threshold, rate or fee")
    elif "deadline_or_period" in cats:
        reasons.append("Changes a date or time limit")
    elif "new_obligation" in cats:
        reasons.append("Adds a requirement")

    if final >= BAND_LIKELY:
        band = "likely"
    elif final >= BAND_POSSIBLE:
        band = "possible"
    elif final >= BAND_CHECK and mat >= 0.75:
        band = "check"
    else:
        band = "excluded"
    return Ranked(change, final, band, reasons, best_sector)


def rank(changes: list[dict], profile: Profile, feedback: dict | None = None, limit: int = 12) -> list[Ranked]:
    out = [score(c, profile, feedback) for c in changes]
    out = [r for r in out if r.band != "excluded"]
    order = {"likely": 0, "possible": 1, "check": 2}
    out.sort(key=lambda r: (order[r.band], -r.score))
    return out[:limit]


def feedback_for(conn, user_id: int) -> dict:
    """Net feedback per topic for a user: +1 relevant/saved_me, -1 not_relevant."""
    rows = conn.execute(
        "SELECT e.kind, d.topics FROM events e JOIN changes c ON c.id=e.change_id JOIN documents d ON d.id=c.document_id "
        "WHERE e.user_id=? AND e.kind IN ('relevant','saved_me','not_relevant')", (user_id,)).fetchall()
    import json
    out: dict[str, int] = {}
    for r in rows:
        delta = -1 if r["kind"] == "not_relevant" else 1
        for t in json.loads(r["topics"] or "[]"):
            out[t] = out.get(t, 0) + delta
    return out
