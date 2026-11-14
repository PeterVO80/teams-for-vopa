"""Shared pytest fixtures.

Every test gets a fresh application with an empty database containing the
demo data set (see ``app/cli.py``) and a client that is already logged in.
"""

import pytest
from flask import g

from app import create_app
from app.cli import _seed_demo_data
from app.config import TestConfig
from app.extensions import db
from app.models import User, UserRole


@pytest.fixture()
def app():
    app = create_app(TestConfig)

    # The tests keep one app context open (for direct database access), and
    # Flask-Login caches the logged-in user on `g`, which lives on that app
    # context. Forget it at the start of every request, so tests that log in
    # as several users (one test client each) get the right user per request.
    @app.before_request
    def _forget_cached_user():
        g.pop("_login_user", None)

    with app.app_context():
        db.create_all()
        admin = User(username="admin", full_name="Admin Tester", role=UserRole.ADMIN)
        admin.set_password("secret123")
        db.session.add(admin)
        db.session.commit()
        _seed_demo_data()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """Test client logged in as the administrator."""
    client = app.test_client()
    response = client.post("/auth/login", data={"username": "admin", "password": "secret123"})
    assert response.status_code == 302
    return client
