"""Functional tests for the business rules of each module."""

from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from openpyxl import Workbook, load_workbook

from app.extensions import db
from app.models import (
    Article,
    Assignment,
    Note,
    OrderStatus,
    PurchaseOrder,
    Tag,
    Task,
    TaskStatus,
    WikiPage,
)
from app.services.markdown_render import render_markdown, slugify


# ------------------------------------------------------------- punch list
def test_kanban_move_updates_status_and_order(client):
    tasks = db.session.scalars(sa.select(Task).where(Task.status == TaskStatus.NEW)).all()
    moved = tasks[0]
    response = client.post(
        f"/tasks/{moved.id}/move",
        json={"status": "in_progress", "order": [moved.id]},
    )
    assert response.status_code == 200
    db.session.refresh(moved)
    assert moved.status == TaskStatus.IN_PROGRESS
    assert moved.position == 0


def test_kanban_move_rejects_unknown_status(client):
    task = db.session.scalar(sa.select(Task))
    assert client.post(f"/tasks/{task.id}/move", json={"status": "bogus"}).status_code == 400


def test_create_task(client):
    response = client.post(
        "/tasks/new",
        data={"title": "Schema nalezen", "project_id": 0, "assignee_id": 0,
              "status": "new", "priority": "high"},
    )
    assert response.status_code == 302
    task = db.session.scalar(sa.select(Task).filter_by(title="Schema nalezen"))
    assert task.project_id is None and task.priority.value == "high"


# ------------------------------------------------------------- purchasing
def test_partial_and_full_delivery_update_order_status(client):
    order = db.session.scalar(sa.select(PurchaseOrder))
    first, second = order.lines
    client.post(f"/purchasing/lines/{first.id}/receive",
                data={"quantity": "1", "received_on": "2026-11-01"})
    db.session.refresh(order)
    assert order.status == OrderStatus.PARTIAL

    client.post(f"/purchasing/{order.id}/receive-all")
    db.session.refresh(order)
    assert order.status == OrderStatus.DELIVERED
    assert order.delivery_progress == 100
    assert all(line.quantity_outstanding == 0 for line in order.lines)


def test_add_order_line_from_article(client):
    order = db.session.scalar(sa.select(PurchaseOrder))
    article = db.session.scalar(sa.select(Article).filter_by(article_number="A-3001"))
    client.post(f"/purchasing/{order.id}/lines", data={
        "article_id": article.id, "description": article.description,
        "quantity": "10", "unit": "st", "unit_price": "48.20",
    })
    db.session.refresh(order)
    assert order.lines[-1].article_id == article.id
    assert order.lines[-1].line_total == Decimal("482.00")


def test_order_excel_export(client):
    order = db.session.scalar(sa.select(PurchaseOrder))
    response = client.get(f"/purchasing/{order.id}/export")
    assert response.status_code == 200
    ws = load_workbook(BytesIO(response.data)).active
    assert ws["B1"].value == order.number


# --------------------------------------------------------------- articles
def test_article_export_import_round_trip(client):
    response = client.get("/articles/export")
    wb = load_workbook(BytesIO(response.data))
    ws = wb.active
    assert ws["A1"].value == "Artikelnummer"
    ws["H2"] = "999,95"                       # change a price (Belgian notation)
    ws.append(["A-9999", "Nieuw relais", "Finder", "40.52", None, "Relais", "st", 7.5])

    upload = BytesIO()
    wb.save(upload)
    upload.seek(0)
    response = client.post(
        "/articles/import",
        data={"file": (upload, "artikelen.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    first = ws["A2"].value
    assert db.session.scalar(sa.select(Article).filter_by(article_number=first)).unit_price == Decimal("999.95")
    new = db.session.scalar(sa.select(Article).filter_by(article_number="A-9999"))
    assert new.manufacturer_ref == "40.52" and new.unit_price == Decimal("7.50")


def test_import_reports_invalid_rows(client):
    wb = Workbook()
    wb.active.append(["Artikelnummer", "Omschrijving", "Eenheidsprijs"])
    wb.active.append(["B-1", None, 5])          # new article without description
    wb.active.append(["B-2", "Geldig", "abc"])  # invalid price
    wb.active.append(["B-3", "Geldig", 5])
    upload = BytesIO()
    wb.save(upload)
    upload.seek(0)
    html = client.post("/articles/import", data={"file": (upload, "x.xlsx")},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert "Rij 2" in html and "Rij 3" in html
    assert db.session.scalar(sa.select(Article).filter_by(article_number="B-3")) is not None
    assert db.session.scalar(sa.select(Article).filter_by(article_number="B-2")) is None


def test_article_search_json(client):
    data = client.get("/articles/search.json?q=S7-1200").get_json()
    assert data and data[0]["unit_price"] == 385.0


# ------------------------------------------------------------- team planning
def test_assignment_end_before_start_is_rejected(client):
    count = db.session.scalar(sa.select(sa.func.count(Assignment.id)))
    client.post("/team/assignments/new", data={
        "user_id": 1, "kind": "leave", "project_id": 0,
        "start_date": "2026-11-20", "end_date": "2026-11-10", "hours_per_day": 8,
    })
    assert db.session.scalar(sa.select(sa.func.count(Assignment.id))) == count


def test_workload_levels(app):
    from datetime import date
    from app.services.workload import build_overview, monday_of

    days, rows = build_overview(monday_of(date.today()), 1)
    by_name = {r.user.username: r for r in rows}
    # Seed: Jan 8h/day on P1, Els 4h + 4h on two projects -> both fully booked.
    assert by_name["jan"].cells[0].level(8) == "full"
    assert by_name["els"].cells[0].planned_hours == 8
    # Tom is on leave from Wednesday.
    assert by_name["tom"].cells[2].level(8) == "absent"
    assert len(days) == 5


# ---------------------------------------------------------- notes & wiki
def test_note_tags_are_created_and_reused(client):
    client.post("/notes/new", data={
        "title": "Overleg", "project_id": 0, "tags": "status meeting, Nieuw label",
        "content": "**Belangrijk**",
    })
    note = db.session.scalar(sa.select(Note).filter_by(title="Overleg"))
    assert sorted(t.name for t in note.tags) == ["Nieuw label", "Status meeting"]
    assert db.session.scalar(sa.select(sa.func.count(Tag.id)).where(Tag.name.ilike("status meeting"))) == 1


def test_wiki_edit_creates_revision_and_restore(client):
    page = db.session.scalar(sa.select(WikiPage).filter_by(slug="naamgeving-tags"))
    original = page.content
    client.post(f"/wiki/{page.slug}/edit", data={"title": "Naamgeving tags", "parent_id": page.parent_id, "content": "Nieuw"})
    db.session.refresh(page)
    assert page.content == "Nieuw" and len(page.revisions) == 1

    client.post(f"/wiki/revision/{page.revisions[0].id}/restore")
    db.session.refresh(page)
    assert page.content == original and len(page.revisions) == 2


def test_wiki_cannot_become_its_own_parent(client):
    root = db.session.scalar(sa.select(WikiPage).filter_by(slug="siemens-tia-portal"))
    child = root.children[0]
    response = client.post(f"/wiki/{root.slug}/edit", data={"title": root.title, "parent_id": child.id, "content": ""})
    assert response.status_code == 200  # form re-rendered with a validation error
    db.session.refresh(root)
    assert root.parent_id is None


def test_markdown_is_sanitised(app):
    with app.test_request_context():
        html = str(render_markdown("<script>alert(1)</script>\n\n**ok**"))
        assert "<script>" not in html and "<strong>ok</strong>" in html
        linked = str(render_markdown("Zie \\[\\[Naamgeving tags\\]\\]", wiki_links=True))
        assert 'href="/wiki/naamgeving-tags"' in linked


def test_slugify():
    assert slugify("Siemens S7 – Tips & Tricks") == "siemens-s7-tips-tricks"
