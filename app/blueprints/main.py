"""Dashboard: a personal start page with the most relevant information."""

from datetime import date

import sqlalchemy as sa
from flask import render_template
from flask_login import current_user

from ..access import project_scope, visible_projects_condition
from ..extensions import db
from ..services import backup
from ..models import (
    OPEN_STATUSES,
    Note,
    OrderStatus,
    Project,
    ProjectStatus,
    PurchaseOrder,
    Task,
    TaskStatus,
)
from ..services.stock import below_minimum_count
from . import protected_blueprint

bp = protected_blueprint("main", __name__)


@bp.route("/")
def dashboard():
    # Everything below only counts / lists what the user may see (app/access.py).
    tasks_scope = project_scope(Task.project_id)
    orders_scope = project_scope(PurchaseOrder.project_id)
    visible = visible_projects_condition()

    my_tasks = db.session.scalars(
        sa.select(Task)
        .where(Task.assignee_id == current_user.id, Task.status.in_(OPEN_STATUSES), tasks_scope)
        .order_by(Task.due_date.is_(None), Task.due_date, Task.position)
        .limit(15)
    ).all()

    open_orders = db.session.scalars(
        sa.select(PurchaseOrder)
        .where(PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL]), orders_scope)
        .order_by(PurchaseOrder.expected_date.is_(None), PurchaseOrder.expected_date)
        .limit(10)
    ).all()

    recent_notes = db.session.scalars(
        sa.select(Note).where(project_scope(Note.project_id)).order_by(Note.updated_at.desc()).limit(6)
    ).all()

    # Headline counters for the cards at the top of the dashboard.
    stats = {
        "active_projects": db.session.scalar(
            sa.select(sa.func.count(Project.id)).where(
                Project.status == ProjectStatus.ACTIVE, visible if visible is not None else sa.true())
        ),
        "open_tasks": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(Task.status.in_(OPEN_STATUSES), tasks_scope)
        ),
        "overdue_tasks": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(
                Task.status.in_(OPEN_STATUSES), Task.due_date < date.today(), tasks_scope
            )
        ),
        "in_progress": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(Task.status == TaskStatus.IN_PROGRESS, tasks_scope)
        ),
        "below_minimum": below_minimum_count(),
        "open_orders": db.session.scalar(
            sa.select(sa.func.count(PurchaseOrder.id)).where(
                PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL]), orders_scope
            )
        ),
    }
    return render_template(
        "main/dashboard.html",
        my_tasks=my_tasks,
        open_orders=open_orders,
        recent_notes=recent_notes,
        stats=stats,
        # Administrators are warned when the automatic back-ups stop.
        backup_warning=backup.backup_warning() if current_user.is_admin else None,
    )
