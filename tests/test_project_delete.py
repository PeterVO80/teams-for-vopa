"""Deleting a project (administrators only)."""

from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db
from app.models import (Article, Assignment, Note, OutlookLink, Project, PurchaseOrder, StockLocation, StockMovement,
                        Tag, Task, User, WikiPage, WikiRevision)
from app.models.stock import MovementKind


def _project():
    return db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))


def _fill(project):
    admin = db.session.scalar(sa.select(User).filter_by(username="admin"))
    parent = WikiPage(title="Ouder", slug="p-ouder", content="a", project=project)
    db.session.add(parent)
    db.session.flush()
    child = WikiPage(title="Kind", slug="p-kind", content="b", project=project, parent_id=parent.id)
    db.session.add(child)
    db.session.flush()
    db.session.add(WikiRevision(page_id=child.id, title="Kind", content="b", author_id=admin.id))
    note = Note(title="Overleg", content="x", project=project, author_id=admin.id)
    note.tags.append(Tag(name="Testlabel verwijderen"))
    db.session.add(note)
    db.session.add(Task(title="Projecttaak", project=project, created_by_id=admin.id))
    location = StockLocation(name="Magazijn test")
    article = Article(article_number="DEL-1", description="Klemmen")
    db.session.add_all([location, article])
    db.session.flush()
    db.session.add(StockMovement(kind=MovementKind.ISSUE, location_id=location.id, project_id=project.id,
                                 article_id=article.id, quantity=Decimal("-2"), moved_on=date.today()))
    db.session.commit()


def test_only_admins_can_delete(app):
    project = _project()
    jan = app.test_client()
    jan.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert jan.get(f"/projects/{project.id}/delete").status_code == 403
    assert jan.post(f"/projects/{project.id}/delete", data={"confirm": "VERWIJDER"}).status_code == 403
    jan_user = db.session.scalar(sa.select(User).filter_by(username="jan"))
    project.add_member(jan_user)
    db.session.commit()
    assert f"/projects/{project.id}/delete" not in jan.get(f"/projects/{project.id}").get_data(as_text=True)


def test_admin_sees_delete_button(client):
    project = _project()
    assert f"/projects/{project.id}/delete" in client.get(f"/projects/{project.id}").get_data(as_text=True)


def test_confirmation_page_shows_what_happens(client):
    project = _project()
    html = client.get(f"/projects/{project.id}/delete").get_data(as_text=True)
    assert project.code in html and "Wordt definitief verwijderd" in html
    response = client.post(f"/projects/{project.id}/delete", data={"confirm": "nee"})
    assert response.status_code == 200 and db.session.get(Project, project.id) is not None


def test_delete_removes_project_content_and_keeps_orders(client):
    project = _project()
    pid = project.id
    orders = db.session.scalar(sa.select(sa.func.count(PurchaseOrder.id)).where(PurchaseOrder.project_id == pid))
    assignments = db.session.scalars(sa.select(Assignment.id).where(Assignment.project_id == pid)).all()
    assert assignments
    db.session.add(OutlookLink(assignment_id=assignments[0], user_id=1, event_id="evt",
                               start_date=date.today(), end_date=date.today()))
    _fill(project)
    total_orders = db.session.scalar(sa.select(sa.func.count(PurchaseOrder.id)))
    total_movements = db.session.scalar(sa.select(sa.func.count(StockMovement.id)))

    response = client.post(f"/projects/{pid}/delete", data={"confirm": "verwijder"})
    assert response.status_code == 302 and response.location.endswith("/projects/")
    db.session.expire_all()
    assert db.session.get(Project, pid) is None
    for model in (Task, Assignment, WikiPage, Note):
        assert db.session.scalar(sa.select(sa.func.count(model.id)).where(model.project_id == pid)) == 0
    assert db.session.scalar(sa.select(sa.func.count(WikiRevision.id)).where(
        ~WikiRevision.page_id.in_(sa.select(WikiPage.id)))) == 0
    # Orders and stock movements are kept, without project.
    assert db.session.scalar(sa.select(sa.func.count(PurchaseOrder.id))) == total_orders
    assert db.session.scalar(sa.select(sa.func.count(StockMovement.id))) == total_movements
    assert db.session.scalar(sa.select(StockMovement).join(Article).filter(Article.article_number == "DEL-1")).project_id is None
    if orders:
        assert db.session.scalar(sa.select(sa.func.count(PurchaseOrder.id)).where(PurchaseOrder.project_id.is_(None))) >= orders
    # The Outlook appointment of the deleted planning is queued for removal.
    assert db.session.scalar(sa.select(OutlookLink)).assignment_id is None
    assert db.session.scalar(sa.select(Tag).filter_by(name="Testlabel verwijderen")) is not None
