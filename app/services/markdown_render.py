"""Safe Markdown -> HTML rendering for notes and wiki pages.

Rendering happens in two steps:

1. ``markdown`` converts the text to HTML (with tables, code blocks, a table
   of contents, ...).
2. ``nh3`` *sanitises* that HTML with an allow-list of tags and attributes.
   Markdown permits raw HTML, so without this step a user could inject
   ``<script>`` tags (stored XSS).

Wiki pages additionally support ``[[Page title]]`` links, which are turned
into links to the wiki page with the matching slug.
"""

from __future__ import annotations

import re
import unicodedata

import markdown
import nh3
from flask import url_for
from markupsafe import Markup

#: Markdown extensions: tables, fenced code, task lists, auto TOC anchors, ...
_EXTENSIONS = ["extra", "sane_lists", "toc", "nl2br"]

#: HTML tags that may survive sanitisation.
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl",
    "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input",
    "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
}
_ALLOWED_ATTRIBUTES = {
    "*": {"class", "id"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "input": {"type", "checked", "disabled"},  # task-list checkboxes
    "td": {"align"},
    "th": {"align"},
}

#: ``[[Some page]]`` or ``[[Some page|link text]]``
_WIKI_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def slugify(text: str) -> str:
    """Turn ``"Siemens S7 – Tips & Tricks"`` into ``"siemens-s7-tips-tricks"``."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s_]+", "-", text) or "page"


def _expand_wiki_links(text: str) -> str:
    """Replace ``[[Title]]`` with standard Markdown links to the wiki page."""

    def replace(match: re.Match) -> str:
        title = match.group(1).strip()
        label = (match.group(2) or title).strip()
        href = url_for("wiki.view", slug=slugify(title))
        return f"[{label}]({href})"

    return _WIKI_LINK.sub(replace, text)


def render_markdown(text: str | None, wiki_links: bool = False) -> Markup:
    """Render ``text`` to sanitised HTML that can be inserted in a template."""
    if not text:
        return Markup("")
    if wiki_links:
        # The WYSIWYG editor escapes square brackets (``\[\[Page\]\]``);
        # undo that so wiki links typed in WYSIWYG mode also work.
        text = text.replace("\\[\\[", "[[").replace("\\]\\]", "]]")
        text = _expand_wiki_links(text)
    html = markdown.markdown(text, extensions=_EXTENSIONS, output_format="html")
    clean = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto", "data"},
        link_rel="noopener noreferrer",
    )
    return Markup(clean)
