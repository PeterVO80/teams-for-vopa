"""One Flask blueprint per functional module.

Every blueprint protects all its routes with ``login_required`` via a
``before_request`` hook (except the login page itself), so a forgotten
decorator can never expose a page to anonymous visitors.
"""

from flask import Blueprint
from flask_login import login_required


def protected_blueprint(name: str, import_name: str) -> Blueprint:
    """Create a blueprint whose routes all require a logged-in user."""
    bp = Blueprint(name, import_name)

    @bp.before_request
    @login_required
    def _require_login():
        return None

    return bp
