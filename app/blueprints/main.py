"""Dashboard: a personal start page with the most relevant information."""

from datetime import date

import sqlalchemy as sa
from flask import render_template
from flask_login import current_user

from ..extensions import db
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
    my_tasks = db.session.scalars(
        sa.select(Task)
        .where(Task.assignee_id == current_user.id, Task.status.in_(OPEN_STATUSES))
        .order_by(Task.due_date.is_(None), Task.due_date, Task.position)
        .limit(15)
    ).all()

    open_orders = db.session.scalars(
        sa.select(PurchaseOrder)
        .where(PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL]))
        .order_by(PurchaseOrder.expected_date.is_(None), PurchaseOrder.expected_date)
        .limit(10)
    ).all()

    recent_notes = db.session.scalars(
        sa.select(Note).order_by(Note.updated_at.desc()).limit(6)
    ).all()

    # Headline counters for the cards at the top of the dashboard.
    stats = {
        "active_projects": db.session.scalar(
            sa.select(sa.func.count(Project.id)).where(Project.status == ProjectStatus.ACTIVE)
        ),
        "open_tasks": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(Task.status.in_(OPEN_STATUSES))
        ),
        "overdue_tasks": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(
                Task.status.in_(OPEN_STATUSES), Task.due_date < date.today()
            )
        ),
        "in_progress": db.session.scalar(
            sa.select(sa.func.count(Task.id)).where(Task.status == TaskStatus.IN_PROGRESS)
        ),
        "below_minimum": below_minimum_count(),
        "open_orders": db.session.scalar(
            sa.select(sa.func.count(PurchaseOrder.id)).where(
                PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL])
            )
        ),
    }
    return render_template(
        "main/dashboard.html",
        my_tasks=my_tasks,
        open_orders=open_orders,
        recent_notes=recent_notes,
        stats=stats,
    )
