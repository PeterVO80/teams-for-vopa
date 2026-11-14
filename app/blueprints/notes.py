"""Notes: Markdown notes with tags, optionally linked to a project."""

import sqlalchemy as sa
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..access import project_scope, require_project
from ..extensions import db
from ..forms import NoteForm, TagForm, none_if_zero
from ..models import Note, Project, Tag, note_tags
from ..utils import int_arg, next_url
from . import protected_blueprint

bp = protected_blueprint("notes", __name__)


def _note(note_id: int) -> Note:
    """The note, if the current user may see its project (404 otherwise)."""
    note = db.get_or_404(Note, note_id)
    require_project(note.project)
    return note


@bp.route("/")
def index():
    query = sa.select(Note).where(project_scope(Note.project_id)).order_by(Note.pinned.desc(), Note.updated_at.desc())

    search = request.args.get("q", "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.where(sa.or_(Note.title.ilike(pattern), Note.content.ilike(pattern)))

    tag_id = int_arg("tag")
    if tag_id:
        query = query.where(Note.tags.any(Tag.id == tag_id))

    project_id = int_arg("project")
    if project_id:
        query = query.where(Note.project_id == project_id)

    page = db.paginate(query, per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)

    # Tags with usage counts for the filter sidebar.
    tags = db.session.execute(
        sa.select(Tag, sa.func.count(note_tags.c.note_id))
        .outerjoin(note_tags, note_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    return render_template(
        "notes/index.html",
        page=page,
        tags=tags,
        tag_id=tag_id,
        project_id=project_id,
        projects=Project.open_choices(include_empty=False),
        search=search,
    )


@bp.route("/<int:note_id>")
def view(note_id: int):
    return render_template("notes/view.html", note=_note(note_id))


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:note_id>/edit", methods=["GET", "POST"])
def edit(note_id: int | None = None):
    note = _note(note_id) if note_id else None
    form = NoteForm(obj=note)
    form.project_id.choices = Project.open_choices()
    if not form.is_submitted():
        form.project_id.data = (note.project_id if note else int_arg("project")) or 0
        form.tags.data = ", ".join(t.name for t in note.tags) if note else ""

    if form.validate_on_submit():
        if note is None:
            note = Note(author_id=current_user.id)
            db.session.add(note)
        note.title = form.title.data
        note.content = form.content.data or ""
        note.pinned = form.pinned.data
        note.project_id = none_if_zero(form.project_id.data)
        # Tags are typed as free text; unknown names become new tags.
        names = {n.strip() for n in (form.tags.data or "").split(",") if n.strip()}
        with db.session.no_autoflush:
            note.tags = [Tag.get_or_create(name) for name in sorted(names, key=str.lower)]
        db.session.commit()
        flash("Notitie opgeslagen.", "success")
        return redirect(url_for("notes.view", note_id=note.id))

    all_tags = db.session.scalars(sa.select(Tag.name).order_by(Tag.name)).all()
    return render_template(
        "notes/edit.html",
        form=form,
        note=note,
        all_tags=all_tags,
        cancel_url=url_for("notes.view", note_id=note.id) if note else url_for("notes.index"),
    )


@bp.route("/<int:note_id>/delete", methods=["POST"])
def delete(note_id: int):
    db.session.delete(_note(note_id))
    db.session.commit()
    flash("Notitie verwijderd.", "success")
    return redirect(url_for("notes.index"))


@bp.route("/<int:note_id>/download")
def download(note_id: int):
    """Download a note as a plain ``.md`` file."""
    note = _note(note_id)
    body = f"# {note.title}\n\n{note.content}"
    return current_app.response_class(
        body,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="note-{note.id}.md"'},
    )


# --------------------------------------------------------------------- tags
@bp.route("/tags", methods=["GET", "POST"])
def tags():
    """Manage tags: rename, recolour, delete."""
    form = TagForm()
    if form.validate_on_submit():
        if db.session.scalar(sa.select(Tag).where(sa.func.lower(Tag.name) == form.name.data.strip().lower())):
            form.name.errors.append("Deze tag bestaat al.")
        else:
            db.session.add(Tag(name=form.name.data.strip(), color=form.color.data))
            db.session.commit()
            flash("Tag toegevoegd.", "success")
            return redirect(url_for("notes.tags"))
    all_tags = db.session.scalars(sa.select(Tag).order_by(Tag.name)).all()
    return render_template("notes/tags.html", form=form, tags=all_tags)


@bp.route("/tags/<int:tag_id>", methods=["POST"])
def update_tag(tag_id: int):
    tag = db.get_or_404(Tag, tag_id)
    form = TagForm()
    if form.validate_on_submit():
        clash = db.session.scalar(
            sa.select(Tag).where(sa.func.lower(Tag.name) == form.name.data.strip().lower(), Tag.id != tag.id)
        )
        if clash:
            flash("Een andere tag heeft al deze naam.", "danger")
        else:
            tag.name, tag.color = form.name.data.strip(), form.color.data
            db.session.commit()
            flash("Tag bijgewerkt.", "success")
    return redirect(next_url(url_for("notes.tags")))


@bp.route("/tags/<int:tag_id>/delete", methods=["POST"])
def delete_tag(tag_id: int):
    db.session.delete(db.get_or_404(Tag, tag_id))
    db.session.commit()
    flash("Tag verwijderd (notities blijven behouden).", "success")
    return redirect(url_for("notes.tags"))
