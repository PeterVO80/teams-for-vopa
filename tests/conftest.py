"""Shared pytest fixtures.

Every test gets a fresh application with an empty database containing the
demo data set (see ``app/cli.py``) and a client that is already logged in.
"""

import pytest

from app import create_app
from app.cli import _seed_demo_data
from app.config import TestConfig
from app.extensions import db
from app.models import User, UserRole


@pytest.fixture()
def app():
    app = create_app(TestConfig)
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
