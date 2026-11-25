"""Projects: list, create/edit and a detail page tying all modules together."""

from decimal import Decimal

import sqlalchemy as sa
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup

from ..access import can_manage_team, link_user, require_project, visible_projects_condition
from ..extensions import db
from ..forms import ProjectForm, none_if_zero
from ..models import (
    Assignment,
    Company,
    Note,
    Project,
    ProjectStatus,
    Quote,
    PurchaseOrder,
    Task,
    TaskStatus,
    TimeEntry,
    User,
    OutlookLink,
    StockMovement,
    WikiPage,
)
from ..services import outlook
from ..services import stock as stock_svc
from ..services.material import project_material
from ..services.workload import build_overview, planning_period
from ..services.numbering import insert_with_unique_number
from . import protected_blueprint
from .relations import add_button as add_relation_button
from .wiki import wiki_tree

bp = protected_blueprint("projects", __name__)


@bp.route("/")
def index():
    """Project list with filters: search text, customer, project manager, status."""
    status = request.args.get("status", "open")
    search = request.args.get("q", "").strip()
    customer_id = request.args.get("customer", type=int)
    manager_id = request.args.get("manager", type=int)

    visible = visible_projects_condition()  # employees: only their own projects
    base = sa.select(Project)
    if visible is not None:
        base = base.where(visible)

    query = base.order_by(Project.code.desc())
    if status == "open":
        query = query.where(Project.status != ProjectStatus.CLOSED)
    elif status in ProjectStatus._value2member_map_:
        query = query.where(Project.status == ProjectStatus(status))
    if search:
        like = f"%{search}%"
        query = query.outerjoin(Company, Project.customer_id == Company.id).where(sa.or_(
            Project.code.ilike(like), Project.name.ilike(like),
            Project.description.ilike(like), Company.name.ilike(like),
        ))
    if customer_id:
        query = query.where(Project.customer_id == customer_id)
    if manager_id:
        query = query.where(Project.manager_id == manager_id)
    projects = db.session.scalars(query).all()

    # Filter choices: only customers / managers that occur in the visible projects.
    visible_ids = base.with_only_columns(Project.id)
    customers = db.session.execute(
        sa.select(Company.id, Company.name).where(
            Company.id.in_(sa.select(Project.customer_id).where(Project.id.in_(visible_ids))))
        .order_by(Company.name)).all()
    managers = db.session.execute(
        sa.select(User.id, User.full_name).where(
            User.id.in_(sa.select(Project.manager_id).where(Project.id.in_(visible_ids))))
        .order_by(User.full_name)).all()

    # Open task counts per project in a single grouped query.
    open_counts = dict(
        db.session.execute(
            sa.select(Task.project_id, sa.func.count(Task.id))
            .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD]))
            .group_by(Task.project_id)
        ).all()
    )
    return render_template(
        "projects/index.html", projects=projects, status=status, open_counts=open_counts,
        search=search, customer_id=customer_id, manager_id=manager_id,
        customers=customers, managers=managers,
        filtered=bool(search or customer_id or manager_id or status != "open"),
    )


@bp.route("/<int:project_id>")
def detail(project_id: int):
    project = db.get_or_404(Project, project_id)
    require_project(project)
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
    wiki_roots = wiki_tree(project.id)
    wiki_count = db.session.scalar(sa.select(sa.func.count(WikiPage.id)).filter_by(project_id=project.id))
    # Ordered vs. delivered material, grouped per article (services/material.py).
    material = project_material(project.id)

    # Planning tab: the team board focused on this project (?planning_start=,
    # ?planning_weeks=), plus every planning entry of the project.
    period = planning_period(request.args.get("planning_view"), request.args.get("planning_start"),
                             request.args.get("planning_weeks", type=int), default_weeks=4, max_weeks=12)
    planning_days, planning_rows = build_overview(period.start, days=period.days)
    # Team members planned on this project in the period first, then the others.
    planning_rows.sort(key=lambda r: not any(s.assignment and s.assignment.project_id == project.id for s in r.segments))
    project_assignments = db.session.scalars(
        sa.select(Assignment).filter_by(project_id=project.id).order_by(Assignment.start_date)
    ).all()
    stock_usage = stock_svc.project_usage(project.id)
    # Hours tab: every registration on the project, newest first (models/timesheet.py).
    time_entries = db.session.scalars(
        sa.select(TimeEntry).filter_by(project_id=project.id)
        .order_by(TimeEntry.work_date.desc(), TimeEntry.id.desc())
    ).all()

    # Task count per status for the progress summary.
    status_counts = {s: 0 for s in TaskStatus}
    for task in tasks:
        status_counts[task.status] += 1
    can_manage = can_manage_team(project)
    addable_users = [
        u for u in db.session.scalars(sa.select(User).where(User.active.is_(True)).order_by(User.full_name))
        if not project.has_member(u)
    ] if can_manage else []
    return render_template(
        "projects/detail.html",
        can_manage_team=can_manage,
        addable_users=addable_users,
        project=project,
        tasks=tasks,
        orders=orders,
        notes=notes,
        wiki_roots=wiki_roots,
        material=material,
        planning_days=planning_days,
        planning_rows=planning_rows,
        planning_period=period,
        project_assignments=project_assignments,
        workday_hours=current_app.config["WORKDAY_HOURS"],
        stock_usage=stock_usage,
        wiki_count=wiki_count,
        status_counts=status_counts,
        time_entries=time_entries,
        quotes=db.session.scalars(sa.select(Quote).filter_by(project_id=project.id).order_by(Quote.number.desc())).all(),
        time_total_hours=sum((e.hours for e in time_entries), Decimal("0")),
        time_total_km=sum((e.km or Decimal("0") for e in time_entries), Decimal("0")),
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
def edit(project_id: int | None = None):
    """Create or edit a project.

    The project code is never entered by the user: a new project gets the
    next automatic code (see :meth:`Project.assign_code`) when it is saved.
    """
    project = db.get_or_404(Project, project_id) if project_id else None
    require_project(project)
    form = ProjectForm(obj=project)
    form.manager_id.choices = [(0, "- geen -")] + User.active_choices()
    form.customer_id.choices = Company.choices("customer", project.customer_id if project else None, empty="- geen klant -")
    add_relation_button(form.customer_id, "customer")
    if project and not form.is_submitted():
        form.manager_id.data = project.manager_id or 0
        form.customer_id.data = project.customer_id or 0

    if form.validate_on_submit():
        is_new = project is None
        if is_new:
            project = Project()
        form.populate_obj(project)
        project.status = ProjectStatus(form.status.data)
        project.manager_id = none_if_zero(form.manager_id.data)
        project.customer_id = none_if_zero(form.customer_id.data)
        if is_new:
            # The creator is linked, otherwise an employee couldn't see the
            # project they just made.
            link_user(project, current_user)
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


# ------------------------------------------------------------------- team
@bp.route("/<int:project_id>/members", methods=["POST"])
def add_member(project_id: int):
    """Link an employee to the project (admins and the project manager)."""
    project = db.get_or_404(Project, project_id)
    require_project(project)
    if not can_manage_team(project):
        abort(403)
    user = db.session.get(User, request.form.get("user_id", type=int) or 0)
    if user is None or not user.active:
        flash("Kies een medewerker.", "warning")
    elif project.add_member(user):
        db.session.commit()
        flash(f"{user.full_name} is toegevoegd aan het project.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@bp.route("/<int:project_id>/members/<int:user_id>/remove", methods=["POST"])
def remove_member(project_id: int, user_id: int):
    """Unlink an employee. Their tasks and planning stay, but they no longer see the project."""
    project = db.get_or_404(Project, project_id)
    require_project(project)
    if not can_manage_team(project):
        abort(403)
    user = db.get_or_404(User, user_id)
    if user in project.members:
        project.members.remove(user)
        db.session.commit()
        flash(f"{user.full_name} is niet langer gekoppeld aan het project.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


# ------------------------------------------------------------------ delete
#: Word an administrator types to confirm deleting a project.
DELETE_WORD = "VERWIJDER"


def project_contents(project: Project) -> dict[str, int]:
    """What is linked to a project (shown before deleting it)."""
    def count(model):
        return db.session.scalar(sa.select(sa.func.count(model.id)).where(model.project_id == project.id))
    return {
        "tasks": count(Task), "assignments": count(Assignment), "wiki": count(WikiPage),
        "notes": count(Note), "orders": count(PurchaseOrder), "movements": count(StockMovement),
    }


def delete_project(project: Project) -> None:
    """Delete a project and everything that only exists within it.

    Deleted: tasks, planning (their Outlook appointments are removed by the
    Outlook synchronisation), the project wiki with its history, the
    project's notes and the team. Kept but unlinked: purchase orders and
    stock movements - they are part of the purchasing and stock history,
    and deleting them would change stock levels.
    """
    pid = project.id
    db.session.execute(sa.update(PurchaseOrder).where(PurchaseOrder.project_id == pid).values(project_id=None))
    db.session.execute(sa.update(StockMovement).where(StockMovement.project_id == pid).values(project_id=None))
    assignment_ids = sa.select(Assignment.id).where(Assignment.project_id == pid)
    db.session.execute(sa.update(OutlookLink).where(OutlookLink.assignment_id.in_(assignment_ids))
                       .values(assignment_id=None))
    db.session.execute(sa.delete(Assignment).where(Assignment.project_id == pid))
    db.session.execute(sa.delete(Task).where(Task.project_id == pid))
    for note in db.session.scalars(sa.select(Note).where(Note.project_id == pid)):
        db.session.delete(note)               # ORM: also removes its tag links
    db.session.execute(sa.update(WikiPage).where(WikiPage.project_id == pid).values(parent_id=None))
    for page in db.session.scalars(sa.select(WikiPage).where(WikiPage.project_id == pid)):
        db.session.delete(page)               # ORM: also removes its revisions
    db.session.flush()
    db.session.delete(project)                # the team (project_members) goes with it


@bp.route("/<int:project_id>/delete", methods=["GET", "POST"])
def delete(project_id: int):
    if not current_user.is_admin:
        abort(403)
    project = db.get_or_404(Project, project_id)
    contents = project_contents(project)
    if request.method == "POST":
        if request.form.get("confirm", "").strip().upper() != DELETE_WORD:
            flash(f"Typ {DELETE_WORD} om het verwijderen te bevestigen.", "warning")
        else:
            label = f"{project.code} - {project.name}"
            delete_project(project)
            db.session.commit()
            current_app.logger.warning("Project %s verwijderd door %s", label, current_user.username)
            if contents["assignments"]:
                outlook.push_soon(current_app._get_current_object())   # remove the Outlook appointments
            flash(f"Project {label} is verwijderd.", "success")
            return redirect(url_for("projects.index"))
    return render_template("projects/delete.html", project=project, contents=contents, confirm_word=DELETE_WORD)
