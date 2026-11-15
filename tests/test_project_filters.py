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

    rows = _rows(client.get(f"/projects/?customer={project.customer_id}").get_data(as_text=True))
    assert "Verpakkingslijn L3" in rows and "Waterzuivering SCADA" not in rows
    rows = _rows(client.get(f"/projects/?manager={project.manager_id}").get_data(as_text=True))
    assert "Verpakkingslijn L3" in rows

    project.status = ProjectStatus.CLOSED
    db.session.commit()
    assert "Verpakkingslijn L3" not in _rows(client.get("/projects/").get_data(as_text=True))       # default: open
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
