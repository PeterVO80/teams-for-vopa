"""Markdown -> ReportLab flowables (the accompanying text of a quote on the PDF).

The text is written in the WYSIWYG editor (Toast UI), which stores Markdown.
It is parsed with the same markdown-it setup as the web pages
(services/markdown_render.py) and turned into PDF building blocks:

* paragraphs, headings (#, ##, ###), line breaks;
* **bold**, *italic*, ~~strikethrough~~, `code`, links;
* bullet and numbered lists (nested), task lists (checked / unchecked box);
* quotes (indented, italic), horizontal lines, tables, code blocks.

Raw HTML tags and images are left out (the text inside HTML is kept). All text is escaped for ReportLab's
mini-markup, so the user's text can't break the PDF.
"""

from __future__ import annotations

import html
import re
from xml.sax.saxutils import escape

import nh3
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, Spacer, Table, TableStyle

from .markdown_render import _MD

#: Task list boxes in a font every PDF reader has (symbol fonts aren't embedded).
_CHECKED = '<font face="Courier-Bold">[x]</font>'
_UNCHECKED = '<font face="Courier">[&nbsp;]</font>'


def _inline(token) -> str:
    """ReportLab markup for an ``inline`` token (bold, italic, links ...)."""
    out = []
    links = []                                   # per open link: was a clickable <a> written?
    for child in token.children or []:
        kind = child.type
        if kind == "text":
            out.append(escape(child.content))
        elif kind in ("softbreak", "hardbreak"):
            out.append("<br/>")
        elif kind == "strong_open":
            out.append("<b>")
        elif kind == "strong_close":
            out.append("</b>")
        elif kind == "em_open":
            out.append("<i>")
        elif kind == "em_close":
            out.append("</i>")
        elif kind == "s_open":
            out.append("<strike>")
        elif kind == "s_close":
            out.append("</strike>")
        elif kind == "code_inline":
            out.append(f'<font face="Courier">{escape(child.content)}</font>')
        elif kind == "link_open":
            href = child.attrs.get("href", "") or ""
            clickable = bool(re.match(r"^(https?:|mailto:)", href))
            if clickable:
                out.append(f'<a href="{escape(href, {chr(34): "&quot;"})}" color="#1F4E79">')
            links.append(clickable)
        elif kind == "link_close":
            if links and links.pop():
                out.append("</a>")
        elif kind == "html_inline" and "checkbox" in child.content:
            out.append(_CHECKED if "checked" in child.content else _UNCHECKED)
        elif kind == "image":
            out.append(escape(child.content or ""))           # the alt text
        # other html_inline: left out
    return "".join(out)


def _styles(base: ParagraphStyle) -> dict[str, ParagraphStyle]:
    return {
        "p": ParagraphStyle("md-p", parent=base, spaceAfter=4),
        "h1": ParagraphStyle("md-h1", parent=base, fontName="Helvetica-Bold", fontSize=base.fontSize + 3,
                             leading=base.leading + 4, spaceBefore=4, spaceAfter=3),
        "h2": ParagraphStyle("md-h2", parent=base, fontName="Helvetica-Bold", fontSize=base.fontSize + 2,
                             leading=base.leading + 3, spaceBefore=3, spaceAfter=2),
        "h3": ParagraphStyle("md-h3", parent=base, fontName="Helvetica-Bold", spaceBefore=2, spaceAfter=2),
        "quote": ParagraphStyle("md-q", parent=base, fontName="Helvetica-Oblique", leftIndent=4 * mm,
                                textColor=colors.HexColor("#555555"), spaceAfter=4),
        "code": ParagraphStyle("md-code", parent=base, fontName="Courier", fontSize=base.fontSize - 1,
                               leftIndent=3 * mm, backColor=colors.HexColor("#F4F4F4"), spaceAfter=4),
        "li": ParagraphStyle("md-li", parent=base, spaceAfter=1),
        "cell": ParagraphStyle("md-cell", parent=base, fontSize=base.fontSize - 0.5),
    }


def _blocks(tokens, i: int, end: str | None, st: dict, quote: bool = False) -> tuple[list, int]:
    """Flowables for tokens[i:] up to the closing token ``end``; returns (flowables, next index)."""
    flow = []
    while i < len(tokens):
        token = tokens[i]
        kind = token.type
        if end and kind == end:
            return flow, i + 1
        if kind == "paragraph_open":
            inline = tokens[i + 1]
            style = st["quote"] if quote else st["p"]
            if not token.hidden:                             # (tight list items: no paragraph spacing)
                flow.append(Paragraph(_inline(inline), style))
            else:
                flow.append(Paragraph(_inline(inline), st["li"]))
            i += 3
        elif kind == "heading_open":
            level = min(int(token.tag[1]), 3)
            flow.append(Paragraph(_inline(tokens[i + 1]), st[f"h{level}"]))
            i += 3
        elif kind in ("bullet_list_open", "ordered_list_open"):
            items, i = _list(tokens, i + 1, kind.replace("_open", "_close"), st)
            numbered = kind == "ordered_list_open"
            start = int(token.attrs.get("start", 1) or 1) if numbered else 1
            flow.append(ListFlowable(items, bulletType="1" if numbered else "bullet", start=start if numbered else None,
                                     bulletFormat="%s." if numbered else None,
                                     leftIndent=5 * mm, bulletFontSize=st["li"].fontSize,
                                     bulletFontName="Helvetica", spaceAfter=3))
        elif kind == "blockquote_open":
            inner, i = _blocks(tokens, i + 1, "blockquote_close", st, quote=True)
            flow += inner
        elif kind == "hr":
            flow.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#999999"),
                                   spaceBefore=2, spaceAfter=4))
            i += 1
        elif kind in ("fence", "code_block"):
            code = escape(token.content.rstrip("\n")).replace("\n", "<br/>").replace("  ", "&nbsp;&nbsp;")
            flow.append(Paragraph(code, st["code"]))
            i += 1
        elif kind == "table_open":
            table, i = _table(tokens, i + 1, st)
            flow.append(table)
        elif kind == "html_block":
            # Raw HTML: keep only its text (script / style content is dropped), as on the web page.
            plain = nh3.clean(token.content, tags=set()).strip()
            if plain:
                flow.append(Paragraph(escape(html.unescape(plain)).replace("\n", "<br/>"), st["p"]))
            i += 1
        else:
            i += 1
    return flow, i


def _list(tokens, i: int, end: str, st: dict) -> tuple[list, int]:
    items = []
    while i < len(tokens):
        token = tokens[i]
        if token.type == end:
            return items, i + 1
        if token.type == "list_item_open":
            content, i = _blocks(tokens, i + 1, "list_item_close", st)
            task = "task-list-item" in (token.attrs.get("class") or "")
            # Task list items show their own box instead of a bullet.
            items.append(ListItem(content or [Spacer(1, 0)], value="") if task else ListItem(content or [Spacer(1, 0)]))
        else:
            i += 1
    return items, i


def _table(tokens, i: int, st: dict) -> tuple[Table, int]:
    rows, row, header_rows = [], None, 0
    in_head = False
    while i < len(tokens):
        kind = tokens[i].type
        if kind == "table_close":
            i += 1
            break
        if kind == "thead_open":
            in_head = True
        elif kind == "thead_close":
            in_head = False
        elif kind == "tr_open":
            row = []
        elif kind == "tr_close":
            rows.append(row)
            if in_head:
                header_rows += 1
        elif kind == "inline":
            text = _inline(tokens[i])
            row.append(Paragraph(f"<b>{text}</b>" if in_head else text, st["cell"]))
        i += 1
    width = max((len(r) for r in rows), default=1)
    rows = [r + [""] * (width - len(r)) for r in rows] or [[""]]
    table = Table(rows, hAlign="LEFT")
    commands = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if header_rows:
        commands.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#EEF2F7")))
    table.setStyle(TableStyle(commands))
    return table, i


def markdown_flowables(text: str | None, base: ParagraphStyle) -> list:
    """The Markdown ``text`` as ReportLab flowables in the font of ``base``."""
    if not text or not text.strip():
        return []
    flow, _ = _blocks(_MD.parse(text), 0, None, _styles(base))
    return flow
