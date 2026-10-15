"""Extraction of the facts that matter most in a rule change.

Thresholds, rates, fees, periods and dates are what turn a wording tweak into
something a landlord or café owner has to act on, so they get first-class
treatment in both the change detector and the faithfulness checker.
"""
from __future__ import annotations

import re
from datetime import date, datetime

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MONTH_RE = "|".join(MONTHS)

MONEY_RE = re.compile(r"£\s?(\d[\d,]*(?:\.\d+)?)\s?(k|m|bn|million|billion)?\b", re.I)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|per ?cent)", re.I)
DURATION_RE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fourteen|twenty[- ]?eight|thirty)\s+"
    r"(working days?|calendar days?|days?|weeks?|months?|years?|hours?)\b", re.I)
DATE_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\s+(\d{{4}})\b", re.I)
MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_RE})\s+(\d{{4}})\b", re.I)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
COUNT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s+(?:or more\s+|or fewer\s+|or less\s+|or over\s+|and over\s+|and above\s+)?"
                      r"(people|persons|households|employees|staff|workers|properties|tenants|occupiers|storeys|"
                      r"bedrooms|rooms|units|vehicles|square metres|m2|kg|grams|litres|mg|tonnes|hours a week)\b", re.I)

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "twelve": 12, "fourteen": 14, "twenty eight": 28, "twentyeight": 28,
            "thirty": 30}
_MULT = {"k": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}

# Words that create, remove or soften legal duties.
DEONTIC = {
    "must": "obligation", "must not": "prohibition", "shall": "obligation", "shall not": "prohibition",
    "required to": "obligation", "is required": "obligation", "are required": "obligation",
    "have to": "obligation", "has to": "obligation", "need to": "obligation", "needs to": "obligation",
    "may not": "prohibition", "cannot": "prohibition", "can not": "prohibition", "prohibited": "prohibition",
    "not allowed": "prohibition", "it is an offence": "offence", "offence": "offence", "penalty": "penalty",
    "fine": "penalty", "civil penalty": "penalty", "liable": "penalty", "should": "recommendation",
    "may": "permission", "exempt": "exemption", "exemption": "exemption",
    "does not apply": "exemption", "no longer": "removal", "abolished": "removal", "repealed": "removal",
    "revoked": "removal", "mandatory": "obligation", "compulsory": "obligation", "optional": "permission",
}
_DEONTIC_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in DEONTIC), key=len, reverse=True)) + r")\b", re.I)

NATIONS = {"england": "england", "wales": "wales", "scotland": "scotland", "northern ireland": "northern_ireland"}
_NATION_RE = re.compile(r"\b(england|wales|scotland|northern ireland)\b", re.I)


def _num(s: str) -> float:
    s = s.lower().replace(",", "").replace("-", " ").strip()
    if s in _WORDNUM:
        return float(_WORDNUM[s])
    return float(s)


def money(text: str) -> list[float]:
    out = []
    for m in MONEY_RE.finditer(text):
        v = _num(m.group(1))
        if m.group(2):
            v *= _MULT[m.group(2).lower()]
        out.append(round(v, 2))
    return out


def percents(text: str) -> list[float]:
    return [float(m.group(1)) for m in PERCENT_RE.finditer(text)]


def durations(text: str) -> list[tuple[float, str]]:
    out = []
    for m in DURATION_RE.finditer(text):
        unit = m.group(2).lower().rstrip("s")
        unit = {"working day": "working_day", "calendar day": "day"}.get(unit, unit)
        out.append((_num(m.group(1)), unit))
    return out


def dates(text: str) -> list[date]:
    out: list[date] = []
    spans = []
    for m in DATE_RE.finditer(text):
        try:
            out.append(date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))))
            spans.append(m.span())
        except ValueError:
            pass
    for m in ISO_DATE_RE.finditer(text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in MONTH_YEAR_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        out.append(date(int(m.group(2)), MONTHS[m.group(1).lower()], 1))
    return out


def counts(text: str) -> list[tuple[float, str]]:
    return [(float(m.group(1)), m.group(2).lower()) for m in COUNT_RE.finditer(text)]


def deontic(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    # Strip dates first so the month "May" is not read as a permission.
    text = MONTH_YEAR_RE.sub(" ", DATE_RE.sub(" ", text))
    for m in _DEONTIC_RE.finditer(text):
        kind = DEONTIC[m.group(1).lower()]
        found[kind] = found.get(kind, 0) + 1
    return found


def nations(text: str) -> set[str]:
    return {NATIONS[m.group(1).lower()] for m in _NATION_RE.finditer(text)}


def facts(text: str) -> dict[str, list]:
    """All quantitative facts, normalised so two texts can be compared as sets."""
    return {
        "money": sorted(set(money(text))),
        "percent": sorted(set(percents(text))),
        "duration": sorted(set(durations(text))),
        "date": sorted(set(dates(text))),
        "count": sorted(set(counts(text))),
    }


def fact_strings(text: str) -> set[str]:
    """Flat, comparable string forms of every fact in ``text``."""
    f = facts(text)
    out = {f"£{v:g}" for v in f["money"]}
    out |= {f"{v:g}%" for v in f["percent"]}
    out |= {f"{v:g} {u}" for v, u in f["duration"]}
    out |= {d.isoformat() for d in f["date"]}
    out |= {f"{v:g} {u}" for v, u in f["count"]}
    return out


_DEADLINE_CUES = re.compile(
    r"(comes? into force|come into effect|takes? effect|from|by|before|no later than|deadline|until|starting|"
    r"applies from|will apply|effective|commenc\w+)", re.I)


def deadline(text: str, today: date | None = None) -> date | None:
    """Earliest future date in ``text`` that is introduced by a deadline cue."""
    today = today or datetime.utcnow().date()
    best = None
    for m in list(DATE_RE.finditer(text)) + list(MONTH_YEAR_RE.finditer(text)):
        window = text[max(0, m.start() - 60):m.start()]
        if not _DEADLINE_CUES.search(window):
            continue
        d = dates(m.group(0))
        if d and d[0] >= today and (best is None or d[0] < best):
            best = d[0]
    return best


def surface_facts(text: str) -> list[tuple[str, str]]:
    """(normalised, as-written) pairs for each fact, in order of appearance."""
    out: list[tuple[int, str, str]] = []
    for m in MONEY_RE.finditer(text):
        out.append((m.start(), next(iter(fact_strings(m.group(0))), m.group(0)), m.group(0).strip()))
    for m in PERCENT_RE.finditer(text):
        out.append((m.start(), f"{float(m.group(1)):g}%", m.group(0)))
    for m in DURATION_RE.finditer(text):
        out.append((m.start(), next(iter(fact_strings(m.group(0))), m.group(0)), m.group(0)))
    for m in DATE_RE.finditer(text):
        d = dates(m.group(0))
        if d:
            out.append((m.start(), d[0].isoformat(), m.group(0)))
    for m in COUNT_RE.finditer(text):
        out.append((m.start(), f"{float(m.group(1)):g} {m.group(2).lower()}", m.group(0)))
    seen, res = set(), []
    for _, norm, surf in sorted(out):
        if norm not in seen:
            seen.add(norm)
            res.append((norm, surf))
    return res
