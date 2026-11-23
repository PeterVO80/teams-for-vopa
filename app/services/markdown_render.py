"""Safe Markdown -> HTML rendering for notes and wiki pages.

Rendering happens in two steps:

1. ``markdown-it-py`` converts the text to HTML, with the same Markdown
   dialect as the WYSIWYG editor (Toast UI writes CommonMark with GitHub
   extensions): lists right after a paragraph, task lists, tables,
   strikethrough, links typed as plain addresses, ... A different dialect
   would show parts of a page as raw Markdown (``* item``, ``[x]``, ``~~``).
2. ``nh3`` *sanitises* that HTML with an allow-list of tags and attributes.
   Markdown permits raw HTML, so without this step a user could inject
   ``<script>`` tags (stored XSS).

Wiki pages additionally support ``[[Page title]]`` links. They point to the
``wiki.resolve`` view, which looks the title up at click time - first in the
wiki of the page containing the link (a project wiki), then in the general
wiki - so links keep working when pages are renamed or moved.
"""

from __future__ import annotations

import re
import unicodedata

import nh3
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from flask import url_for
from markupsafe import Markup

#: CommonMark + the GitHub extensions the editor uses. ``breaks``: a single
#: line break in the text is a line break on the page (as in the editor).
_MD = (
    MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": True})
    .enable(["table", "strikethrough", "linkify"])
    .use(tasklists_plugin)
    .use(anchors_plugin, min_level=1, max_level=4)
)

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
    "code": {"class"},
    "td": {"align"},
    "th": {"align"},
}

#: ``[[Some page]]`` or ``[[Some page|link text]]``
_WIKI_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def slugify(text: str) -> str:
    """Turn ``"Siemens S7 – Tips & Tricks"`` into ``"siemens-s7-tips-tricks"``.

    Slashes separate words (``"PRJ/2026/0001"`` -> ``"prj-2026-0001"``).
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.replace("/", " ")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s_]+", "-", text) or "page"


def _expand_wiki_links(text: str, project_id: int | None = None) -> str:
    """Replace ``[[Title]]`` with standard Markdown links to the wiki page.

    ``project_id`` is the project wiki the text belongs to (None = general wiki).
    """

    def replace(match: re.Match) -> str:
        title = match.group(1).strip()
        label = (match.group(2) or title).strip()
        href = url_for("wiki.resolve", title=title, project=project_id)
        return f"[{label}](<{href}>)"

    return _WIKI_LINK.sub(replace, text)


def render_markdown(
    text: str | None, wiki_links: bool = False, wiki_project_id: int | None = None
) -> Markup:
    """Render ``text`` to sanitised HTML that can be inserted in a template.

    :param wiki_links: expand ``[[Page title]]`` links (wiki pages only).
    :param wiki_project_id: project wiki the text belongs to, so its links
        prefer pages of the same project.
    """
    if not text:
        return Markup("")
    if wiki_links:
        # The WYSIWYG editor escapes square brackets (``\[\[Page\]\]``);
        # undo that so wiki links typed in WYSIWYG mode also work.
        text = text.replace("\\[\\[", "[[").replace("\\]\\]", "]]")
        text = _expand_wiki_links(text, wiki_project_id)
    html = _MD.render(text)
    clean = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto", "data"},
        link_rel="noopener noreferrer",
    )
    return Markup(clean)
