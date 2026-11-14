"""Who may see which projects.

Rules:

* Administrators see everything.
* Other employees only see the projects they are linked to: as a member
  (``Project.members``) or as the project manager.
* Things that don't belong to a project (tasks or notes without a project,
  the general wiki, absences, ...) are visible to everyone.

Everything in the application that lists or opens project data uses the
helpers below, so the rules live in one place:

* :func:`visible_projects_condition` - SQL condition on ``Project``;
* :func:`project_scope` - SQL condition for a ``project_id`` column of
  another table (tasks, notes, orders, ...);
* :func:`can_view_project` / :func:`require_project` - checks for one project
  (the latter answers 404, so the existence of other projects isn't revealed).
"""

from __future__ import annotations

import sqlalchemy as sa
from flask import abort, has_request_context
from flask_login import current_user

from .models import Project, User, project_members


def _user():
    if not has_request_context():
        return None  # CLI, tests outside a request: no restriction
    return current_user if current_user.is_authenticated else None


def sees_everything(user=None) -> bool:
    user = user or _user()
    return user is None or user.is_admin


def visible_projects_condition(user=None):
    """SQL condition selecting the projects ``user`` may see (None = all)."""
    user = user or _user()
    if sees_everything(user):
        return None
    is_member = sa.select(project_members.c.project_id).where(
        project_members.c.project_id == Project.id, project_members.c.user_id == user.id
    ).exists()
    return sa.or_(Project.manager_id == user.id, is_member)


def visible_project_ids(user=None) -> set[int] | None:
    """Ids of the projects ``user`` may see, or None when they see all."""
    condition = visible_projects_condition(user)
    if condition is None:
        return None
    from .extensions import db

    return set(db.session.scalars(sa.select(Project.id).where(condition)))


def project_scope(column, user=None):
    """SQL condition for a ``project_id`` column: no project, or a visible one."""
    condition = visible_projects_condition(user)
    if condition is None:
        return sa.true()
    return sa.or_(column.is_(None), column.in_(sa.select(Project.id).where(condition)))


def can_view_project(project: Project | None, user=None) -> bool:
    """True when there is no project, or ``user`` may see it."""
    user = user or _user()
    return project is None or sees_everything(user) or project.has_member(user)


def require_project(project: Project | None) -> None:
    """Stop the request with 404 when the current user may not see ``project``."""
    if not can_view_project(project):
        abort(404)


def can_manage_team(project: Project, user=None) -> bool:
    """Admins and the project manager may change the members of a project."""
    user = user or _user()
    return sees_everything(user) or project.manager_id == user.id


def link_user(project: Project | None, user: User | None) -> None:
    """Make sure ``user`` is linked to ``project`` (planned / assigned / created)."""
    if project is not None and user is not None:
        project.add_member(user)
