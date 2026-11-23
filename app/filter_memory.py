"""Remember the filters of list pages while the user moves around the app.

When someone filters the punch list on a project, opens a task and comes
back, the board should still show that project. For every page listed in
:data:`REMEMBERED` the filter values (query parameters) are stored in the
user's session:

* a filter form is submitted (it carries the hidden marker ``f=1``): the
  submitted values are stored as they are - also empty ones, so clearing a
  filter or unticking a checkbox is remembered too;
* the page is opened without some remembered filters (menu, back link,
  redirect after saving): the browser is redirected to the same page with
  the remembered filters added;
* ``?reset=1`` forgets the filters of that page ("Filters wissen").

Page numbers and dates are deliberately not remembered. The values live in
the signed session cookie of the user, so they are per user and per browser
and disappear at log-out.
"""

from __future__ import annotations

from flask import Flask, redirect, request, session, url_for
from flask_login import current_user
from markupsafe import Markup

#: Endpoint -> (remembered query parameters, remembered per object?)
#: "per object" pages (one supplier catalogue) keep separate filters per id.
REMEMBERED: dict[str, tuple[tuple[str, ...], bool]] = {
    "projects.index": (("q", "customer", "manager", "status"), False),
    "projects.detail": (("planning_view", "planning_weeks"), False),
    "tasks.board": (("project", "assignee", "show", "view", "sort", "dir"), False),
    "articles.index": (("q", "category", "inactive"), False),
    "catalog.detail": (("q", "manufacturer", "group", "status"), True),
    "notes.index": (("q", "project", "tag"), False),
    "purchasing.index": (("status", "project"), False),
    "purchasing.material": (("project", "open"), False),
    "relations.index": (("q", "kind", "inactive"), False),
    "stock.index": (("q", "location", "category", "below"), False),
    "stock.movements": (("location", "project", "kind"), False),
    "team.overview": (("view", "weeks"), False),
    "team.assignments": (("user",), False),
}

#: Query parameter that marks a submitted filter form (the complete filter state).
MARKER = "f"
SESSION_KEY = "filters"


def marker_field() -> Markup:
    """Hidden input for filter forms: ``{{ filter_marker() }}``."""
    return Markup(f'<input type="hidden" name="{MARKER}" value="1">')


def _store_key(endpoint: str, per_object: bool) -> str:
    if per_object and request.view_args:
        ids = ",".join(f"{k}={v}" for k, v in sorted(request.view_args.items()))
        return f"{endpoint}:{ids}"
    return endpoint


def _remember():
    endpoint = request.endpoint
    if request.method != "GET" or endpoint not in REMEMBERED or not current_user.is_authenticated:
        return None
    if request.headers.get("X-Modal"):
        return None
    keys, per_object = REMEMBERED[endpoint]
    key = _store_key(endpoint, per_object)
    stored: dict = session.get(SESSION_KEY, {})
    saved: dict = stored.get(key, {})
    args = request.args
    view_args = request.view_args or {}

    if args.get("reset"):
        if key in stored:
            stored.pop(key)
            session[SESSION_KEY] = stored
        return redirect(url_for(endpoint, **view_args))

    submitted = MARKER in args
    if not submitted:
        missing = {k: v for k, v in saved.items() if k not in args}
        if missing:
            params = args.to_dict(flat=False)
            params.update({k: [v] for k, v in missing.items()})
            return redirect(url_for(endpoint, **view_args, **params))

    if submitted or any(k in args for k in keys):
        # A submitted form is the complete state: empty fields and unticked
        # checkboxes simply drop out, so clearing a filter is remembered too.
        current = {k: args[k] for k in keys if args.get(k)}
        if current != saved:
            if current:
                stored[key] = current
            else:
                stored.pop(key, None)
            session[SESSION_KEY] = stored
    return None


def init_filter_memory(app: Flask) -> None:
    app.before_request(_remember)
    app.jinja_env.globals["filter_marker"] = marker_field
