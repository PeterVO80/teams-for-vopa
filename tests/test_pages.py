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
