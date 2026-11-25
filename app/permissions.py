"""Access per module (app/modules.py), checked on every request.

* no access: every page of the module answers 403 and the module is not in
  the menu;
* read only: pages can be viewed - also an existing record's form (a task, an
  article: there is no separate view page), shown read-only - but every change
  (POST) and every page to create, delete or import answers 403. The buttons
  for those are hidden: some in the templates with ``can_write("module")``,
  the rest by static/js/read_only.js with the rules of :func:`client_rules`;
* write: everything.

Administrators always have write access. Which projects someone sees is a
separate rule on top of this (app/access.py).
"""

from __future__ import annotations

import re

from flask import Flask, abort, current_app, g, request
from flask_login import current_user

from .modules import BLUEPRINT_MODULE, MODULE_KEYS

#: Endpoints (function names) that change data, also on GET (their form page).
WRITE_VIEWS = {"create", "delete", "move", "week", "add_article", "import_view", "import_supplier_order"}
#: Form pages that also show an existing record: read-only users may open them
#: for an existing record (URL with an id), not for a new one.
RECORD_FORMS = {"edit", "edit_assignment", "edit_line"}

#: Helpers other modules' forms use (article search in an order, distance in
#: the relation form): available to every logged-in user.
SHARED_ENDPOINTS = {"articles.search_json", "relations.calc_distance"}


def needs_write(endpoint: str, method: str, view_args: dict | None = None) -> bool:
    if method not in ("GET", "HEAD", "OPTIONS"):
        return True
    name = endpoint.rsplit(".", 1)[-1]
    return name in WRITE_VIEWS or (name in RECORD_FORMS and not view_args)


def _check_module_access():
    endpoint = request.endpoint or ""
    module = BLUEPRINT_MODULE.get(request.blueprint or "")
    if module is None or endpoint in SHARED_ENDPOINTS or not current_user.is_authenticated:
        return None                                  # not a module page (login is checked by the blueprint)
    if not current_user.can_read(module):
        abort(403)
    if needs_write(endpoint, request.method, request.view_args) and not current_user.can_write(module):
        abort(403)
    return None


def _can_read(module: str) -> bool:
    return current_user.is_authenticated and current_user.can_read(module)


def _can_write(module: str) -> bool:
    return current_user.is_authenticated and current_user.can_write(module)


def _path_regex(rule) -> str:
    """``/tasks/<int:task_id>/edit`` -> ``^/tasks/[^/]+/edit$`` (JavaScript-compatible)."""
    parts = re.split(r"<[^>]+>", rule.rule)
    return "^" + "[^/]+".join(re.escape(p) for p in parts) + "$"


def client_rules() -> dict | None:
    """URL patterns for static/js/read_only.js: what the current user may not change.

    ``hide``: links to pages that create, delete, import ... (GET) in modules
    without write access; ``post``: every URL of those modules that accepts a
    POST (forms posting there are shown read-only, delete buttons hidden).
    None when the user may write everywhere.
    """
    if not current_user.is_authenticated or current_user.is_admin:
        return None
    if "read_only_rules" not in g:
        readonly = {m for m in MODULE_KEYS if not current_user.can_write(m)}
        hide, post = [], []
        if readonly:
            for rule in current_app.url_map.iter_rules():
                if BLUEPRINT_MODULE.get(rule.endpoint.split(".")[0]) not in readonly or rule.endpoint in SHARED_ENDPOINTS:
                    continue
                if "GET" in rule.methods and needs_write(rule.endpoint, "GET", rule.arguments):
                    hide.append(_path_regex(rule))
                if "POST" in rule.methods:
                    post.append(_path_regex(rule))
        g.read_only_rules = {"hide": hide, "post": post} if readonly else None
    return g.read_only_rules


def init_permissions(app: Flask) -> None:
    app.before_request(_check_module_access)
    app.jinja_env.globals.update(can_read=_can_read, can_write=_can_write, read_only_rules=client_rules)
