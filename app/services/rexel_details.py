"""Extended description and specifications from the Rexel product page.

The Rexel price list (DBF) only carries a short, truncated description such as
``"SIMATIC DP, CPU 1510SP F-1 PN"``. Every article in it also has a link to
its product page in Rexel Netstore (field ``URL``). That page is public (no
login needed) and shows:

* the **extended description** ("Uitgebreide omschrijving"), e.g.
  ``"SIMATIC DP, CPU 1510SP F-1 PN FOR ET 200SP, CENTRAL PROCESSING UNIT WITH
  WORK MEMORY 300 KB ..."`` (Rexel itself cuts it off at 250 characters);
* the **specifications** ("Kenmerken"), e.g. ``Breedte: 100 mm``.

The extended description becomes the article description; the
specifications are stored as JSON on the article and shown on its page.

How the page is reached
-----------------------
The link answers with a small page whose JavaScript POSTs a form to a
servlet, which redirects (302) to the product detail page. :func:`fetch_page`
does the same with a cookie-aware opener (urllib follows a 302 after a POST
with a GET, like a browser).

Fetching takes 1-2 seconds per article, so imported articles are queued
(``details_status = "pending"``) and processed in small batches driven by the
browser (:func:`process_pending`, ``static/js/article_details.js``): large
imports never hit a request timeout, and pending work survives a restart.
"""

from __future__ import annotations

import html as html_lib
import http.cookiejar
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import sqlalchemy as sa

from ..extensions import db
from ..models import Article
from ..models.base import utcnow

PENDING, DONE, NOT_FOUND, ERROR = "pending", "done", "not_found", "error"

NETSTORE = "https://netstore.rexel.be"
#: Identify ourselves honestly; keep the load on Rexel's site small.
USER_AGENT = "VOPA-Teams/1.0 (article details)"
HTTP_TIMEOUT = 20
#: Articles fetched per batch request, and how many at the same time.
BATCH_SIZE = 6
PARALLEL = 3


@dataclass
class Details:
    found: bool
    description: str | None = None
    specifications: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None


# ------------------------------------------------------------------ parsing
def _text(fragment: str) -> str:
    """HTML fragment -> clean single-line text (tags removed, entities decoded)."""
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_detail_page(page: str) -> Details:
    """Read the extended description and the specifications from a product page."""
    description = re.search(r'<div id="description">.*?<p>(.*?)</p>', page, re.S | re.I)
    description = _text(description.group(1)) if description else ""

    specifications: list[tuple[str, str]] = []
    series = re.search(r"Serienaam(?:&nbsp;|\s)*:(?:&nbsp;|\s)*</td>\s*<td[^>]*>(.*?)</td>", page, re.S | re.I)
    if series and _text(series.group(1)):
        specifications.append(("Serie", _text(series.group(1))))
    for label, value in re.findall(r'<td class="label">(.*?)</td>\s*<td[^>]*>(.*?)</td>', page, re.S | re.I):
        label, value = _text(label).rstrip(" :"), _text(value)
        if label and value:
            specifications.append((label, value))

    if not description and not specifications:
        return Details(found=False)
    return Details(found=True, description=description or None, specifications=specifications)


def item_code(url: str | None) -> str | None:
    """Rexel's item code (``E5808005``) from a Netstore product link."""
    if not url or "netstore.rexel.be" not in url.lower():
        return None
    codes = parse_qs(urlparse(url).query).get("itemcode")
    return codes[0].strip() if codes and codes[0].strip() else None


def supports(article: Article) -> bool:
    """True when the article has a Rexel Netstore link to read details from."""
    return item_code(article.url) is not None


# ----------------------------------------------------------------- fetching
def fetch_page(code: str) -> str:
    """Open the Netstore product page of item ``code`` and return its HTML."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", USER_AGENT), ("Accept-Language", "nl-BE,nl;q=0.9")]
    start = opener.open(f"{NETSTORE}/NS/Start?appl=netstore&itemcode={code}&LANGUAGE_CODE=NL", timeout=HTTP_TIMEOUT)
    page = start.read().decode("utf-8", errors="replace")
    action = re.search(r"redirect\('([^']+)'\)", page)
    if not action:  # already the detail page (should Rexel ever skip the extra step)
        return page
    request = urllib.request.Request(NETSTORE + action.group(1), data=b"", method="POST")
    with opener.open(request, timeout=HTTP_TIMEOUT) as response:
        return response.read(2_000_000).decode("utf-8", errors="replace")


def fetch(article: Article) -> Details:
    """Fetch and parse the details of one article (no database changes)."""
    code = item_code(article.url)
    if code is None:
        return Details(found=False, error="Geen Rexel-link bij dit artikel.")
    try:
        return parse_detail_page(fetch_page(code))
    except urllib.error.HTTPError as exc:
        return Details(found=False, error=f"Rexel antwoordde met HTTP {exc.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Details(found=False, error=f"Rexel niet bereikbaar ({exc}).")


def apply(article: Article, details: Details) -> None:
    """Store fetched details on the article (the caller commits).

    The extended description replaces the short one; when Rexel has no
    extended description the article keeps its current description.
    """
    article.details_fetched_at = utcnow()
    if details.found:
        if details.description:
            article.description = details.description[:300]
        if details.specifications:
            article.specifications = json.dumps(details.specifications, ensure_ascii=False)
        article.details_status = DONE
    else:
        article.details_status = ERROR if details.error else NOT_FOUND


# ------------------------------------------------------------ batch work
def queue(articles) -> int:
    """Mark articles (with a Rexel link) to be fetched; returns how many."""
    count = 0
    for article in articles:
        if supports(article):
            article.details_status = PENDING
            count += 1
    return count


def pending_count() -> int:
    return db.session.scalar(sa.select(sa.func.count(Article.id)).where(Article.details_status == PENDING))


def process_pending(batch_size: int = BATCH_SIZE) -> list[dict]:
    """Fetch the next batch of pending articles (a few at a time) and save them."""
    articles = db.session.scalars(
        sa.select(Article).where(Article.details_status == PENDING).order_by(Article.id).limit(batch_size)
    ).all()
    if not articles:
        return []
    # Network-bound: fetch concurrently. The threads only read plain
    # attributes; the database is updated afterwards in this thread.
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(articles))) as pool:
        results = list(pool.map(fetch, articles))
    summary = []
    for article, details in zip(articles, results):
        apply(article, details)
        summary.append({"article_number": article.article_number, "status": article.details_status,
                        "description": article.description, "error": details.error})
    db.session.commit()
    return summary
