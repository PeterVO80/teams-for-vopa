"""Wiki: hierarchical Markdown knowledge base with full revision history.

There is one **general wiki** (company-wide knowledge) and every project can
have its **own wiki** (knowledge specific to that installation). Technically
both are the same pages; a page belongs to a project wiki when its
``project_id`` is set. A page and its sub pages always live in the same wiki.

Links between pages are written as ``[[Page title]]``. They are resolved by
:func:`resolve` at click time: first in the wiki of the current page, then in
the general wiki. Links to pages that do not exist yet lead to the "new page"
form with the title already filled in.
"""

import sqlalchemy as sa
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import WikiPageForm, none_if_zero
from ..models import Project, ProjectStatus, WikiPage, WikiRevision
from ..services.markdown_render import slugify
from ..utils import int_arg
from . import protected_blueprint

bp = protected_blueprint("wiki", __name__)

#: Slugs that would collide with fixed routes such as ``/wiki/new``.
RESERVED_SLUGS = {"new", "search", "go", "project", "revision"}


# ------------------------------------------------------------------ helpers
def _unique_slug(title: str, project: Project | None, page_id: int | None = None) -> str:
    """URL slug for a page, unique over all wikis.

    Project pages are prefixed with the project code, which keeps their URLs
    readable and avoids clashes between projects that all have e.g. an
    "I/O-lijst" page: ``/wiki/prj-2026-0001-io-lijst``.
    """
    base = slugify(f"{project.code} {title}" if project else title)
    if base in RESERVED_SLUGS:
        base = f"{base}-page"
    slug, counter = base, 2
    while db.session.scalar(
        sa.select(WikiPage.id).where(WikiPage.slug == slug, WikiPage.id != (page_id or 0))
    ):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _wiki_choices(current_project_id: int | None) -> list[tuple[int, str]]:
    """Options for the "Wiki" select: the general wiki + every project.

    Closed projects are only listed when the page already belongs to one,
    so new pages are not added to finished projects by accident.
    """
    projects = db.session.scalars(
        sa.select(Project)
        .where(sa.or_(Project.status != ProjectStatus.CLOSED, Project.id == (current_project_id or 0)))
        .order_by(Project.code.desc())
    )
    return [(0, "Algemene wiki")] + [(p.id, f"Projectwiki {p.display_name}") for p in projects]


def _parent_choices(exclude: WikiPage | None = None) -> list[tuple]:
    """Indented list of all pages, of all wikis, for the "parent page" select.

    Each option carries a ``data-project`` attribute; ``static/js/wiki.js``
    uses it to only show the pages of the wiki that is selected in the form.
    The page itself and its descendants are left out, otherwise a user could
    create a cycle (a page that is its own grandparent).
    """
    excluded = set()
    if exclude is not None and exclude.id:
        excluded = exclude.descendant_ids() | {exclude.id}
    roots = db.session.scalars(
        sa.select(WikiPage).where(WikiPage.parent_id.is_(None)).order_by(WikiPage.title)
    ).all()

    choices: list[tuple] = [(0, "- hoofdniveau -", {"data-project": "any"})]

    def walk(pages, depth):
        for page in pages:
            if page.id in excluded:
                continue
            choices.append(
                (page.id, "   " * depth + page.title, {"data-project": str(page.project_id or 0)})
            )
            walk(page.children, depth + 1)

    walk(roots, 0)
    return choices


def _prepare_form(form: WikiPageForm, page: WikiPage | None) -> None:
    form.project_id.choices = _wiki_choices(page.project_id if page else None)
    form.parent_id.choices = _parent_choices(exclude=page)


def _validate_parent(form: WikiPageForm) -> WikiPage | None | bool:
    """Return the chosen parent page (or None), or False when it is invalid.

    A parent must be in the same wiki as the page itself.
    """
    if not form.parent_id.data:
        return None
    parent = db.session.get(WikiPage, form.parent_id.data)
    if parent is None or (parent.project_id or 0) != form.project_id.data:
        form.parent_id.errors.append("De bovenliggende pagina moet in dezelfde wiki staan.")
        return False
    return parent


def _wiki_url(project_id: int | None) -> str:
    """URL of the start page of the general wiki or of a project wiki."""
    return url_for("wiki.project_index", project_id=project_id) if project_id else url_for("wiki.index")


def _roots(project_id: int | None) -> list[WikiPage]:
    """Top-level pages of one wiki (``None`` = the general wiki)."""
    in_wiki = WikiPage.project_id.is_(None) if project_id is None else WikiPage.project_id == project_id
    return db.session.scalars(
        sa.select(WikiPage).where(WikiPage.parent_id.is_(None), in_wiki).order_by(WikiPage.title)
    ).all()


# -------------------------------------------------------------- overviews
@bp.route("/")
def index():
    """Start page of the general wiki, with links to the project wikis."""
    recent = db.session.scalars(
        sa.select(WikiPage).where(WikiPage.project_id.is_(None)).order_by(WikiPage.updated_at.desc()).limit(10)
    ).all()
    # Projects that have a wiki, with their number of pages.
    project_wikis = db.session.execute(
        sa.select(Project, sa.func.count(WikiPage.id))
        .join(WikiPage, WikiPage.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.code.desc())
    ).all()
    return render_template(
        "wiki/index.html", roots=_roots(None), recent=recent, project_wikis=project_wikis
    )


@bp.route("/project/<int:project_id>")
def project_index(project_id: int):
    """Start page of the wiki of one project."""
    project = db.get_or_404(Project, project_id)
    recent = db.session.scalars(
        sa.select(WikiPage).where(WikiPage.project_id == project.id).order_by(WikiPage.updated_at.desc()).limit(10)
    ).all()
    return render_template("wiki/project.html", project=project, roots=_roots(project.id), recent=recent)


@bp.route("/search")
def search():
    """Search all wikis, or only one project wiki with ``?project=<id>``."""
    term = request.args.get("q", "").strip()
    project = db.session.get(Project, int_arg("project")) if int_arg("project") else None
    results = []
    if term:
        pattern = f"%{term}%"
        query = sa.select(WikiPage).where(sa.or_(WikiPage.title.ilike(pattern), WikiPage.content.ilike(pattern)))
        if project:
            query = query.where(WikiPage.project_id == project.id)
        results = db.session.scalars(query.order_by(WikiPage.title).limit(100)).all()
    return render_template("wiki/search.html", results=results, term=term, project=project)


@bp.route("/go")
def resolve():
    """Follow a ``[[Page title]]`` link.

    Looks for the title (case-insensitive) in the wiki of the page that
    contains the link (``?project=``), then in the general wiki, and finally
    by slug (links written before project wikis existed). When nothing is
    found the user can create the page right away.
    """
    title = request.args.get("title", "").strip()
    if not title:
        abort(404)
    project_id = int_arg("project")
    by_title = sa.func.lower(WikiPage.title) == title.lower()
    for scope in ([WikiPage.project_id == project_id] if project_id else []) + [WikiPage.project_id.is_(None)]:
        page = db.session.scalar(sa.select(WikiPage).where(by_title, scope).limit(1))
        if page:
            return redirect(url_for("wiki.view", slug=page.slug))
    page = db.session.scalar(sa.select(WikiPage).filter_by(slug=slugify(title)))
    if page:
        return redirect(url_for("wiki.view", slug=page.slug))
    return redirect(url_for("wiki.create", title=title, project=project_id))


# ------------------------------------------------------------ create / edit
@bp.route("/new", methods=["GET", "POST"])
def create():
    """New page. ``?project=``, ``?parent=`` and ``?title=`` pre-fill the form."""
    form = WikiPageForm()
    _prepare_form(form, None)
    if not form.is_submitted():
        parent = db.session.get(WikiPage, int_arg("parent")) if int_arg("parent") else None
        form.title.data = request.args.get("title", "")
        form.parent_id.data = parent.id if parent else 0
        # A sub page always goes into the wiki of its parent.
        form.project_id.data = (parent.project_id if parent else int_arg("project")) or 0

    if form.validate_on_submit():
        parent = _validate_parent(form)
        if parent is not False:
            project = db.session.get(Project, form.project_id.data) if form.project_id.data else None
            page = WikiPage(
                title=form.title.data,
                slug=_unique_slug(form.title.data, project),
                content=form.content.data or "",
                parent_id=parent.id if parent else None,
                project_id=project.id if project else None,
                updated_by_id=current_user.id,
            )
            db.session.add(page)
            db.session.commit()
            flash("Pagina aangemaakt.", "success")
            return redirect(url_for("wiki.view", slug=page.slug))
    return render_template(
        "wiki/edit.html", form=form, page=None, cancel_url=_wiki_url(none_if_zero(form.project_id.data))
    )


@bp.route("/<slug>")
def view(slug: str):
    page = db.session.scalar(sa.select(WikiPage).filter_by(slug=slug))
    if page is None:
        # Unknown slug: offer to create it instead of showing a plain 404.
        return render_template("wiki/missing.html", slug=slug), 404
    return render_template("wiki/view.html", page=page)


@bp.route("/<slug>/edit", methods=["GET", "POST"])
def edit(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    form = WikiPageForm(obj=page)
    _prepare_form(form, page)
    if not form.is_submitted():
        form.parent_id.data = page.parent_id or 0
        form.project_id.data = page.project_id or 0

    if form.validate_on_submit():
        parent = _validate_parent(form)
        if parent is not False:
            new_project_id = none_if_zero(form.project_id.data)
            if form.title.data != page.title or (form.content.data or "") != page.content:
                # Store the *previous* version before overwriting it.
                page.revisions.append(
                    WikiRevision(title=page.title, content=page.content, author_id=page.updated_by_id)
                )
            if new_project_id != page.project_id:
                # Moving a page to another wiki moves its whole sub tree along.
                for descendant_id in page.descendant_ids():
                    db.session.get(WikiPage, descendant_id).project_id = new_project_id
            if form.title.data != page.title or new_project_id != page.project_id:
                project = db.session.get(Project, new_project_id) if new_project_id else None
                page.slug = _unique_slug(form.title.data, project, page.id)
            page.title = form.title.data
            page.content = form.content.data or ""
            page.parent_id = parent.id if parent else None
            page.project_id = new_project_id
            page.updated_by_id = current_user.id
            db.session.commit()
            flash("Pagina opgeslagen.", "success")
            return redirect(url_for("wiki.view", slug=page.slug))
    return render_template(
        "wiki/edit.html", form=form, page=page, cancel_url=url_for("wiki.view", slug=page.slug)
    )


# ------------------------------------------------------------------ history
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
        page.slug = _unique_slug(rev.title, page.project, page.id)
    page.title, page.content, page.updated_by_id = rev.title, rev.content, current_user.id
    db.session.commit()
    flash("Oude versie teruggezet.", "success")
    return redirect(url_for("wiki.view", slug=page.slug))


# ------------------------------------------------------------ delete / export
@bp.route("/<slug>/delete", methods=["POST"])
def delete(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    if page.children:
        flash("Deze pagina heeft subpagina's. Verplaats of verwijder die eerst.", "danger")
        return redirect(url_for("wiki.view", slug=page.slug))
    project_id = page.project_id
    db.session.delete(page)
    db.session.commit()
    flash("Pagina verwijderd.", "success")
    return redirect(_wiki_url(project_id))


@bp.route("/<slug>/download")
def download(slug: str):
    page = db.first_or_404(sa.select(WikiPage).filter_by(slug=slug))
    return current_app.response_class(
        f"# {page.title}\n\n{page.content}",
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{page.slug}.md"'},
    )
