"""Smoke tests: every page renders and requires authentication."""

import sqlalchemy as sa
import pytest

from app.extensions import db
from app.models import Note, Project, PurchaseOrder, Task, WikiPage


def test_login_required(app):
    response = app.test_client().get("/tasks/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_wrong_password_is_rejected(app):
    response = app.test_client().post(
        "/auth/login", data={"username": "admin", "password": "wrong"}, follow_redirects=True
    )
    assert "Ongeldige gebruikersnaam" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/projects/",
        "/projects/?status=all",
        "/projects/new",
        "/articles/",
        "/articles/?q=siemens",
        "/articles/new",
        "/articles/import",
        "/purchasing/",
        "/purchasing/deliveries",
        "/purchasing/new",
        "/tasks/",
        "/tasks/?assignee=me&all=1",
        "/tasks/new",
        "/team/",
        "/team/?weeks=4&start=2026-01-05",
        "/team/assignments",
        "/team/assignments/new",
        "/notes/",
        "/notes/new",
        "/notes/tags",
        "/wiki/",
        "/wiki/new",
        "/wiki/search?q=tia",
        "/users/",
        "/users/new",
        "/auth/password",
    ],
)
def test_static_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_detail_pages_render(client):
    project = db.session.scalar(sa.select(Project))
    order = db.session.scalar(sa.select(PurchaseOrder))
    task = db.session.scalar(sa.select(Task))
    note = db.session.scalar(sa.select(Note))
    page = db.session.scalar(sa.select(WikiPage))
    for url in [
        f"/projects/{project.id}",
        f"/projects/{project.id}/edit",
        f"/purchasing/{order.id}",
        f"/purchasing/{order.id}/edit",
        f"/tasks/{task.id}/edit",
        f"/notes/{note.id}",
        f"/notes/{note.id}/edit",
        f"/wiki/{page.slug}",
        f"/wiki/{page.slug}/edit",
        f"/wiki/{page.slug}/history",
    ]:
        assert client.get(url).status_code == 200, url


def test_missing_wiki_page_offers_creation(client):
    response = client.get("/wiki/does-not-exist")
    assert response.status_code == 404
    assert "Pagina aanmaken" in response.get_data(as_text=True)


def test_non_admin_cannot_manage_users(app):
    client = app.test_client()
    client.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert client.get("/users/").status_code == 403


# ------------------------------------------------------------------ security
def _login(client, password, username="admin"):
    return client.post("/auth/login", data={"username": username, "password": password})


def test_login_is_blocked_after_too_many_failures(app):
    client = app.test_client()
    for _ in range(5):
        assert _login(client, "wrong").status_code == 200
    response = _login(client, "secret123")                       # right password, but locked now
    assert response.status_code == 429 and "Te veel mislukte" in response.get_data(as_text=True)
    other = app.test_client()                                    # other user names still work
    assert _login(other, "demo1234", "jan").status_code == 302


def test_successful_login_resets_user_failures(app):
    client = app.test_client()
    for _ in range(4):
        _login(client, "wrong")
    assert _login(client, "secret123").status_code == 302
    client.post("/auth/logout")
    for _ in range(4):
        _login(client, "wrong")
    assert _login(client, "secret123").status_code == 302


def test_many_failures_from_one_address_are_blocked(app):
    from app.blueprints import auth
    client = app.test_client()
    for i in range(auth.MAX_IP_FAILURES):
        _login(client, "wrong", f"nobody{i}")
    assert _login(client, "demo1234", "jan").status_code == 429


def test_security_headers_and_csp_nonce(client):
    response = client.get("/notes/new")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp
    nonce = csp.split("'nonce-")[1].split("'")[0]
    html = response.get_data(as_text=True)
    assert f'<script nonce="{nonce}">' in html
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in response.headers               # only in HTTPS mode


def test_no_inline_event_handlers_in_templates():
    import pathlib
    import re
    for path in pathlib.Path("app/templates").rglob("*.html"):
        text = path.read_text()
        assert not re.search(r"\son[a-z]+=\"", text), path               # blocked by the CSP
        assert "<script>" not in text, path                               # needs a nonce


def test_https_mode_requires_strong_secret_and_secures_cookies():
    import pytest
    from app import create_app
    from app.config import TestConfig

    class Weak(TestConfig):
        HTTPS = True
        SECRET_KEY = "short"

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Weak)

    class Strong(TestConfig):
        HTTPS = True
        SECRET_KEY = "x" * 64

    app = create_app(Strong)
    assert app.config["SESSION_COOKIE_SECURE"] and app.config["REMEMBER_COOKIE_SECURE"]
    with app.app_context():
        from app.extensions import db
        db.create_all()
        response = app.test_client().get("/auth/login", headers={"X-Forwarded-Proto": "https"})
        assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
