"""Turning source documents into stable, citable paragraphs.

Every downstream step (diffing, summarising, citing) works on a list of
paragraphs shaped like ``{"id": "p12", "heading": "Deposits", "text": "..."}``.
Paragraph ids are positional within one version; citations always name the
version they point into, so they stay valid even when later versions shift.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Iterable

Paragraph = dict  # {"id": str, "heading": str, "text": str}

_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dt", "dd", "blockquote", "pre", "caption"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "nav", "svg", "noscript"}


class _BlockExtractor(HTMLParser):
    """Collects text per block element, tracking the nearest heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (tag, text)
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            # A nested block (e.g. <p> inside <li>) flushes the outer text first.
            self._flush()
            self._stack.append(tag)
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def _flush(self):
        text = clean_text("".join(self._buf))
        self._buf = []
        if text:
            tag = self._stack[-1] if self._stack else "p"
            self.blocks.append((tag, text))

    def close(self):
        super().close()
        self._flush()


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def html_to_paragraphs(html: str, heading: str = "", start: int = 0) -> list[Paragraph]:
    parser = _BlockExtractor()
    parser.feed(html or "")
    parser.close()
    paras: list[Paragraph] = []
    current_heading = heading
    for tag, text in parser.blocks:
        if tag in _HEADING_TAGS:
            current_heading = text
        paras.append({"id": f"p{start + len(paras) + 1}", "heading": current_heading, "text": text,
                      "kind": "heading" if tag in _HEADING_TAGS else "body"})
    return paras


def renumber(paras: Iterable[Paragraph]) -> list[Paragraph]:
    out = []
    for i, p in enumerate(paras, 1):
        q = dict(p)
        q["id"] = f"p{i}"
        out.append(q)
    return out


def comparable(text: str) -> str:
    """Form used to decide whether two paragraphs say the same thing.

    Drops case, punctuation and whitespace so that typo-level and formatting
    edits are recognised as cosmetic.
    """
    t = clean_text(text).lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^\w£$€%]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def content_hash(paras: list[Paragraph], extra: dict | None = None) -> str:
    payload = json.dumps({"p": [p["text"] for p in paras], "x": extra or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


_STOP = set("""a an and are as at be been but by for from has have if in into is it its of on or that the
their there these this to was were which will with you your we our they them he she his her not no
can may must should would could shall do does did than then also any all such per""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9£%]+(?:'[a-z]+)?", text.lower())]


def content_words(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in _STOP and len(t) > 2}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\"'])", text)
    return [p.strip() for p in parts if p.strip()]
