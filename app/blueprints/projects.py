"""Projects: list, create/edit and a detail page tying all modules together."""

import sqlalchemy as sa
from flask import flash, redirect, render_template, request, url_for
from markupsafe import Markup

from ..extensions import db
from ..forms import ProjectForm, none_if_zero
from ..models import (
    Note,
    Project,
    ProjectStatus,
    PurchaseOrder,
    Task,
    TaskStatus,
    User,
    WikiPage,
)
from ..services.numbering import insert_with_unique_number
from . import protected_blueprint

bp = protected_blueprint("projects", __name__)


@bp.route("/")
def index():
    status = request.args.get("status", "open")
    query = sa.select(Project).order_by(Project.code.desc())
    if status == "open":
        query = query.where(Project.status != ProjectStatus.CLOSED)
    elif status in ProjectStatus._value2member_map_:
        query = query.where(Project.status == ProjectStatus(status))
    projects = db.session.scalars(query).all()

    # Open task counts per project in a single grouped query.
    open_counts = dict(
        db.session.execute(
            sa.select(Task.project_id, sa.func.count(Task.id))
            .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD]))
            .group_by(Task.project_id)
        ).all()
    )
    return render_template("projects/index.html", projects=projects, status=status, open_counts=open_counts)


@bp.route("/<int:project_id>")
def detail(project_id: int):
    project = db.get_or_404(Project, project_id)
    tasks = db.session.scalars(
        sa.select(Task).filter_by(project_id=project.id).order_by(Task.status, Task.position)
    ).all()
    orders = db.session.scalars(
        sa.select(PurchaseOrder).filter_by(project_id=project.id).order_by(PurchaseOrder.order_date.desc())
    ).all()
    notes = db.session.scalars(
        sa.select(Note).filter_by(project_id=project.id).order_by(Note.updated_at.desc())
    ).all()
    # Project wiki: top-level pages (the template shows the tree) + page count.
    wiki_roots = db.session.scalars(
        sa.select(WikiPage).filter_by(project_id=project.id, parent_id=None).order_by(WikiPage.title)
    ).all()
    wiki_count = db.session.scalar(sa.select(sa.func.count(WikiPage.id)).filter_by(project_id=project.id))

    # Task count per status for the progress summary.
    status_counts = {s: 0 for s in TaskStatus}
    for task in tasks:
        status_counts[task.status] += 1
    return render_template(
        "projects/detail.html",
        project=project,
        tasks=tasks,
        orders=orders,
        notes=notes,
        wiki_roots=wiki_roots,
        wiki_count=wiki_count,
        status_counts=status_counts,
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
def edit(project_id: int | None = None):
    """Create or edit a project.

    The project code is never entered by the user: a new project gets the
    next automatic code (see :meth:`Project.assign_code`) when it is saved.
    """
    project = db.get_or_404(Project, project_id) if project_id else None
    form = ProjectForm(obj=project)
    form.manager_id.choices = [(0, "- geen -")] + User.active_choices()
    if project and not form.is_submitted():
        form.manager_id.data = project.manager_id or 0

    if form.validate_on_submit():
        is_new = project is None
        if is_new:
            project = Project()
        form.populate_obj(project)
        project.status = ProjectStatus(form.status.data)
        project.manager_id = none_if_zero(form.manager_id.data)
        if is_new:
            # Retries automatically if a colleague saved a project with the
            # same code at the same moment.
            insert_with_unique_number(project, project.assign_code)
        db.session.commit()
        flash(f"Project {project.code} {'aangemaakt' if is_new else 'opgeslagen'}.", "success")
        return redirect(url_for("projects.detail", project_id=project.id))

    if project:
        intro = f"Projectcode: <strong>{project.code}</strong>"
    else:
        # Preview only: the definitive code is assigned when saving.
        preview = Project()
        preview.assign_code()
        intro = (
            f"De projectcode wordt automatisch toegekend bij het opslaan "
            f"(volgende code: <strong>{preview.code}</strong>)."
        )
    return render_template(
        "form_page.html",
        form=form,
        title=f"Project {project.code} bewerken" if project else "Nieuw project",
        intro=Markup(intro),
        cancel_url=url_for("projects.detail", project_id=project.id) if project else url_for("projects.index"),
    )
