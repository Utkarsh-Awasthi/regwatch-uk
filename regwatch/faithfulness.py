"""Faithfulness scoring for change summaries.

Every claim in a summary must cite one or more evidence paragraphs
(``new:p4`` = paragraph 4 of the new version, ``old:p3`` = paragraph 3 of the
old one). A claim passes only if all of these hold:

1. **Cited.** It has at least one citation, and every citation exists.
2. **Facts grounded.** Every number, amount, percentage, period and date in
   the claim appears in the cited paragraphs. This is the check that catches
   the most dangerous hallucination: a wrong threshold or deadline.
3. **Lexically supported.** Enough of the claim's content words appear in
   the cited text. A cheap entailment proxy that catches invented subject
   matter. Words inside quotation marks must appear verbatim.
4. **No advice.** It doesn't tell the reader what to do (see policy.py).

The summary score is the fraction of claims that pass. An optional
LLM-judge (``judge_with_llm``) adds an entailment verdict for offline evals.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import entities, policy
from .text import comparable, content_words

MIN_SUPPORT = 0.6
# Words a plain-English rewrite may add without the source using them.
_PARAPHRASE_ALLOW = set("""changed change changes now previously before after rises raised rise increased increase
increases reduced reduce reduces decreased decrease falls lowered updated update updates new added adds
removed removes deleted text section paragraph guidance page rule rules says said reads read states
stated longer applies apply plain english source amount date period threshold rate figure
from replaced replaces introduces introduced sets set version earlier latest published also now under
includes include including note publisher publisher's continues withdrawn""".split())
# Clause text runs to the next ";" or sentence-ending "." (a decimal point like £12.71 is not an end).
_CLAUSE = r"(?:[^.;]|\.(?=\d))+"
_DIRECTION_RE = re.compile(
    rf"(?:\bfrom\b(?P<a>.+?)\bto\b(?P<b>{_CLAUSE}))|"
    rf"(?:(?P<a2>[^:;]+?)\b(?:changed|changes|rose|rises|fell|falls|increased|decreased|reduced|went up|went down) to\b(?P<b2>{_CLAUSE}))",
    re.I)
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{8,})[\"”]")


@dataclass
class ClaimCheck:
    text: str
    citations: list[str]
    passed: bool
    problems: list[str] = field(default_factory=list)
    support: float = 0.0


@dataclass
class FaithfulnessResult:
    score: float
    claims: list[ClaimCheck]
    passed: bool

    def to_dict(self):
        return asdict(self)


def build_evidence(report: dict) -> dict[str, str]:
    ev: dict[str, str] = {}
    for p in report.get("paragraphs", []):
        # The heading is part of what a paragraph says ("Deposits: ... 5 weeks").
        head = f"{p['heading']}: " if p.get("heading") else ""
        if p.get("new_id") and p.get("new_text"):
            ev[f"new:{p['new_id']}"] = head + p["new_text"]
        if p.get("old_id") and p.get("old_text"):
            ev[f"old:{p['old_id']}"] = head + p["old_text"]
    return ev


def check_claim(text: str, citations: list[str], evidence: dict[str, str]) -> ClaimCheck:
    problems = []
    if not citations:
        problems.append("no citation")
    missing = [c for c in citations if c not in evidence]
    if missing:
        problems.append(f"unknown citation(s): {', '.join(missing)}")
    cited = " ".join(evidence[c] for c in citations if c in evidence)

    # 2. facts
    claim_facts = entities.fact_strings(text)
    cited_facts = entities.fact_strings(cited)
    ungrounded = sorted(claim_facts - cited_facts)
    if ungrounded:
        problems.append(f"facts not in source: {', '.join(ungrounded)}")

    # 2b. direction: in "from A to B" / "A changed to B", A must come from the old
    # text and B from the new one. Catches a swapped or invented "before" value
    # that happens to appear elsewhere in the cited paragraphs.
    old_cited = " ".join(evidence[c] for c in citations if c.startswith("old:") and c in evidence)
    new_cited = " ".join(evidence[c] for c in citations if c.startswith("new:") and c in evidence)
    if old_cited and new_cited:
        m = _DIRECTION_RE.search(_QUOTE_RE.sub(" ", text))
        if m:
            before = entities.fact_strings(m.group("a") or m.group("a2") or "")
            after = entities.fact_strings(m.group("b") or m.group("b2") or "")
            if before - entities.fact_strings(old_cited) or after - entities.fact_strings(new_cited):
                problems.append("before/after values don't match the old/new text")

    # 3. quotes verbatim + lexical support
    for q in _QUOTE_RE.findall(text):
        if comparable(q) not in comparable(cited):
            problems.append("quotation not found verbatim in source")
            break
    words = content_words(_QUOTE_RE.sub(" ", text)) - _PARAPHRASE_ALLOW
    words = {w for w in words if not re.fullmatch(r"[\d£%.,]+", w)}
    cited_words = content_words(cited)
    # Allow simple inflection: "landlords" ~ "landlord", "registering" ~ "register".
    stems = {w[:6] for w in cited_words}
    supported = {w for w in words if w in cited_words or w[:6] in stems}
    support = len(supported) / len(words) if words else 1.0
    if support < MIN_SUPPORT:
        problems.append(f"low lexical support ({support:.0%}); unsupported: {', '.join(sorted(words - supported)[:6])}")

    # 4. advice (outside quotations: quoting the source's own "you must" is fine)
    adv = policy.advice_violations(_QUOTE_RE.sub(" ", text))
    if adv:
        problems.append(f"advice language: {', '.join(adv)}")

    return ClaimCheck(text, citations, not problems, problems, round(support, 3))


def score_summary(summary: dict, report: dict, threshold: float = 0.9) -> FaithfulnessResult:
    evidence = build_evidence(report)
    checks = []
    for c in summary.get("claims", []):
        checks.append(check_claim(c.get("text", ""), c.get("citations", []), evidence))
    # Headline: must be grounded in facts and free of advice, but may be a short label.
    head = summary.get("headline") or {}
    if head.get("text"):
        hc = check_claim(head["text"], head.get("citations", []), evidence)
        hc.problems = [p for p in hc.problems if not p.startswith("low lexical support")]
        hc.passed = not hc.problems
        checks.append(hc)
    if summary.get("deadline"):
        dl = summary["deadline"]
        cited = " ".join(evidence.get(c, "") for c in dl.get("citations", []))
        ok = dl.get("date", "") in entities.fact_strings(cited)
        checks.append(ClaimCheck(f"Deadline {dl.get('date')}", dl.get("citations", []), ok,
                                 [] if ok else ["deadline not found in cited text"]))
    if not checks:
        return FaithfulnessResult(0.0, [], False)
    s = sum(c.passed for c in checks) / len(checks)
    return FaithfulnessResult(round(s, 3), checks, s >= threshold)


def judge_with_llm(summary: dict, report: dict, client=None, model: str | None = None) -> list[dict]:
    """Optional LLM-as-judge entailment check, used by the offline eval harness only."""
    from pydantic import BaseModel

    from .config import settings

    class Verdict(BaseModel):
        claim_index: int
        entailed: bool
        explanation: str

    class Verdicts(BaseModel):
        verdicts: list[Verdict]

    import anthropic
    client = client or anthropic.Anthropic()
    evidence = build_evidence(report)
    claims = summary.get("claims", [])
    prompt = ("For each claim, decide whether the cited source paragraphs fully entail it. A claim that adds "
              "facts, implications or advice not stated in the source is NOT entailed.\n\n"
              + "\n".join(f"[{k}] {v}" for k, v in evidence.items()) + "\n\nClaims:\n"
              + "\n".join(f"{i}. {c['text']} (cites {', '.join(c.get('citations', []))})" for i, c in enumerate(claims)))
    resp = client.messages.parse(model=model or settings.llm_model, max_tokens=4000,
                                 output_config={"effort": "low"},
                                 messages=[{"role": "user", "content": prompt}], output_format=Verdicts)
    return [v.model_dump() for v in resp.parsed_output.verdicts] if resp.parsed_output else []
