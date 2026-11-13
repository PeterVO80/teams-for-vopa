"""Functional tests for the business rules of each module."""

import re
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
    # The grid shows the full week; an empty weekend day is "weekend", not "free".
    assert len(days) == 7
    assert by_name["els"].cells[6].level(8) == "weekend"
    # Jan works 6h on Saturday (include_weekends) on top of 5 x 8h -> 46/40.
    assert by_name["jan"].cells[5].planned_hours == 6
    assert by_name["jan"].period_load(8) == 115
    # Tom's leave runs Wed -> next Wed but does not mark his weekend as absent.
    assert by_name["tom"].cells[5].level(8) == "weekend"


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
    client.post(f"/wiki/{page.slug}/edit", data={
        "title": "Naamgeving tags", "project_id": 0, "parent_id": page.parent_id, "content": "Nieuw",
    })
    db.session.refresh(page)
    assert page.content == "Nieuw" and len(page.revisions) == 1

    client.post(f"/wiki/revision/{page.revisions[0].id}/restore")
    db.session.refresh(page)
    assert page.content == original and len(page.revisions) == 2


def test_wiki_cannot_become_its_own_parent(client):
    root = db.session.scalar(sa.select(WikiPage).filter_by(slug="siemens-tia-portal"))
    child = root.children[0]
    response = client.post(f"/wiki/{root.slug}/edit", data={
        "title": root.title, "project_id": 0, "parent_id": child.id, "content": "",
    })
    assert response.status_code == 200  # form re-rendered with a validation error
    db.session.refresh(root)
    assert root.parent_id is None


def test_markdown_is_sanitised(app):
    with app.test_request_context():
        html = str(render_markdown("<script>alert(1)</script>\n\n**ok**"))
        assert "<script>" not in html and "<strong>ok</strong>" in html
        linked = str(render_markdown("Zie \\[\\[Naamgeving tags\\]\\]", wiki_links=True))
        assert 'href="/wiki/go?title=Naamgeving+tags"' in linked


def test_slugify():
    assert slugify("Siemens S7 – Tips & Tricks") == "siemens-s7-tips-tricks"
    assert slugify("PRJ/2026/0001 Netwerk") == "prj-2026-0001-netwerk"


# ------------------------------------------------------- automatic numbering
def test_new_project_gets_next_code_and_ignores_form_input(client):
    from datetime import date

    from app.models import Project

    year = date.today().year
    # Demo data already created PRJ/<year>/0001 and 0002.
    response = client.post("/projects/new", data={
        "code": "HACK-1", "name": "Nieuwe lijn", "status": "active", "manager_id": 0,
    })
    assert response.status_code == 302
    project = db.session.scalar(sa.select(Project).filter_by(name="Nieuwe lijn"))
    assert project.code == f"PRJ/{year}/0003"


def test_project_code_does_not_change_on_edit(client):
    from app.models import Project

    project = db.session.scalar(sa.select(Project))
    original = project.code
    client.post(f"/projects/{project.id}/edit", data={
        "name": "Hernoemd", "status": "active", "manager_id": 0,
    })
    db.session.refresh(project)
    assert project.name == "Hernoemd" and project.code == original


def test_next_number_sequence_rules(app):
    from app.models import Project
    from app.services.numbering import next_number

    db.session.add_all([
        Project(code="PRJ/2025/0042", name="Vorig jaar"),
        Project(code="PRJ/2030/9999", name="Hoog"),
        Project(code="PRJ/2030/10000", name="Voorbij 4 cijfers"),
        Project(code="P2030-777", name="Oude handmatige code"),
    ])
    db.session.commit()
    # Numbers are compared numerically, old-style codes are ignored.
    assert next_number(Project.code, "PRJ", year=2030) == "PRJ/2030/10001"
    # Every year starts again at 0001.
    assert next_number(Project.code, "PRJ", year=2031) == "PRJ/2031/0001"


def test_concurrent_code_collision_is_retried(app):
    """Simulate a colleague taking the same code between computing and saving."""
    from datetime import date

    from app.models import Project
    from app.services.numbering import insert_with_unique_number

    year = date.today().year
    project = Project(name="Gelijktijdig")
    attempts = []

    def assign():
        # First attempt deliberately reuses an existing code -> UNIQUE violation.
        attempts.append(1)
        if len(attempts) == 1:
            project.code = f"PRJ/{year}/0001"
        else:
            project.assign_code()

    insert_with_unique_number(project, assign)
    db.session.commit()
    assert len(attempts) == 2 and project.code == f"PRJ/{year}/0003"


def test_order_number_suggestion(client):
    from datetime import date

    html = client.get("/purchasing/new").get_data(as_text=True)
    assert f"PO-{date.today().year}-0102" in html  # demo order is PO-<year>-0101


# -------------------------------------------------- planning popup (modal)
MODAL = {"X-Modal": "1"}


def test_planning_popup_prefills_dragged_range(client):
    html = client.get("/team/assignments/new?user=2&start=2026-11-18&end=2026-11-16", headers=MODAL).get_data(as_text=True)
    assert "<html" not in html and "modal-header" in html          # fragment, not a page
    assert 'value="2026-11-16"' in html and 'value="2026-11-18"' in html  # right-to-left drag swapped
    assert '<option selected value="2">' in html


def test_planning_popup_single_day(client):
    html = client.get("/team/assignments/new?user=2&day=2026-11-16", headers=MODAL).get_data(as_text=True)
    assert html.count('value="2026-11-16"') == 2  # start and end


def test_planning_popup_save_returns_json(client):
    count = db.session.scalar(sa.select(sa.func.count(Assignment.id)))
    response = client.post("/team/assignments/new", headers=MODAL, data={
        "user_id": 2, "kind": "training", "project_id": 0,
        "start_date": "2026-12-01", "end_date": "2026-12-03", "hours_per_day": 8,
    })
    assert response.status_code == 200 and response.get_json() == {"ok": True}
    assert db.session.scalar(sa.select(sa.func.count(Assignment.id))) == count + 1


def test_planning_popup_validation_error_returns_422(client):
    response = client.post("/team/assignments/new", headers=MODAL, data={
        "user_id": 2, "kind": "project", "project_id": 0,
        "start_date": "2026-12-01", "end_date": "2026-12-03", "hours_per_day": 8,
    })
    assert response.status_code == 422
    assert "Kies een project" in response.get_data(as_text=True)


def test_planning_popup_delete(client):
    assignment = db.session.scalar(sa.select(Assignment))
    response = client.post(f"/team/assignments/{assignment.id}/delete", headers=MODAL)
    assert response.get_json() == {"ok": True}
    assert db.session.get(Assignment, assignment.id) is None


# ------------------------------------------------------------ weekend planning
def test_assignment_skips_weekends_unless_included(app):
    from datetime import date

    a = Assignment(start_date=date(2026, 11, 13), end_date=date(2026, 11, 16))  # Fri -> Mon
    assert a.covers(date(2026, 11, 13)) and not a.covers(date(2026, 11, 14)) and a.planned_days == 2
    a.include_weekends = True
    assert a.covers(date(2026, 11, 14)) and a.planned_days == 4


def test_planning_popup_preselects_weekends_when_dragging_onto_saturday(client):
    html = client.get("/team/assignments/new?user=2&start=2026-11-13&end=2026-11-14", headers=MODAL).get_data(as_text=True)
    checked = re.compile(r'<input checked[^>]*id="include_weekends"')
    assert checked.search(html)
    html = client.get("/team/assignments/new?user=2&start=2026-11-16&end=2026-11-18", headers=MODAL).get_data(as_text=True)
    assert 'id="include_weekends"' in html and not checked.search(html)


def test_weekend_only_period_requires_weekend_option(client):
    data = {"user_id": 2, "kind": "training", "project_id": 0,
            "start_date": "2026-11-14", "end_date": "2026-11-15", "hours_per_day": 8}
    response = client.post("/team/assignments/new", headers=MODAL, data=data)
    assert response.status_code == 422 and "enkel weekenddagen" in response.get_data(as_text=True)
    response = client.post("/team/assignments/new", headers=MODAL, data={**data, "include_weekends": "y"})
    assert response.get_json() == {"ok": True}


def test_team_grid_shows_weekend_columns(client):
    html = client.get("/team/?start=2026-11-09&weeks=1").get_data(as_text=True)
    assert "za<br>14/11" in html and "zo<br>15/11" in html


# ------------------------------------------------------------- project wikis
def _project(code_suffix="0001"):
    from datetime import date

    from app.models import Project

    return db.session.scalar(sa.select(Project).filter_by(code=f"PRJ/{date.today().year}/{code_suffix}"))


def test_create_project_wiki_page(client):
    project = _project()
    response = client.post("/wiki/new", data={
        "title": "I/O-lijst", "project_id": project.id, "parent_id": 0, "content": "DI_ = ingang",
    })
    page = db.session.scalar(sa.select(WikiPage).filter_by(title="I/O-lijst"))
    assert response.status_code == 302 and page.project_id == project.id
    assert page.slug == f"prj-{project.code.split('/')[1]}-0001-i-o-lijst"
    # Listed in the project wiki and on the project page, not in the general wiki.
    assert "I/O-lijst" in client.get(f"/wiki/project/{project.id}").get_data(as_text=True)
    assert "I/O-lijst" in client.get(f"/projects/{project.id}").get_data(as_text=True)
    assert "I/O-lijst" not in client.get("/wiki/").get_data(as_text=True).split("Recent gewijzigd")[0]


def test_same_title_in_two_project_wikis(client):
    p1, p2 = _project("0001"), _project("0002")
    for project in (p1, p2):
        client.post("/wiki/new", data={"title": "Netwerk", "project_id": project.id, "parent_id": 0, "content": ""})
    pages = db.session.scalars(sa.select(WikiPage).filter_by(title="Netwerk")).all()
    assert len(pages) == 2 and len({p.slug for p in pages}) == 2


def test_wiki_link_prefers_same_project(client):
    p1, p2 = _project("0001"), _project("0002")
    for project in (p1, p2):
        client.post("/wiki/new", data={"title": "Netwerk", "project_id": project.id, "parent_id": 0, "content": ""})
    target = db.session.scalar(sa.select(WikiPage).filter_by(title="Netwerk", project_id=p2.id))
    response = client.get(f"/wiki/go?title=netwerk&project={p2.id}")
    assert response.headers["Location"].endswith(f"/wiki/{target.slug}")
    # Falls back to the general wiki ...
    response = client.get(f"/wiki/go?title=Naamgeving%20tags&project={p2.id}")
    assert response.headers["Location"].endswith("/wiki/naamgeving-tags")
    # ... and offers to create unknown pages in the same project wiki.
    response = client.get(f"/wiki/go?title=Nieuw%20onderwerp&project={p2.id}")
    assert "/wiki/new?" in response.headers["Location"] and f"project={p2.id}" in response.headers["Location"]


def test_parent_must_be_in_same_wiki(client):
    project = _project()
    general = db.session.scalar(sa.select(WikiPage).filter_by(slug="siemens-tia-portal"))
    response = client.post("/wiki/new", data={
        "title": "Fout", "project_id": project.id, "parent_id": general.id, "content": "",
    })
    assert response.status_code == 200 and "dezelfde wiki" in response.get_data(as_text=True)
    assert db.session.scalar(sa.select(WikiPage).filter_by(title="Fout")) is None


def test_moving_page_to_project_wiki_moves_sub_pages(client):
    project = _project()
    root = db.session.scalar(sa.select(WikiPage).filter_by(slug="siemens-tia-portal"))
    client.post(f"/wiki/{root.slug}/edit", data={
        "title": root.title, "project_id": project.id, "parent_id": 0, "content": root.content,
    })
    db.session.refresh(root)
    assert root.project_id == project.id and root.slug.startswith("prj-")
    assert all(child.project_id == project.id for child in root.children)
