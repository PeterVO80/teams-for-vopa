"""Filters on the project list."""

import sqlalchemy as sa

from app.extensions import db
from app.models import Company, Project, ProjectStatus, User


def _rows(html):
    return html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


def test_search_in_code_name_description_and_customer(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    project.description = "Migratie van de PLC naar S7-1500"
    db.session.commit()
    for term in ("waterzuivering", project.code, "s7-1500", project.customer.name.lower()):
        rows = _rows(client.get("/projects/", query_string={"q": term}).get_data(as_text=True))
        assert "Waterzuivering SCADA" in rows, term
        assert "Verpakkingslijn L3" not in rows, term
    rows = _rows(client.get("/projects/?q=bestaatniet").get_data(as_text=True))
    assert "Geen projecten gevonden met deze filters" in rows


def test_filter_on_customer_manager_and_status(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    html = client.get("/projects/").get_data(as_text=True)
    assert f'<option value="{project.customer_id}"' in html          # customer dropdown

    rows = _rows(client.get(f"/projects/?customer={project.customer_id}&f=1").get_data(as_text=True))
    assert "Verpakkingslijn L3" in rows and "Waterzuivering SCADA" not in rows
    rows = _rows(client.get(f"/projects/?manager={project.manager_id}&f=1").get_data(as_text=True))
    assert "Verpakkingslijn L3" in rows

    project.status = ProjectStatus.CLOSED
    db.session.commit()
    assert "Verpakkingslijn L3" not in _rows(client.get("/projects/?f=1").get_data(as_text=True))   # default: open
    assert "Verpakkingslijn L3" in _rows(client.get("/projects/?status=closed").get_data(as_text=True))
    assert "Verpakkingslijn L3" in _rows(client.get("/projects/?status=all").get_data(as_text=True))


def test_employee_only_sees_customers_of_own_projects(app):
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    mine = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    other = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    other.members = [m for m in other.members if m.id != jan.id]
    if other.manager_id == jan.id:
        other.manager_id = None
    mine.add_member(jan)
    secret = Company(name="Geheime Klant NV", is_customer=True)
    db.session.add(secret)
    db.session.flush()
    other.customer_id = secret.id
    db.session.commit()
    client = app.test_client()
    client.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    html = client.get("/projects/?status=all").get_data(as_text=True)
    assert "Verpakkingslijn L3" in html
    assert "Geheime Klant NV" not in html
    assert "Geheime Klant NV" not in _rows(client.get("/projects/?status=all&q=geheime").get_data(as_text=True))


def test_note_cards_show_customer_and_project_name(client):
    from app.models import Note
    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    db.session.add(Note(title="Kaarttest", content="x", project=project, author_id=1))
    db.session.commit()
    html = client.get("/notes/").get_data(as_text=True)
    card = html.split("Kaarttest", 1)[1].split("card-footer", 1)[1][:600]
    assert project.customer_label in card                      # "Aquafin - Waterzuivering SCADA"
    assert f">{project.code}" not in card and f"{project.code} ·" not in card



# --------------------------------------------------- remembered filters (filter_memory.py)
def test_filters_are_remembered_between_pages(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    client.get(f"/projects/?q=&customer={project.customer_id}&manager=&status=all&f=1")
    client.get("/tasks/")                                         # somewhere else
    response = client.get("/projects/")                          # back via the menu
    assert response.status_code == 302
    assert f"customer={project.customer_id}" in response.location and "status=all" in response.location
    html = client.get(response.location).get_data(as_text=True)
    assert f'<option value="{project.customer_id}" selected' in html


def test_cleared_filters_and_unticked_checkboxes_are_remembered(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    client.get(f"/tasks/?project={project.id}&assignee=&show=all&f=1")
    location = client.get("/tasks/").location
    assert f"project={project.id}" in location and "show=all" in location
    client.get("/tasks/?project=&assignee=&show=recent&f=1")      # cleared project
    location = client.get("/tasks/").location
    assert "project=" not in location and "show=recent" in location
    # An unticked checkbox is remembered too.
    client.get("/articles/?q=&category=&inactive=1&f=1")
    assert "inactive=1" in client.get("/articles/").location
    client.get("/articles/?q=&category=&f=1")
    assert client.get("/articles/").status_code == 200            # nothing left to restore


def test_links_keep_their_own_values_and_reset_forgets(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    client.get("/projects/?q=lijn&status=all&f=1")
    # A link with one filter keeps it and gets the other remembered ones added.
    location = client.get(f"/projects/?customer={project.customer_id}").location
    assert f"customer={project.customer_id}" in location and "q=lijn" in location
    response = client.get("/projects/?reset=1")
    assert response.status_code == 302 and response.location.endswith("/projects/")
    assert client.get("/projects/").status_code == 200


def test_filters_are_per_user(app, client):
    client.get("/projects/?status=all&f=1")
    other = app.test_client()
    other.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert other.get("/projects/").status_code == 200


def test_week_or_month_view_is_remembered(client):
    client.get("/team/?view=month&start=2026-02-01")
    response = client.get("/team/?start=2026-03-02")             # e.g. a link from an Outlook appointment
    assert "view=month" in response.location and "start=2026-03-02" in response.location
