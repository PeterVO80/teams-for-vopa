"""Functional tests for the business rules of each module."""

import re
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from openpyxl import Workbook, load_workbook

from app.extensions import db
from app.models import (
    Company,
    User,
    Article,
    Assignment,
    AssignmentKind,
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
    headers = [c.value for c in ws[1]]
    price_col = headers.index("Eenheidsprijs") + 1
    ws.cell(row=2, column=price_col, value="999,95")   # change a price (Belgian notation)
    new_row = {"Artikelnummer": "A-9999", "Omschrijving": "Nieuw relais", "Fabrikant": "Finder",
               "Bestelcode fabrikant": "40.52", "Categorie": "Relais", "Eenheid": "st", "Eenheidsprijs": 7.5}
    ws.append([new_row.get(h) for h in headers])

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
        "code": "HACK-1", "name": "Nieuwe lijn", "status": "active", "manager_id": 0, "customer_id": 0,
    })
    assert response.status_code == 302
    project = db.session.scalar(sa.select(Project).filter_by(name="Nieuwe lijn"))
    assert project.code == f"PRJ/{year}/0003"


def test_project_code_does_not_change_on_edit(client):
    from app.models import Project

    project = db.session.scalar(sa.select(Project))
    original = project.code
    client.post(f"/projects/{project.id}/edit", data={
        "name": "Hernoemd", "status": "active", "manager_id": 0, "customer_id": project.customer_id,
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


# --------------------------------------------- material follow-up per project
def _article(number):
    return db.session.scalar(sa.select(Article).filter_by(article_number=number))


def _material_setup():
    """Demo order PO-..-0101 (ordered: 2x A-1001, 1x A-1002) + extra orders for the same project."""
    from app.models import PurchaseOrderLine

    order = db.session.scalar(sa.select(PurchaseOrder))
    project = order.project
    plc, sensor = _article("A-1001"), _article("A-1003") or _article("A-3001")
    second = PurchaseOrder(number="PO-T-2", project=project, supplier=Company.by_name("Rexel"), status=OrderStatus.ORDERED)
    second.lines.append(PurchaseOrderLine(article=plc, description=plc.description, quantity=Decimal(3),
                                          unit="st", unit_price=Decimal(385)))
    second.lines.append(PurchaseOrderLine(description="Montageplaat op maat", quantity=Decimal(1), unit="st"))
    draft = PurchaseOrder(number="PO-T-3", project=project, supplier=Company.by_name("ifm"), status=OrderStatus.DRAFT)
    draft.lines.append(PurchaseOrderLine(article=sensor, description=sensor.description, quantity=Decimal(10), unit="st"))
    cancelled = PurchaseOrder(number="PO-T-4", project=project, supplier=Company.by_name("Cebeo"), status=OrderStatus.CANCELLED)
    cancelled.lines.append(PurchaseOrderLine(article=plc, description="x", quantity=Decimal(99), unit="st"))
    db.session.add_all([second, draft, cancelled])
    db.session.commit()
    return project, order, second


def test_material_overview_groups_per_article(client):
    from app.services.material import project_material

    project, first, second = _material_setup()
    # Receive 1 of the 2 PLCs on the first order.
    plc_line = next(l for l in first.lines if l.article and l.article.article_number == "A-1001")
    client.post(f"/purchasing/lines/{plc_line.id}/receive", data={"quantity": "1", "received_on": "2026-11-10"})

    rows = {(r.article.article_number if r.article else r.description): r for r in project_material(project.id).rows}
    plc = rows["A-1001"]
    assert plc.qty_ordered == 5          # 2 + 3, the cancelled 99 is ignored
    assert plc.qty_received == 1 and plc.qty_outstanding == 4
    assert plc.status == "partial" and [o.number for o in plc.orders] == [first.number, "PO-T-2"]
    assert rows["A-3001"].status == "draft" and rows["A-3001"].qty_draft == 10
    assert rows["Montageplaat op maat"].article is None
    # Draft materials are listed first: they still need to be ordered.
    assert project_material(project.id).rows[0].status == "draft"


def test_material_only_open_and_pages(client):
    from app.services.material import project_material

    project, first, _ = _material_setup()
    client.post(f"/purchasing/{first.id}/receive-all")
    statuses = {r.status for r in project_material(project.id, only_open=True).rows}
    assert "delivered" not in statuses
    assert "Materiaal (" in client.get(f"/projects/{project.id}").get_data(as_text=True)
    html = client.get(f"/purchasing/material?project={project.id}").get_data(as_text=True)
    assert "A-1001" in html and "Montageplaat op maat" in html
    response = client.get(f"/purchasing/material/{project.id}.xlsx")
    ws = load_workbook(BytesIO(response.data)).active
    assert ws["A4"].value == "Artikelnummer" and ws.max_row >= 5


def test_add_article_to_order_popup(client):
    order = db.session.scalar(sa.select(PurchaseOrder))
    article = _article("A-3001")
    html = client.get(f"/purchasing/add-article/{article.id}", headers=MODAL).get_data(as_text=True)
    assert order.number in html and "modal-header" in html

    data = {"order_id": order.id, "quantity": "4", "unit_price": "48.20"}
    assert client.post(f"/purchasing/add-article/{article.id}", headers=MODAL, data=data).get_json() == {"ok": True}
    assert client.post(f"/purchasing/add-article/{article.id}", headers=MODAL, data=data).get_json() == {"ok": True}
    db.session.refresh(order)
    lines = [l for l in order.lines if l.article_id == article.id]
    assert len(lines) == 1 and lines[0].quantity == 8   # second time: quantity increased

    response = client.post(f"/purchasing/add-article/{article.id}", headers=MODAL, data={**data, "order_id": 0})
    assert response.status_code == 422 and "Kies een bestelling" in response.get_data(as_text=True)


def test_link_free_text_line_to_article_and_unlink(client):
    from app.models import PurchaseOrderLine

    order = db.session.scalar(sa.select(PurchaseOrder))
    order.lines.append(PurchaseOrderLine(description="Sensor M12", quantity=Decimal(2), unit="st"))
    db.session.commit()
    line = order.lines[-1]
    article = _article("A-3001")
    form = {"edit-description": "Sensor M12 PNP", "edit-quantity": "2", "edit-unit": "st", "edit-unit_price": "48.20"}

    html = client.get(f"/purchasing/lines/{line.id}/edit", headers=MODAL).get_data(as_text=True)
    assert 'name="edit-article_id"' in html
    response = client.post(f"/purchasing/lines/{line.id}/edit", headers=MODAL, data={**form, "edit-article_id": article.id})
    assert response.get_json() == {"ok": True}
    db.session.refresh(line)
    assert line.article_id == article.id and line.description == "Sensor M12 PNP"

    client.post(f"/purchasing/lines/{line.id}/edit", headers=MODAL, data={**form, "edit-article_id": ""})
    db.session.refresh(line)
    assert line.article_id is None


def test_article_page_shows_order_history(client):
    article = _article("A-1001")
    html = client.get(f"/articles/{article.id}/edit").get_data(as_text=True)
    order = db.session.scalar(sa.select(PurchaseOrder))
    assert "Bestelhistoriek" in html and order.number in html



# ---------------------------------------------- planning bars (team overview)
def _assignment(start, end, **kwargs):
    from datetime import date as d
    return Assignment(id=kwargs.pop("id", None), start_date=d.fromisoformat(start), end_date=d.fromisoformat(end), **kwargs)


def test_consecutive_days_become_one_bar(app):
    from datetime import date, timedelta
    from app.services.workload import build_segments

    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(14)]   # Mon 16/11 - Sun 29/11
    segments, lanes = build_segments([_assignment("2026-11-16", "2026-11-18", id=1)], days)
    assert [(s.first, s.last) for s in segments] == [(0, 2)] and lanes == 1


def test_bar_is_split_by_unplanned_weekend(app):
    from datetime import date, timedelta
    from app.services.workload import build_segments

    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(14)]
    two_weeks = _assignment("2026-11-16", "2026-11-27", id=1)
    segments, _ = build_segments([two_weeks], days)
    assert [(s.first, s.last) for s in segments] == [(0, 4), (7, 11)]
    two_weeks.include_weekends = True
    segments, _ = build_segments([two_weeks], days)
    assert [(s.first, s.last) for s in segments] == [(0, 11)]


def test_overlapping_bars_get_separate_lanes_and_edges(app):
    from datetime import date, timedelta
    from app.services.workload import build_segments

    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(7)]
    a = _assignment("2026-11-10", "2026-11-18", id=1)        # started last week
    b = _assignment("2026-11-17", "2026-11-25", id=2, include_weekends=True)  # runs on next week
    c = _assignment("2026-11-20", "2026-11-20", id=3)        # fits in lane 0 after a
    segments, lanes = build_segments([a, b, c], days)
    by_id = {s.assignment.id: s for s in segments}
    assert lanes == 2 and by_id[1].lane == 0 and by_id[2].lane == 1 and by_id[3].lane == 0
    assert by_id[1].continues_before and not by_id[1].continues_after
    assert by_id[2].continues_after


def test_bar_label_shows_customer_and_project(client):
    from app.models import Project
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    html = client.get("/team/").get_data(as_text=True)
    assert "Brouwerij De Kroon - Verpakkingslijn L3" in html
    assert project.code not in html.split("planning-chip", 1)[1].split("</a>", 1)[0]
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    assert f"background-color: {jan.color}" in html


def test_text_color_on_employee_colour(app):
    text_on = app.jinja_env.filters["text_on"]
    assert text_on("#0d6efd") == "#fff" and text_on("#ffc107") == "#212529"


# --------------------------------------- resize / move bars on the planning board
def test_reschedule_changes_dates_and_person(client):
    from datetime import date
    assignment = db.session.scalar(sa.select(Assignment).where(Assignment.kind == AssignmentKind.LEAVE))
    els = db.session.scalar(sa.select(User).filter_by(username="els"))
    response = client.post(f"/team/assignments/{assignment.id}/reschedule", json={
        "start_date": "2026-11-23", "end_date": "2026-11-27", "user_id": els.id})
    assert response.get_json() == {"ok": True}
    db.session.refresh(assignment)
    assert (assignment.start_date, assignment.end_date, assignment.user_id) == (date(2026, 11, 23), date(2026, 11, 27), els.id)


def test_reschedule_validation(client):
    assignment = db.session.scalar(sa.select(Assignment).where(Assignment.kind == AssignmentKind.LEAVE))
    url = f"/team/assignments/{assignment.id}/reschedule"
    response = client.post(url, json={"start_date": "2026-11-27", "end_date": "2026-11-23"})
    assert response.status_code == 400 and "einddatum" in response.get_json()["error"]
    response = client.post(url, json={"start_date": "2026-11-28", "end_date": "2026-11-29"})   # weekend only
    assert response.status_code == 400 and "weekenddagen" in response.get_json()["error"]
    response = client.post(url, json={"start_date": "x", "end_date": "2026-11-29"})
    assert response.status_code == 400
    response = client.post(url, json={"start_date": "2026-11-23", "end_date": "2026-11-24", "user_id": 9999})
    assert response.status_code == 400 and "medewerker" in response.get_json()["error"]


def test_only_outer_bars_get_resize_handles(app):
    from datetime import date, timedelta
    from app.services.workload import build_segments

    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(14)]
    segments, _ = build_segments([_assignment("2026-11-16", "2026-11-27", id=1)], days)   # split by weekend
    assert [(s.is_first, s.is_last) for s in segments] == [(True, False), (False, True)]


def test_board_renders_handles_and_reschedule_url(client):
    html = client.get("/team/").get_data(as_text=True)
    assert 'class="bar-handle start"' in html and 'class="bar-handle end"' in html
    assert "/reschedule" in html and "data-assignment=" in html


# ------------------------------------------------- planning tab on the project page
def test_project_planning_tab(client):
    from datetime import date, timedelta
    from app.models import Project

    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    monday = date.today() - timedelta(days=date.today().weekday())
    html = client.get(f"/projects/{project.id}?planning_start={monday.isoformat()}").get_data(as_text=True)
    board = html.split('id="tab-planning"', 1)[1]
    assert f'data-project="{project.id}"' in board                 # new planning is pre-filled
    assert "Aquafin - Waterzuivering SCADA" in board
    # Planning of the other project (Verpakkingslijn L3) is shown muted and is not draggable.
    muted = board.split("planning-chip", )[1:]
    assert any("muted" in m.split(">", 1)[0] and "data-assignment" not in m.split(">", 1)[0] for m in muted)
    # People planned on this project come first: Els (4h on this project) before Jan.
    assert board.index("Els Janssens") < board.index("Jan Peeters")
    # List with totals: Els 5 days x 4h + Lies 5 days x 8h = 60h.
    assert "Alle planning voor dit project" in board and "<th class=\"text-end\">60</th>" in board


def test_planning_popup_prefills_project(client):
    from app.models import Project
    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    html = client.get(f"/team/assignments/new?user=2&day=2026-11-16&project={project.id}",
                      headers={"X-Modal": "1"}).get_data(as_text=True)
    assert f'<option selected value="{project.id}">' in html


# ------------------------------------------------------- week / month view
def test_planning_period_month_and_weeks():
    from datetime import date
    from app.services.workload import planning_period

    month = planning_period("month", "2026-11-18", None)
    assert month.start == date(2026, 11, 1) and len(month.days) == 30
    assert month.prev_start == date(2026, 10, 1) and month.next_start == date(2026, 12, 1)
    assert month.title == "november 2026"
    december = planning_period("month", "2026-12-05", None)
    assert december.next_start == date(2027, 1, 1) and len(december.days) == 31
    weeks = planning_period("weeks", "2026-11-18", 2)
    assert weeks.start == date(2026, 11, 16) and len(weeks.days) == 14
    assert planning_period("weeks", "rubbish", 99, max_weeks=8).weeks == 8


def test_team_month_view(client):
    html = client.get("/team/?view=month&start=2026-02-10").get_data(as_text=True)
    assert "februari 2026" in html and "planning-compact" in html
    assert html.count('class="pl-day') == 28
    # Navigation keeps the month view.
    assert "view=month&amp;start=2026-01-01" in html and "view=month&amp;start=2026-03-01" in html


def test_project_tab_month_view(client):
    from app.models import Project
    project = db.session.scalar(sa.select(Project))
    html = client.get(f"/projects/{project.id}?planning_view=month&planning_start=2026-11-01").get_data(as_text=True)
    board = html.split('id="tab-planning"', 1)[1]
    assert "november 2026" in board and board.count('class="pl-day') == 30
    assert f"/projects/{project.id}?planning_view=month&amp;planning_start=2026-12-01#planning" in board


def test_day_columns_have_a_maximum_width(client):
    html = client.get("/team/?view=weeks&weeks=1").get_data(as_text=True)
    assert "minmax(2.8rem, 7rem)" in html and "1fr" not in html.split("--planning-columns", 1)[1].split(";", 1)[0]


def test_bars_carry_instant_tooltip_content(client):
    html = client.get("/team/").get_data(as_text=True)
    # HTML content is escaped inside the attribute (no broken quotes / injection).
    assert 'data-tooltip-html="' in html
    attr = html.split('data-tooltip-html="', 1)[1].split('"', 1)[0]
    assert "&lt;div class=&#34;fw-semibold&#34;&gt;" in attr and "<" not in attr


def test_kanban_cards_show_customer_and_project(client):
    html = client.get("/tasks/").get_data(as_text=True)
    assert 'kanban-project" title="' in html
    assert ">Brouwerij De Kroon - Verpakkingslijn L3</span>" in html
    assert ">Aquafin - Waterzuivering SCADA</span>" in html
