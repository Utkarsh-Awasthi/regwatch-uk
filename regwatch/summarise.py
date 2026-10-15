"""Plain-English change summaries, every claim cited to a source paragraph.

Two summarisers share one output shape:

* **Extractive** (always available, deterministic). Builds claims from
  the structured diff ("£85,000 changed to £90,000") and verbatim quotes. It
  is faithful by construction and serves as the fallback.
* **LLM** (Claude, when an API key is configured). Rewrites the same
  evidence in plainer English with structured output. Its result is scored
  by ``faithfulness.score_summary`` and is only used when it clears the
  threshold. Otherwise we fall back to extractive and record why.

Output shape::

    {"headline": {"text": str, "citations": [key]},
     "claims":   [{"text": str, "citations": [key]}],
     "deadline": {"date": "YYYY-MM-DD", "citations": [key]} | None}
"""
from __future__ import annotations

import logging
import os
import re

from . import db, entities
from .config import settings
from .faithfulness import build_evidence, score_summary
from .text import comparable, split_sentences

log = logging.getLogger("regwatch.summarise")

CATEGORY_PHRASES = {
    "threshold_or_rate": "a threshold, rate or fee has changed",
    "deadline_or_period": "a date or time limit has changed",
    "new_obligation": "a new requirement has been added",
    "removed_obligation": "a requirement has been removed or relaxed",
    "penalty": "penalties or offences have changed",
    "exemption": "an exemption has changed",
    "scope": "where or to whom it applies has changed",
    "negation": "the meaning of a rule has been reversed or qualified",
    "new_section": "new guidance has been added",
    "removed_section": "guidance has been removed",
    "wording": "the wording has been revised",
    "withdrawn": "this page has been withdrawn",
}


def _clip(s: str, n: int = 280) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def _novel_sentences(old: str, new: str, n: int = 2) -> list[str]:
    """Up to ``n`` consecutive sentences of ``new`` starting at the first one not in ``old``."""
    old_set = {comparable(s) for s in split_sentences(old)}
    sents = split_sentences(new)
    for i, s in enumerate(sents):
        if comparable(s) not in old_set:
            return [x for x in sents[i:i + n] if comparable(x) not in old_set] or [s]
    return sents[:1]


def _fact_kind(norm: str) -> str:
    if norm.startswith("£"):
        return "money"
    if norm.endswith("%"):
        return "percent"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", norm):
        return "date"
    if re.search(r" (day|working_day|week|month|year|hour)$", norm):
        return "duration"
    return "count"


def _quote(s: str) -> str:
    # The quote must stay verbatim, so clip at a word boundary *inside* it.
    q = _clip(s.strip(), 260)
    if q.endswith("…"):
        return f'"{q[:-1].rstrip()}" (continues)'
    return f'"{q}"'


def extractive(report: dict, title: str = "") -> dict:
    claims = []
    paras = report.get("paragraphs", [])
    if report.get("kind") == "withdrawn":
        claims.append({"text": "The publisher has withdrawn this page.", "citations": []})
    for p in paras[:4]:
        cats = [c for c in p.get("categories", []) if c != "cosmetic"]
        if not cats and report.get("kind") != "new":
            continue
        where = f"Under '{p['heading']}': " if p.get("heading") and p["heading"] not in ("Summary",) else ""
        cite_new = [f"new:{p['new_id']}"] if p.get("new_id") else []
        cite_old = [f"old:{p['old_id']}"] if p.get("old_id") else []
        if p["op"] == "modified" and p.get("old_text"):
            before = dict(entities.surface_facts(p["old_text"]))
            after = dict(entities.surface_facts(p["new_text"]))
            gone = {k: v for k, v in before.items() if k not in after}
            came = {k: v for k, v in after.items() if k not in before}
            # Pair like with like: an amount changes to an amount, a date to a date.
            paired, extra = [], []
            for kind in ("money", "percent", "date", "duration", "count"):
                g = [v for k, v in gone.items() if _fact_kind(k) == kind]
                c = [v for k, v in came.items() if _fact_kind(k) == kind]
                if g and c:
                    paired.append(f"{', '.join(g[:3])} changed to {', '.join(c[:3])}")
                elif c:
                    extra += c
            if paired:
                claims.append({"text": f"{where}{'; '.join(paired)}.", "citations": cite_old + cite_new})
            # Dates are reported once, as the deadline, rather than in every claim.
            extra = [v for v in extra if dict((b, a) for a, b in after.items()).get(v) != report.get("deadline")]
            if extra:
                claims.append({"text": f"{where}the text now includes {', '.join(extra[:3])}.", "citations": cite_new})
            novel = _novel_sentences(p["old_text"], p["new_text"])
            claims.append({"text": f"It now reads: {_quote(' '.join(novel))}", "citations": cite_new})
        elif p["op"] == "added" or (p["op"] == "modified" and not p.get("old_text")):
            label = {"note": "Publisher's change note"}.get(p.get("new_id"), "New text")
            if report.get("kind") == "new":
                label = {"Summary": "Summary", "Explanatory Note": "Explanatory note",
                         "Coming into force": "Coming into force", "Title": "Title"}.get(p.get("heading"), "Text")
                where = ""
            claims.append({"text": f"{where}{label}: {_quote(split_sentences(p['new_text'])[0] if p['new_text'] else '')}",
                           "citations": cite_new})
        elif p["op"] == "removed":
            claims.append({"text": f"{where}Removed: {_quote(split_sentences(p['old_text'])[0] if p['old_text'] else '')}",
                           "citations": cite_old})
    # Drop claims whose quotation repeats one already made (GOV.UK descriptions often
    # repeat the first body paragraph).
    seen_quotes, unique = [], []
    for c in claims:
        q = re.findall(r'"([^"]+)"', c["text"])
        key = comparable(q[0].replace("…", "")) if q else None
        if key and any(key in s or s in key for s in seen_quotes):
            continue
        if key:
            seen_quotes.append(key)
        unique.append(c)
    claims = [c for c in unique if c["citations"] or report.get("kind") == "withdrawn"][:5]

    top_cats = [c for c in report.get("categories", []) if c != "cosmetic"]
    phrase = CATEGORY_PHRASES.get(top_cats[0], "the text has changed") if top_cats else "the text has changed"
    if report.get("kind") == "new":
        phrase = "new " + ("legislation" if title.lower().startswith("the ") else "guidance") + " published"
    first_cite = (claims[0]["citations"][:1] if claims else [])
    headline = {"text": phrase[0].upper() + phrase[1:], "citations": first_cite}

    deadline = None
    if report.get("deadline"):
        for p in paras:
            txt = p.get("new_text") or ""
            if report["deadline"] in entities.fact_strings(txt):
                deadline = {"date": report["deadline"], "citations": [f"new:{p['new_id']}"]}
                break
    return {"headline": headline, "claims": claims, "deadline": deadline}


SYSTEM_PROMPT = """You write plain-English summaries of changes to UK government guidance and legislation for \
small businesses, landlords, sole traders and charities.

Rules:
- Use ONLY the evidence paragraphs provided. Do not add background knowledge, implications, or context the \
evidence does not state.
- Every claim must cite the evidence keys it relies on, e.g. ["old:p3", "new:p4"]. Cite both the old and new \
paragraph when describing what changed between them.
- Copy every number, amount, percentage, period and date exactly as written in the evidence.
- Describe what the source says. Never tell the reader what to do or whether they are affected: no "you should", \
"you must", "you need to", "we recommend", "you are (not) affected". You may quote the source's own words in \
double quotes, verbatim.
- If the evidence is unclear about who is affected or when, say what the source says and nothing more.
- At most 4 claims, each under 35 words, written for a non-lawyer. The headline is under 12 words.
- The deadline is the date (YYYY-MM-DD) the change takes effect, only if the evidence states it."""


def _llm_available() -> bool:
    if not settings.llm_enabled:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def llm(report: dict, title: str, url: str, client=None) -> dict | None:
    """Ask Claude for a structured, cited summary. Returns None on refusal or error."""
    from typing import Optional

    import anthropic
    from pydantic import BaseModel, Field

    class Claim(BaseModel):
        text: str
        citations: list[str] = Field(description="Evidence keys such as new:p4 or old:p3")

    class Deadline(BaseModel):
        date: str = Field(description="YYYY-MM-DD")
        citations: list[str]

    class Summary(BaseModel):
        headline: Claim
        claims: list[Claim]
        deadline: Optional[Deadline] = None

    evidence = build_evidence(report)
    if not evidence:
        return None
    lines = [f"Document: {title}", f"Source: {url}", f"Change type: {report.get('kind')}",
             f"Detected categories: {', '.join(report.get('categories', []))}"]
    if report.get("change_note"):
        lines.append(f"Publisher's change note (context only, not citable): {report['change_note']}")
    lines.append("\nEvidence paragraphs:")
    for p in report.get("paragraphs", [])[:12]:
        if p.get("old_id") and p.get("old_text"):
            lines.append(f"[old:{p['old_id']}] (before; {p.get('heading') or 'no heading'}) {p['old_text']}")
        if p.get("new_id") and p.get("new_text"):
            lines.append(f"[new:{p['new_id']}] (after; {p.get('heading') or 'no heading'}) {p['new_text']}")
    client = client or anthropic.Anthropic()
    try:
        resp = client.beta.messages.parse(
            model=settings.llm_model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_format=Summary,
            output_config={"effort": "medium"},
            # Server-side fallback: a policy decline is retried on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIStatusError as e:
        log.warning("LLM summary failed (%s): %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("LLM summary connection error: %s", e)
        return None
    if resp.stop_reason in ("refusal", "max_tokens") or resp.parsed_output is None:
        log.info("LLM summary unusable (stop_reason=%s)", resp.stop_reason)
        return None
    return resp.parsed_output.model_dump()


def summarise_change(change: dict, use_llm: bool | None = None, client=None) -> tuple[dict, str, float, dict]:
    """Returns (summary, method, faithfulness_score, faithfulness_detail)."""
    report = change["report"]
    use_llm = _llm_available() if use_llm is None else use_llm
    if use_llm:
        s = llm(report, change.get("title", ""), change.get("url", ""), client=client)
        if s:
            res = score_summary(s, report, settings.min_faithfulness)
            if res.passed:
                return s, "llm", res.score, res.to_dict()
            log.info("LLM summary for change %s failed faithfulness (%.2f); falling back", change.get("id"), res.score)
            ex = extractive(report, change.get("title", ""))
            ex_res = score_summary(ex, report, 0)
            return ex, "extractive_fallback", ex_res.score, {"rejected_llm": res.to_dict(), **ex_res.to_dict()}
    ex = extractive(report, change.get("title", ""))
    res = score_summary(ex, report, 0)
    return ex, "extractive", res.score, res.to_dict()


def summarise_pending(conn, limit: int = 200, use_llm: bool | None = None) -> int:
    rows = conn.execute("SELECT id FROM changes WHERE summary IS NULL AND material=1 ORDER BY materiality DESC LIMIT ?",
                        (limit,)).fetchall()
    for r in rows:
        ch = db.change_row(conn, r["id"])
        summary, method, score, _ = summarise_change(ch, use_llm=use_llm)
        with db.tx(conn):
            db.set_summary(conn, ch["id"], summary, method, score)
    return len(rows)
