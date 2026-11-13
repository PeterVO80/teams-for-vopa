"""Wiki: hierarchical Markdown knowledge base with full revision history."""

import sqlalchemy as sa
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import WikiPageForm, none_if_zero
from ..models import WikiPage, WikiRevision
from ..services.markdown_render import slugify
from . import protected_blueprint

bp = protected_blueprint("wiki", __name__)

#: Slugs that would collide with fixed routes such as ``/wiki/new``.
RESERVED_SLUGS = {"new", "search"}


def _unique_slug(title: str, page_id: int | None = None) -> str:
    """Slug for ``title`` that is not used by another page (``-2``, ``-3`` ...)."""
    base = slugify(title)
    if base in RESERVED_SLUGS:
        base = f"{base}-page"
    slug, counter = base, 2
    while db.session.scalar(
        sa.select(WikiPage.id).where(WikiPage.slug == slug, WikiPage.id != (page_id or 0))
    ):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _parent_choices(exclude: WikiPage | None = None) -> list[tuple[int, str]]:
    """Indented list of all pages for the 'parent page' select.

    The page itself and its descendants are excluded, otherwise a user could
    create a cycle (a page that is its own grandparent).
    """
    excluded = set()
    if exclude is not None and exclude.id:
        excluded = exclude.descendant_ids() | {exclude.id}
    roots = db.session.scalars(
        sa.select(WikiPage).where(WikiPage.parent_id.is_(None)).order_by(WikiPage.title)
    ).all()

    choices = [(0, "- hoofdniveau -")]

    def walk(pages, depth):
        for page in pages:
            if page.id in excluded:
                continue
            choices.append((page.id, "   " * depth + page.title))
            walk(page.children, depth + 1)

    walk(roots, 0)
    return choices


@bp.route("/")
def index():
    roots = db.session.scalars(
        sa.select(WikiPage).where(WikiPage.parent_id.is_(None)).order_by(WikiPage.title)
    ).all()
    recent = db.session.scalars(sa.select(WikiPage).order_by(WikiPage.updated_at.desc()).limit(10)).all()
    return render_template("wiki/index.html", roots=roots, recent=recent)


@bp.route("/search")
def search():
    term = request.args.get("q", "").strip()
    results = []
    if term:
        pattern = f"%{term}%"
        results = db.session.scalars(
            sa.select(WikiPage)
            .where(sa.or_(WikiPage.title.ilike(pattern), WikiPage.content.ilike(pattern)))
            .order_by(WikiPage.title)
            .limit(100)
        ).all()
    return render_template("wiki/search.html", results=results, term=term)


@bp.route("/new", methods=["GET", "POST"])
def create():
    form = WikiPageForm()
    form.parent_id.choices = _parent_choices()
    if not form.is_submitted():
        # Following a [[link]] to a missing page pre-fills its title.
        form.title.data = request.args.get("title", "")
        form.parent_id.data = request.args.get("parent", 0, type=int)

    if form.validate_on_submit():
        page = WikiPage(
            title=form.title.data,
            slug=_unique_slug(form.title.data),
            content=form.content.data or "",
            parent_id=none_if_zero(form.parent_id.data),
            updated_by_id=current_user.id,
        )
        db.session.add(page)
        db.session.commit()
        flash("Pagina aangemaakt.", "success")
        return redirect(url_for("wiki.view", slug=page.slug))
    return render_template("wiki/edit.html", form=form, page=None, cancel_url=url_for("wiki.index"))


@bp.route("/<slug>")
def view(slug: str):
    page = db.session.scalar(sa.select(WikiPage).filter_by(slug=slug))
    if page is None:
        # Unknown slug (e.g. a [[link]] to a page that does not exist yet):
        # offer to create it instead of showing a plain 404.
        return render_template("wiki/missing.html", slug=slug), 404
    return render_template("wiki/view.html", page=page)


@bp.route("/<slug>/edit", methods=["GET", "POST"])
def edit(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    form = WikiPageForm(obj=page)
    form.parent_id.choices = _parent_choices(exclude=page)
    if not form.is_submitted():
        form.parent_id.data = page.parent_id or 0

    if form.validate_on_submit():
        if form.title.data != page.title or (form.content.data or "") != page.content:
            # Store the *previous* version before overwriting it.
            page.revisions.append(
                WikiRevision(title=page.title, content=page.content, author_id=page.updated_by_id)
            )
        if form.title.data != page.title:
            page.slug = _unique_slug(form.title.data, page.id)
        page.title = form.title.data
        page.content = form.content.data or ""
        page.parent_id = none_if_zero(form.parent_id.data)
        page.updated_by_id = current_user.id
        db.session.commit()
        flash("Pagina opgeslagen.", "success")
        return redirect(url_for("wiki.view", slug=page.slug))
    return render_template(
        "wiki/edit.html", form=form, page=page, cancel_url=url_for("wiki.view", slug=page.slug)
    )


@bp.route("/<slug>/history")
def history(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    return render_template("wiki/history.html", page=page)


@bp.route("/revision/<int:revision_id>")
def revision(revision_id: int):
    return render_template("wiki/revision.html", revision=db.get_or_404(WikiRevision, revision_id))


@bp.route("/revision/<int:revision_id>/restore", methods=["POST"])
def restore(revision_id: int):
    """Make an old revision the current content (the current one is kept too)."""
    rev = db.get_or_404(WikiRevision, revision_id)
    page = rev.page
    page.revisions.append(WikiRevision(title=page.title, content=page.content, author_id=page.updated_by_id))
    if rev.title != page.title:
        page.slug = _unique_slug(rev.title, page.id)
    page.title, page.content, page.updated_by_id = rev.title, rev.content, current_user.id
    db.session.commit()
    flash("Oude versie teruggezet.", "success")
    return redirect(url_for("wiki.view", slug=page.slug))


@bp.route("/<slug>/delete", methods=["POST"])
def delete(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    if page.children:
        flash("Deze pagina heeft subpagina's. Verplaats of verwijder die eerst.", "danger")
        return redirect(url_for("wiki.view", slug=page.slug))
    db.session.delete(page)
    db.session.commit()
    flash("Pagina verwijderd.", "success")
    return redirect(url_for("wiki.index"))


@bp.route("/<slug>/download")
def download(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    return current_app.response_class(
        f"# {page.title}\n\n{page.content}",
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{page.slug}.md"'},
    )
