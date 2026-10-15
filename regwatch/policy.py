"""Responsible-AI boundaries: what RegWatch will and won't say.

1. **Not legal advice.** Every digest, page and summary carries the
   disclaimer. Summaries describe what the source says. They never tell the
   reader what to do.
2. **Refusal policy for ambiguous cases.** If whether a change applies
   depends on facts we don't hold, or on legal interpretation, we say so
   explicitly instead of guessing "this affects you". The item is still
   shown, labelled "Check whether this applies to you", with a pointer to
   appropriate help.
3. **Proposals are not law.** Consultations and draft guidance are labelled
   as proposals.
4. **Cite or drop.** A summary sentence without a citation to a source
   paragraph is not shown.
"""
from __future__ import annotations

import re

DISCLAIMER = ("RegWatch UK summarises public changes on GOV.UK and legislation.gov.uk. It is not legal, tax "
              "or financial advice. Always read the linked source, and speak to a qualified adviser before "
              "acting.")

BAND_LABELS = {
    "likely": "Likely to affect you",
    "possible": "May affect you",
    "check": "Check whether this applies to you",
}

# Wording that signals the outcome depends on judgement or individual facts.
_AMBIGUOUS = re.compile(
    r"\b(reasonable|reasonably|where appropriate|as appropriate|in certain circumstances|in some cases|"
    r"case[- ]by[- ]case|depending on|may be required|at the discretion|discretionary|subject to|"
    r"exceptional circumstances|it is likely that|we expect|further guidance will|to be confirmed|tbc)\b", re.I)
_PROPOSAL = re.compile(r"\b(consultation|consulting on|call for evidence|draft|proposed|proposal|proposes|"
                       r"we are seeking views|white paper|green paper)\b", re.I)
_BILL = re.compile(r"\bBill\b")  # a Bill before Parliament (case-sensitive: not an "electricity bill")

# Phrases a summary must never contain: they turn a description into advice.
ADVICE_PATTERNS = re.compile(
    r"\b(you should|you must|you need to|you have to|we recommend|we advise|our advice|make sure you|"
    r"you are (?:now )?required|this means you|you will need|ensure (?:that )?you|don't worry|no action needed|"
    r"you are not affected|you're not affected|you are affected|guaranteed)\b", re.I)

REFUSAL_TEXT = ("We can't tell from your profile whether this applies to you. It depends on details we "
                "don't hold, or on how the rule is interpreted. Read the source. If you're unsure, ask an "
                "adviser (for example an accountant, solicitor, Citizens Advice, or your trade body).")
PROPOSAL_TEXT = "This is a proposal or consultation, not yet law."
LOW_CONFIDENCE_TEXT = ("We couldn't produce a summary we're confident is accurate, so we're showing the "
                       "changed text as published instead.")


def assess(change: dict, band: str) -> dict:
    """Decide labels and refusals for one ranked change."""
    rep = change.get("report") or {}
    text = " ".join([change.get("title", ""), rep.get("change_note") or ""] +
                    [p.get("new_text") or p.get("old_text") or "" for p in rep.get("paragraphs", [])[:10]])
    notes: list[str] = []
    proposal = bool(_PROPOSAL.search(change.get("title", "") + " " + (change.get("doc_type") or ""))) \
        or bool(_BILL.search(change.get("title", ""))) \
        or (change.get("doc_type") in {"consultation", "open_consultation", "closed_consultation", "call_for_evidence"})
    if proposal:
        notes.append(PROPOSAL_TEXT)
    ambiguous = bool(_AMBIGUOUS.search(text)) or change.get("kind") == "noted"
    refuse = band == "check" or (ambiguous and band != "likely")
    if refuse:
        notes.append(REFUSAL_TEXT)
    if (change.get("summary_method") == "extractive_fallback"):
        notes.append(LOW_CONFIDENCE_TEXT)
    return {"label": BAND_LABELS.get(band, band), "proposal": proposal, "refuse_applicability": refuse,
            "notes": notes}


def advice_violations(text: str) -> list[str]:
    return [m.group(0) for m in ADVICE_PATTERNS.finditer(text)]
