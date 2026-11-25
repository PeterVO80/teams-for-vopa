"""Access per module: none, read or write (app/modules.py, app/permissions.py)."""

import json
import re

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Note, Project, Task, User
from app.modules import FULL_ACCESS, dump_access, parse_access

MODAL = {"X-Modal": "1"}


def user(username):
    return db.session.scalar(sa.select(User).filter_by(username=username))


def login(app, username):
    client = app.test_client()
    assert client.post("/auth/login", data={"username": username, "password": "demo1234"}).status_code == 302
    return client


def set_access(username, **levels):
    user(username).module_access = dump_access(levels)
    db.session.commit()


@pytest.fixture()
def lies(app):
    return login(app, "lies")


def test_parse_and_dump():
    assert parse_access("projects:w, stock:r,bogus:w,tasks:x") == {"projects": "w", "stock": "r"}
    assert dump_access({"stock": "r", "projects": "w", "x": "w"}) == "projects:w,stock:r"
    assert parse_access(FULL_ACCESS)["wiki"] == "w"


def test_new_and_existing_users_have_full_access(app):
    assert user("lies").module_access == FULL_ACCESS
    assert user("lies").can_write("stock") and user("admin").can_write("stock")


def test_no_access_hides_module_and_answers_403(app, lies):
    set_access("lies", projects="w", tasks="w")
    html = lies.get("/").get_data(as_text=True)
    assert 'href="/stock/"' not in html and 'href="/projects/"' in html
    assert "Onder minimumstock" not in html and "Verwachte leveringen" not in html
    assert lies.get("/stock/").status_code == 403
    assert lies.get("/purchasing/").status_code == 403
    assert lies.get("/articles/search.json?q=kabel").status_code != 403     # shared helper of the order form


def test_read_only_can_view_but_not_change(app, lies):
    set_access("lies", projects="w", tasks="r", notes="r")
    assert lies.get("/tasks/").status_code == 200
    task = db.session.scalars(sa.select(Task).join(Project).where(Project.name == "Waterzuivering SCADA")).first()
    assert lies.get(f"/tasks/{task.id}/edit").status_code == 200              # view an existing task
    assert lies.get("/tasks/new").status_code == 403                          # but not create one
    response = lies.post(f"/tasks/{task.id}/edit", data={"title": "Gewijzigd", "status": "new", "priority": "normal"})
    assert response.status_code == 403
    assert db.session.get(Task, task.id).title != "Gewijzigd"
    assert lies.post(f"/tasks/{task.id}/delete").status_code == 403
    note = db.session.scalars(sa.select(Note)).first()
    assert lies.post(f"/notes/{note.id}/delete").status_code == 403
    assert db.session.get(Note, note.id) is not None

    # The page carries the rules for static/js/read_only.js.
    html = lies.get("/tasks/").get_data(as_text=True)
    rules = json.loads(re.search(r'<script type="application/json" id="read-only-rules">(.*?)</script>', html).group(1))
    assert any(re.match(r, "/tasks/new") for r in rules["hide"])
    assert not any(re.match(r, f"/tasks/{task.id}/edit") for r in rules["hide"])   # viewing stays possible
    assert any(re.match(r, f"/tasks/{task.id}/edit") for r in rules["post"])       # but its form is read-only
    assert not any(re.match(r, "/projects/new") for r in rules["hide"])            # projects: write access
    assert "data-read-only" in html                                                 # kanban: no dragging


def test_project_page_shows_only_readable_tabs(app, lies):
    set_access("lies", projects="r", timesheet="w")
    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    html = lies.get(f"/projects/{project.id}").get_data(as_text=True)
    assert 'data-bs-target="#tab-hours"' in html and 'data-bs-target="#tab-tasks"' not in html
    assert f"/projects/{project.id}/edit" not in html                      # read only: no edit button
    assert lies.get(f"/projects/{project.id}/delete").status_code == 403


def test_admins_and_full_access_get_no_rules(client, lies):
    assert "read-only-rules" not in client.get("/").get_data(as_text=True)
    assert "read-only-rules" not in lies.get("/").get_data(as_text=True)


def test_user_form_stores_access(client):
    lies = user("lies")
    html = client.get(f"/users/{lies.id}/edit").get_data(as_text=True)
    assert 'name="write_stock"' in html and "Toegang per module" in html
    data = {"username": "lies", "full_name": lies.full_name, "role": "member", "color": lies.color, "active": "y",
            "read_projects": "y", "write_projects": "y", "read_stock": "y", "write_wiki": "y"}
    response = client.post(f"/users/{lies.id}/edit", data=data)
    assert response.status_code == 302
    db.session.refresh(lies)
    assert parse_access(lies.module_access) == {"projects": "w", "stock": "r", "wiki": "w"}   # write includes read
    assert "2 schrijven, 1 lezen" in client.get("/users/").get_data(as_text=True)


def test_new_user_form_defaults_to_full_access(client):
    html = client.get("/users/new").get_data(as_text=True)
    assert re.search(r'name="write_projects"[^>]*checked', html)
