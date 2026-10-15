"""Semantic change detection between two versions of a document.

A text diff tells you *that* bytes moved. This module tells you *what kind*
of change happened and *how much it matters*:

1. Align paragraphs between versions (sequence alignment, then fuzzy pairing
   inside replaced blocks so an edited paragraph is "modified", not
   "removed + added").
2. For each aligned pair, compare the facts and duties it states: money,
   percentages, periods, dates, counts, obligation/prohibition language,
   negation and geographic scope.
3. Classify every paragraph change and score materiality in [0, 1].

Cosmetic edits (typos, punctuation, link swaps, reflowed formatting) score 0
and never reach a user.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field

from . import entities
from .text import Paragraph, comparable, content_words

# How much each kind of change matters on its own. Tuned against
# evals/fixtures/changes.jsonl; see evals/run_evals.py.
WEIGHTS = {
    "threshold_or_rate": 0.9,   # money, %, counts changed
    "deadline_or_period": 0.85, # dates or durations changed
    "new_obligation": 0.9,
    "removed_obligation": 0.85,
    "penalty": 0.8,
    "exemption": 0.75,
    "scope": 0.7,               # which nations / who it applies to
    "negation": 0.85,
    "new_section": 0.35,
    "removed_section": 0.35,
    "wording": 0.2,
    "cosmetic": 0.0,
}
MATERIAL_THRESHOLD = 0.4
SUMMARY_HEADINGS = {"Summary", "Explanatory Note", "Coming into force", "Title"}
_NEG_RE = re.compile(r"\b(not|no longer|never|none|neither|nor|unless|except)\b", re.I)


@dataclass
class ParaChange:
    op: str                       # modified | added | removed
    old_id: str | None
    new_id: str | None
    old_text: str
    new_text: str
    heading: str
    categories: list[str] = field(default_factory=list)
    facts_before: list[str] = field(default_factory=list)
    facts_after: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class ChangeReport:
    kind: str                     # new | amended | withdrawn | unchanged
    materiality: float
    material: bool
    categories: list[str]
    paragraphs: list[ParaChange]
    deadline: str | None = None
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def similarity(a: str, b: str) -> float:
    ca, cb = comparable(a), comparable(b)
    if not ca and not cb:
        return 1.0
    seq = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()
    wa, wb = content_words(a), content_words(b)
    jac = len(wa & wb) / max(1, len(wa | wb))
    return max(seq, jac)


def _pair_block(old: list[Paragraph], new: list[Paragraph], threshold: float = 0.45):
    """Greedy best-match pairing within a replaced block, preserving order."""
    pairs, used_new = [], set()
    old_left = []
    j_floor = 0
    for o in old:
        best, best_j = 0.0, None
        for j in range(j_floor, len(new)):
            if j in used_new:
                continue
            s = similarity(o["text"], new[j]["text"])
            if s > best:
                best, best_j = s, j
        if best_j is not None and best >= threshold:
            pairs.append((o, new[best_j]))
            used_new.add(best_j)
            j_floor = best_j + 1
        else:
            old_left.append(o)
    new_left = [n for j, n in enumerate(new) if j not in used_new]
    return pairs, old_left, new_left


def align(old: list[Paragraph], new: list[Paragraph]):
    """Yield (op, old_para | None, new_para | None) for every non-equal paragraph.

    Paragraphs that were only moved (identical text, different position) are
    not reported: reordering a page doesn't change what it says.
    """
    ops = list(_align_raw(old, new))
    removed = {}
    for op, o, n in ops:
        if op == "removed":
            removed.setdefault(comparable(o["text"]), []).append(o)
    moved = set()
    for op, o, n in ops:
        if op == "added" and removed.get(comparable(n["text"])):
            moved.add(id(removed[comparable(n["text"])].pop()))
            moved.add(id(n))
    for op, o, n in ops:
        if op in ("removed", "added") and id(o or n) in moved:
            continue
        yield op, o, n


def _align_raw(old: list[Paragraph], new: list[Paragraph]):
    a = [comparable(p["text"]) for p in old]
    b = [comparable(p["text"]) for p in new]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for o in old[i1:i2]:
                yield "removed", o, None
        elif tag == "insert":
            for n in new[j1:j2]:
                yield "added", None, n
        else:
            pairs, old_left, new_left = _pair_block(old[i1:i2], new[j1:j2])
            for o, n in pairs:
                yield "modified", o, n
            for o in old_left:
                yield "removed", o, None
            for n in new_left:
                yield "added", None, n


def classify_pair(old_text: str, new_text: str) -> list[str]:
    """Categories for a modified paragraph. Empty list means cosmetic."""
    if comparable(old_text) == comparable(new_text):
        return ["cosmetic"]
    cats: list[str] = []
    fo, fn = entities.facts(old_text), entities.facts(new_text)
    if fo["money"] != fn["money"] or fo["percent"] != fn["percent"] or fo["count"] != fn["count"]:
        cats.append("threshold_or_rate")
    if fo["date"] != fn["date"] or fo["duration"] != fn["duration"]:
        cats.append("deadline_or_period")
    do, dn = entities.deontic(old_text), entities.deontic(new_text)
    for kind, cat_up, cat_down in (("obligation", "new_obligation", "removed_obligation"),
                                   ("prohibition", "new_obligation", "removed_obligation")):
        if dn.get(kind, 0) > do.get(kind, 0):
            cats.append(cat_up)
        elif dn.get(kind, 0) < do.get(kind, 0):
            cats.append(cat_down)
    if dn.get("removal", 0) > do.get("removal", 0):
        cats.append("removed_obligation")
    if dn.get("penalty", 0) != do.get("penalty", 0) or dn.get("offence", 0) != do.get("offence", 0):
        cats.append("penalty")
    if dn.get("exemption", 0) != do.get("exemption", 0):
        cats.append("exemption")
    if entities.nations(old_text) != entities.nations(new_text):
        cats.append("scope")
    if len(_NEG_RE.findall(old_text)) != len(_NEG_RE.findall(new_text)):
        cats.append("negation")
    if not cats:
        # Same facts and duties. Is it a reword or a link/punctuation tweak?
        wo, wn = content_words(old_text), content_words(new_text)
        changed = len(wo ^ wn)
        cats.append("wording" if changed >= 2 else "cosmetic")
    return sorted(set(cats), key=lambda c: -WEIGHTS[c])


def classify_single(text: str, op: str) -> list[str]:
    """Categories for an added or removed paragraph."""
    cats = []
    d = entities.deontic(text)
    f = entities.facts(text)
    has_duty = any(d.get(k) for k in ("obligation", "prohibition"))
    if has_duty:
        cats.append("new_obligation" if op == "added" else "removed_obligation")
    if d.get("penalty") or d.get("offence"):
        cats.append("penalty")
    if d.get("exemption"):
        cats.append("exemption")
    if f["money"] or f["percent"] or f["count"]:
        cats.append("threshold_or_rate")
    if f["date"] or f["duration"]:
        cats.append("deadline_or_period")
    if not cats:
        cats.append("new_section" if op == "added" else "removed_section")
    return sorted(set(cats), key=lambda c: -WEIGHTS[c])


def _para_score(cats: list[str], kind: str = "body") -> float:
    if not cats:
        return 0.0
    top = max(WEIGHTS[c] for c in cats)
    # Each extra independent signal nudges confidence up a little.
    bonus = 0.03 * (len([c for c in cats if WEIGHTS[c] >= 0.5]) - 1)
    score = min(1.0, top + max(0.0, bonus))
    if kind == "heading":
        score *= 0.5
    return round(score, 3)


def diff_versions(old: list[Paragraph] | None, new: list[Paragraph] | None, *,
                  major_update: bool | None = None, withdrawn: bool = False) -> ChangeReport:
    """Compare two paragraph lists and return a scored change report.

    ``major_update`` is the publisher's own signal where the source has one
    (GOV.UK only bumps ``public_updated_at`` for major changes). A minor
    update caps materiality, because publishers use it for fixes they have
    judged not to change meaning.
    """
    if withdrawn:
        paras = [ParaChange("removed", p["id"], None, p["text"], "", p.get("heading", ""), ["removed_section"])
                 for p in (old or [])[:3]]
        return ChangeReport("withdrawn", 0.7, True, ["withdrawn"], paras)

    if not old:
        paras = []
        for p in new or []:
            if p.get("kind") == "heading":
                continue
            cats = classify_single(p["text"], "added")
            pc = ParaChange("added", None, p["id"], "", p["text"], p.get("heading", ""), cats,
                            [], sorted(entities.fact_strings(p["text"])), _para_score(cats))
            paras.append(pc)
        # The publisher's own summary (GOV.UK description, legislation Explanatory Note,
        # coming-into-force line) is the best plain-English evidence for a new document.
        paras.sort(key=lambda c: (c.heading not in SUMMARY_HEADINGS, -c.score))
        cats = sorted({c for pc in paras for c in pc.categories}, key=lambda c: -WEIGHTS[c])
        dl = _deadline(paras)
        # A brand-new rule is material by definition; materiality reflects how much it asks of people.
        mat = max([0.6] + [pc.score for pc in paras[:5]])
        return ChangeReport("new", round(mat, 3), True, cats, paras[:25], dl,
                            {"added": len(paras), "removed": 0, "modified": 0})

    changes: list[ParaChange] = []
    for op, o, n in align(old, new or []):
        if op == "modified":
            cats = classify_pair(o["text"], n["text"])
            kind = n.get("kind", "body")
        else:
            p = o or n
            cats = classify_single(p["text"], op)
            kind = p.get("kind", "body")
        pc = ParaChange(op, o and o["id"], n and n["id"], o["text"] if o else "", n["text"] if n else "",
                        (n or o).get("heading", ""), cats,
                        sorted(entities.fact_strings(o["text"])) if o else [],
                        sorted(entities.fact_strings(n["text"])) if n else [],
                        _para_score([c for c in cats if c != "cosmetic"], kind))
        changes.append(pc)

    real = [c for c in changes if c.score > 0]
    stats = {k: sum(1 for c in changes if c.op == k) for k in ("added", "removed", "modified")}
    stats["cosmetic"] = len(changes) - len(real)
    if not real:
        return ChangeReport("unchanged" if not changes else "amended", 0.0, False, ["cosmetic"] if changes else [],
                            changes[:10], None, stats)

    real.sort(key=lambda c: -c.score)
    scores = [c.score for c in real]
    # Noisy-OR over the strongest paragraph changes: several medium changes add up.
    p_none = 1.0
    for s in scores[:5]:
        p_none *= (1 - s * 0.6)
    mat = max(scores[0], 1 - p_none)
    if major_update is False:
        mat = min(mat, 0.35)
    cats = sorted({c for pc in real for c in pc.categories}, key=lambda c: -WEIGHTS[c])
    return ChangeReport("amended", round(mat, 3), mat >= MATERIAL_THRESHOLD, cats, real[:25], _deadline(real), stats)


def _deadline(paras: list[ParaChange]) -> str | None:
    found = [entities.deadline(p.new_text or p.old_text) for p in paras]
    found = [d for d in found if d]
    return min(found).isoformat() if found else None


def note_report(note: str) -> ChangeReport:
    """A change we only know about from the publisher's change note (no earlier version stored).

    The note is the only evidence, so it becomes the single citable paragraph
    and materiality is capped: we have not seen the text that changed.
    """
    cats = [c for c in classify_single(note, "added") if c != "new_section"] or ["wording"]
    # Notes like "Updated: contact form link" carry no facts or duties: keep them below
    # the materiality threshold. Notes that state a new figure, date or duty pass it,
    # capped at 0.6 because we haven't seen the text that changed.
    substantive = any(WEIGHTS[c] >= 0.5 for c in cats)
    score = min(0.6, _para_score(cats) * 0.7) if substantive else 0.25
    pc = ParaChange("modified", None, "note", "", note, "Publisher's change note", cats,
                    [], sorted(entities.fact_strings(note)), score)
    return ChangeReport("noted", round(score, 3), score >= MATERIAL_THRESHOLD, cats, [pc], _deadline([pc]), {"modified": 1})
