"""Project teams and visibility.

Demo data: project "Verpakkingslijn L3" (Brouwerij De Kroon): manager Jan,
members Els and Tom. Project "Waterzuivering SCADA" (Aquafin): manager Els,
member Lies. Administrators see everything.
"""

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Assignment, AssignmentKind, Note, Project, PurchaseOrder, Task, User, WikiPage

KROON = "Verpakkingslijn L3"
AQUAFIN = "Waterzuivering SCADA"


def login(app, username):
    client = app.test_client()
    response = client.post("/auth/login", data={"username": username, "password": "demo1234"})
    assert response.status_code == 302
    return client


def project(name):
    return db.session.scalar(sa.select(Project).filter_by(name=name))


def user(username):
    return db.session.scalar(sa.select(User).filter_by(username=username))


@pytest.fixture()
def lies(app):
    return login(app, "lies")


def test_project_list_and_detail(lies, client):
    html = lies.get("/projects/?status=all").get_data(as_text=True)
    assert AQUAFIN in html and KROON not in html
    assert lies.get(f"/projects/{project(KROON).id}").status_code == 404
    assert lies.get(f"/projects/{project(AQUAFIN).id}").status_code == 200
    # Administrators see everything.
    html = client.get("/projects/?status=all").get_data(as_text=True)
    assert AQUAFIN in html and KROON in html


def test_project_manager_counts_as_linked(app):
    jan = login(app, "jan")                         # manager of KROON, not a stored member
    assert jan.get(f"/projects/{project(KROON).id}").status_code == 200
    assert jan.get(f"/projects/{project(AQUAFIN).id}").status_code == 404


def test_kanban_notes_orders_and_dashboard(lies):
    board = lies.get("/tasks/?all=1").get_data(as_text=True)
    assert "SCADA tags importeren" in board and "HMI schermen tekenen" not in board
    kroon_task = db.session.scalar(sa.select(Task).filter_by(title="HMI schermen tekenen"))
    assert lies.get(f"/tasks/{kroon_task.id}/edit").status_code == 404
    assert lies.post(f"/tasks/{kroon_task.id}/move", json={"status": "done"}).status_code == 404

    notes = lies.get("/notes/").get_data(as_text=True)
    assert "Kick-off verpakkingslijn L3" not in notes
    note = db.session.scalar(sa.select(Note).filter_by(title="Kick-off verpakkingslijn L3"))
    assert lies.get(f"/notes/{note.id}").status_code == 404

    order = db.session.scalar(sa.select(PurchaseOrder))      # belongs to KROON
    assert order.number not in lies.get("/purchasing/?status=all").get_data(as_text=True)
    assert lies.get(f"/purchasing/{order.id}").status_code == 404
    assert lies.get(f"/purchasing/material?project={project(KROON).id}").status_code == 404

    dashboard = lies.get("/").get_data(as_text=True)
    assert "Kick-off verpakkingslijn L3" not in dashboard


def test_project_selection_lists_are_limited(lies):
    html = lies.get("/tasks/new").get_data(as_text=True)
    assert f">{project(AQUAFIN).display_name}</option>" in html
    assert f">{project(KROON).display_name}</option>" not in html


def test_project_wiki_is_hidden(lies):
    page = db.session.scalar(sa.select(WikiPage).where(WikiPage.project_id == project(KROON).id))
    assert lies.get(f"/wiki/{page.slug}").status_code == 404
    assert lies.get(f"/wiki/project/{project(KROON).id}").status_code == 404
    assert page.title not in lies.get(f"/wiki/search?q={page.title.split()[0]}").get_data(as_text=True)
    # The general wiki stays visible.
    assert lies.get("/wiki/naamgeving-tags").status_code == 200


def test_team_planning_shows_ingepland_for_other_projects(lies, client):
    html = lies.get("/team/").get_data(as_text=True)
    board = html.split('class="planning-grid', 1)[1]
    assert "Ingepland" in board
    assert "Brouwerij De Kroon" not in board and KROON not in board          # no customer / project
    assert "Aquafin - Waterzuivering SCADA" in board                          # own project
    assert "Verlof" in board                                                  # absences stay visible
    # Hidden bars cannot be opened or dragged.
    hidden = [chunk for chunk in board.split("<a class=\"planning-chip")[1:] if "locked" in chunk.split(">", 1)[0]]
    assert hidden and all("data-modal-url" not in c.split(">", 1)[0] and "data-assignment" not in c.split(">", 1)[0]
                          for c in hidden)
    # Administrators see every project name.
    assert "Brouwerij De Kroon - Verpakkingslijn L3" in client.get("/team/").get_data(as_text=True)
    jan_kroon = db.session.scalar(sa.select(Assignment).where(Assignment.project_id == project(KROON).id))
    assert lies.get(f"/team/assignments/{jan_kroon.id}/edit").status_code == 404
    assert lies.post(f"/team/assignments/{jan_kroon.id}/reschedule",
                     json={"start_date": "2026-12-01", "end_date": "2026-12-02"}).status_code == 404


def test_manage_team(app, client):
    kroon = project(KROON)
    # A member who isn't the manager cannot change the team ...
    tom = login(app, "tom")
    assert tom.post(f"/projects/{kroon.id}/members", data={"user_id": user("lies").id}).status_code == 403
    # ... the manager (and an admin) can.
    jan = login(app, "jan")
    jan.post(f"/projects/{kroon.id}/members", data={"user_id": user("lies").id})
    db.session.refresh(kroon)
    assert user("lies") in kroon.members
    assert login(app, "lies").get(f"/projects/{kroon.id}").status_code == 200
    client.post(f"/projects/{kroon.id}/members/{user('lies').id}/remove")
    db.session.refresh(kroon)
    assert user("lies") not in kroon.members
    html = client.get(f"/projects/{kroon.id}").get_data(as_text=True)
    assert "projectleider" in html and "medewerker toevoegen" in html


def test_planning_or_task_links_person_to_project(client):
    kroon = project(KROON)
    lies_user = user("lies")
    client.post("/team/assignments/new", headers={"X-Modal": "1"}, data={
        "user_id": lies_user.id, "kind": "project", "project_id": kroon.id,
        "start_date": "2026-12-07", "end_date": "2026-12-08", "hours_per_day": 8})
    db.session.refresh(kroon)
    assert lies_user in kroon.members

    aquafin = project(AQUAFIN)
    tom = user("tom")
    client.post("/tasks/new", data={"title": "Nieuwe taak", "project_id": aquafin.id, "assignee_id": tom.id,
                                     "status": "new", "priority": "normal"})
    db.session.refresh(aquafin)
    assert tom in aquafin.members


def test_employee_who_creates_a_project_sees_it(app):
    tom = login(app, "tom")
    tom.post("/projects/new", data={"name": "Project van Tom", "status": "active", "manager_id": 0, "customer_id": 0})
    created = db.session.scalar(sa.select(Project).filter_by(name="Project van Tom"))
    assert user("tom") in created.members
    assert tom.get(f"/projects/{created.id}").status_code == 200
