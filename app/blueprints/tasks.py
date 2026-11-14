"""Punch list: tasks per team member, displayed as a kanban board.

The board has one column per :class:`~app.models.TaskStatus`. Cards are moved
with drag & drop (SortableJS in ``static/js/kanban.js``); every drop sends a
small JSON request to :func:`move` which stores the new status and order.
"""

from datetime import date, timedelta

import sqlalchemy as sa
from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..access import link_user, project_scope, require_project
from ..extensions import db
from ..forms import TaskForm, none_if_zero
from ..models import Project, Task, TaskPriority, TaskStatus, User
from ..utils import int_arg, next_url
from . import protected_blueprint

bp = protected_blueprint("tasks", __name__)

#: Finished cards older than this are hidden from the board to keep it usable.
DONE_VISIBLE_DAYS = 30


@bp.route("/")
def board():
    project_id = int_arg("project")
    assignee = request.args.get("assignee", "")
    show_all = bool(request.args.get("all"))

    # Employees only see tasks without a project or of their own projects.
    query = sa.select(Task).where(project_scope(Task.project_id)).order_by(Task.position, Task.id)
    if project_id:
        query = query.where(Task.project_id == project_id)
    if assignee == "me":
        query = query.where(Task.assignee_id == current_user.id)
    elif assignee == "none":
        query = query.where(Task.assignee_id.is_(None))
    elif assignee.isdigit():
        query = query.where(Task.assignee_id == int(assignee))
    if not show_all:
        cutoff = date.today() - timedelta(days=DONE_VISIBLE_DAYS)
        query = query.where(
            sa.or_(
                Task.status.not_in([TaskStatus.DONE, TaskStatus.CANCELLED]),
                Task.updated_at >= cutoff,
            )
        )

    columns: dict[TaskStatus, list[Task]] = {status: [] for status in TaskStatus}
    for task in db.session.scalars(query):
        columns[task.status].append(task)

    return render_template(
        "tasks/board.html",
        columns=columns,
        projects=Project.open_choices(include_empty=False),
        users=User.active_choices(),
        project_id=project_id,
        assignee=assignee,
        show_all=show_all,
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:task_id>/edit", methods=["GET", "POST"])
def edit(task_id: int | None = None):
    task = db.get_or_404(Task, task_id) if task_id else None
    require_project(task.project if task else None)
    form = TaskForm(obj=task)
    form.project_id.choices = Project.open_choices()
    form.assignee_id.choices = [(0, "- niet toegewezen -")] + User.active_choices()

    if not form.is_submitted():
        if task:
            form.project_id.data = task.project_id or 0
            form.assignee_id.data = task.assignee_id or 0
        else:  # pre-fill from the board filters / query string
            form.project_id.data = int_arg("project") or 0
            form.assignee_id.data = int_arg("assignee") or 0
            if request.args.get("status") in TaskStatus._value2member_map_:
                form.status.data = request.args["status"]

    if form.validate_on_submit():
        new_status = TaskStatus(form.status.data)
        # Determine the position *before* adding a new task to the session,
        # otherwise the query autoflushes a half-filled (invalid) Task row.
        if task is None:
            position = _next_position(new_status)
            task = Task(created_by_id=current_user.id, position=position)
            db.session.add(task)
        elif task.status != new_status:
            task.position = _next_position(new_status)
        form.populate_obj(task)
        task.status = new_status
        task.priority = TaskPriority(form.priority.data)
        task.project_id = none_if_zero(form.project_id.data)
        task.assignee_id = none_if_zero(form.assignee_id.data)
        if task.project_id and task.assignee_id:
            # Whoever gets a task in a project must be able to see that project.
            link_user(db.session.get(Project, task.project_id), db.session.get(User, task.assignee_id))
        db.session.commit()
        flash("Taak opgeslagen.", "success")
        return redirect(next_url(url_for("tasks.board")))

    return render_template(
        "tasks/edit.html",
        form=form,
        task=task,
        cancel_url=next_url(url_for("tasks.board")),
    )


@bp.route("/<int:task_id>/delete", methods=["POST"])
def delete(task_id: int):
    task = db.get_or_404(Task, task_id)
    require_project(task.project)
    db.session.delete(task)
    db.session.commit()
    flash("Taak verwijderd.", "success")
    return redirect(next_url(url_for("tasks.board")))


@bp.route("/<int:task_id>/move", methods=["POST"])
def move(task_id: int):
    """AJAX endpoint called after a drag & drop on the board.

    Expected JSON body::

        {"status": "in_progress", "order": [12, 7, 3]}

    ``order`` contains the ids of all cards in the target column in their new
    order; their ``position`` is rewritten to match.
    """
    task = db.get_or_404(Task, task_id)
    require_project(task.project)
    payload = request.get_json(silent=True) or {}
    try:
        status = TaskStatus(payload.get("status"))
    except ValueError:
        abort(400, "Unknown status")

    task.status = status
    order = [int(i) for i in payload.get("order", []) if str(i).isdigit()]
    if order:
        tasks = {t.id: t for t in db.session.scalars(sa.select(Task).where(Task.id.in_(order)))}
        for position, tid in enumerate(order):
            if tid in tasks:
                tasks[tid].position = position
    db.session.commit()
    return jsonify({"ok": True, "status": status.value, "label": status.label})


def _next_position(status: TaskStatus) -> int:
    """Position that puts a card at the bottom of the given column."""
    current_max = db.session.scalar(sa.select(sa.func.max(Task.position)).where(Task.status == status))
    return (current_max or 0) + 1
