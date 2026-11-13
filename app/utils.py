"""Small helpers shared by the blueprints."""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlparse

from flask import abort, request
from flask_login import current_user


def admin_required(view):
    """Decorator: only allow users with the *admin* role."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def is_safe_redirect(target: str | None) -> bool:
    """Only allow relative redirects (prevents open-redirect attacks)."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")


def next_url(default: str) -> str:
    """Return the ``next`` query/form parameter if it is safe, else ``default``."""
    target = request.args.get("next") or request.form.get("next")
    return target if is_safe_redirect(target) else default


def is_modal_request() -> bool:
    """True when the request comes from the popup (``static/js/modal_form.js``).

    The popup loads a form as an HTML *fragment* and submits it with
    ``fetch()``; it marks those requests with an ``X-Modal: 1`` header. Views
    then answer with a fragment (GET / validation errors, status 422) or JSON
    ``{"ok": true}`` (saved). Without the header the same URLs can still serve
    normal full pages.
    """
    return request.headers.get("X-Modal") == "1"


def int_arg(name: str) -> int | None:
    """Read an optional integer query parameter (``?project=3``)."""
    return request.args.get(name, type=int) or None
