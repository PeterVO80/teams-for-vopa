"""Quotes: material and labour lines in sections, notes, project link (blueprints/quotes.py)."""

from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db
from app.models import AppSetting, Article, Company, Project, Quote, QuoteLine, QuoteLineKind, QuoteStatus, User
from app.modules import dump_access

MODAL = {"X-Modal": "1"}


def new_quote(client, **extra) -> Quote:
    aquafin = Company.by_name("Aquafin")
    data = {"title": "Retrofit pompstation", "customer_id": aquafin.id, "project_id": 0, "status": "draft",
            "quote_date": "2026-11-25", "valid_until": "2026-12-25", "vat_rate": "21",
            "introduction": "Vervangen van de PLC.", **extra}
    response = client.post("/quotes/new", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    return db.session.scalars(sa.select(Quote).order_by(Quote.id.desc())).first()


def add(client, quote, kind, section=None, **data):
    url = f"/quotes/{quote.id}/lines/new?kind={kind}" + (f"&section={section.id}" if section else "")
    response = client.post(url, headers=MODAL, data=data)
    assert response.get_json() == {"ok": True}, response.get_data(as_text=True)[:800]
    return db.session.scalars(sa.select(QuoteLine).order_by(QuoteLine.id.desc())).first()


def test_quote_with_sections_material_labour_and_notes(client):
    AppSetting.put("quote.hourly_rate", "65")
    AppSetting.put("quote.markup", "25")
    db.session.commit()
    article = Article(article_number="PLC-1", description="PLC CPU 1511", unit="st", unit_price=Decimal("800"))
    db.session.add(article)
    db.session.commit()

    quote = new_quote(client)
    assert quote.number.startswith("OFF-") and quote.customer.name == "Aquafin"

    cabinet = add(client, quote, "section", description="Schakelkast", note="Volgens schema rev. B")
    programming = add(client, quote, "section", description="Programmatie")
    # The forms suggest the hourly rate and the markup from the settings.
    html = client.get(f"/quotes/{quote.id}/lines/new?kind=labour", headers=MODAL).get_data(as_text=True)
    assert 'value="65.00"' in html and "Uurprijs" in html
    html = client.get(f"/quotes/{quote.id}/lines/new?kind=material", headers=MODAL).get_data(as_text=True)
    assert 'name="markup"' in html and 'value="25"' in html and "data-article-picker" in html

    add(client, quote, "labour", section=programming, description="PLC-programmatie", quantity="16", unit="u",
        unit_price="65")
    # Material at the end of the first section (before "Programmatie"), price from purchase price + markup.
    plc = add(client, quote, "material", section=cabinet, article_id=str(article.id), description="PLC CPU 1511",
              quantity="1", unit="st", cost_price="800", markup="25", unit_price="", note="Incl. geheugenkaart")
    add(client, quote, "labour", section=cabinet, description="Bekabeling", quantity="7,5", unit="u", unit_price="65")

    db.session.refresh(quote)
    assert plc.unit_price == Decimal("1000") and plc.article == article and plc.note == "Incl. geheugenkaart"
    assert [line.description for line in quote.lines] == [
        "Schakelkast", "PLC CPU 1511", "Bekabeling", "Programmatie", "PLC-programmatie"]
    sections = quote.sections()
    assert [s.title.description for s in sections] == ["Schakelkast", "Programmatie"]
    assert sections[0].total == Decimal("1487.50") and sections[1].total == Decimal("1040.00")
    assert quote.material_total == Decimal("1000.00") and quote.labour_total == Decimal("1527.50")
    assert quote.labour_hours == Decimal("23.5")
    assert quote.total == Decimal("2527.50") and quote.vat_amount == Decimal("530.78")
    assert quote.material_cost == Decimal("800.00") and quote.material_margin == Decimal("200.00")

    html = client.get(f"/quotes/{quote.id}").get_data(as_text=True)
    assert "Subtotaal Schakelkast" in html and "Volgens schema rev. B" in html and "Incl. geheugenkaart" in html
    assert "€ 2.527,50" in html and "€ 3.058,28" in html
    assert "/quotes/" in client.get("/quotes/").get_data(as_text=True)


def test_line_validation_move_and_delete(client):
    quote = new_quote(client)
    response = client.post(f"/quotes/{quote.id}/lines/new?kind=material", headers=MODAL,
                           data={"description": "Kabel", "quantity": "", "unit_price": ""})
    assert response.status_code == 422 and "Vul het aantal in" in response.get_data(as_text=True)
    first = add(client, quote, "material", description="A", quantity="1", unit_price="10")
    second = add(client, quote, "material", description="B", quantity="2", unit_price="5")
    client.post(f"/quotes/lines/{second.id}/move", data={"direction": "up"})
    db.session.refresh(quote)
    assert [line.description for line in quote.lines] == ["B", "A"]
    client.post(f"/quotes/lines/{first.id}/edit", headers=MODAL,
                data={"description": "A2", "quantity": "3", "unit_price": "10", "note": "  "})
    db.session.refresh(first)
    assert first.description == "A2" and first.total == Decimal("30.00") and first.note is None
    client.post(f"/quotes/lines/{second.id}/delete", headers=MODAL)
    db.session.refresh(quote)
    assert [line.description for line in quote.lines] == ["A2"]


def test_link_to_project_and_create_project_from_quote(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    html = client.get(f"/quotes/new?project={project.id}").get_data(as_text=True)
    assert f'<option selected value="{project.id}">' in html or f'value="{project.id}" selected' in html
    linked = new_quote(client, project_id=project.id, title="Uitbreiding L3")
    assert linked.project == project
    html = client.get(f"/projects/{project.id}").get_data(as_text=True)
    assert 'data-bs-target="#tab-quotes"' in html and linked.number in html

    quote = new_quote(client, title="Nieuwe lijn L4")
    response = client.post(f"/quotes/{quote.id}/create-project")
    db.session.refresh(quote)
    created = quote.project
    assert created is not None and response.headers["Location"].endswith(f"/projects/{created.id}")
    assert created.name == "Nieuwe lijn L4" and created.customer == quote.customer
    assert quote.number in created.description and quote.status == QuoteStatus.ACCEPTED
    client.post(f"/quotes/{quote.id}/create-project")                       # not twice
    assert db.session.scalar(sa.select(sa.func.count(Project.id)).filter_by(name="Nieuwe lijn L4")) == 1


def test_copy_status_and_delete(client):
    quote = new_quote(client)
    add(client, quote, "section", description="Titel")
    add(client, quote, "labour", description="Uren", quantity="2", unit_price="60")
    client.post(f"/quotes/{quote.id}/status", data={"status": "sent"})
    db.session.refresh(quote)
    assert quote.status == QuoteStatus.SENT
    client.post(f"/quotes/{quote.id}/copy")
    copy = db.session.scalars(sa.select(Quote).order_by(Quote.id.desc())).first()
    assert copy.id != quote.id and copy.status == QuoteStatus.DRAFT and copy.number != quote.number
    assert [(l.kind, l.description) for l in copy.lines] == [(QuoteLineKind.SECTION, "Titel"), (QuoteLineKind.LABOUR, "Uren")]
    client.post(f"/quotes/{quote.id}/delete")
    assert db.session.get(Quote, quote.id) is None
    assert db.session.scalar(sa.select(sa.func.count(QuoteLine.id)).where(QuoteLine.quote_id == quote.id)) == 0


def test_access_to_quotes(app, client):
    # (Created first: the test clients share one app context, see conftest.py.)
    quote = new_quote(client)
    lies = db.session.scalar(sa.select(User).filter_by(username="lies"))
    lies.module_access = dump_access({"projects": "w"})
    db.session.commit()
    member = app.test_client()
    member.post("/auth/login", data={"username": "lies", "password": "demo1234"})
    assert member.get("/quotes/").status_code == 403
    assert 'href="/quotes/"' not in member.get("/").get_data(as_text=True)
    lies.module_access = dump_access({"projects": "w", "quotes": "r"})
    db.session.commit()
    assert member.get(f"/quotes/{quote.id}").status_code == 200
    assert member.post(f"/quotes/{quote.id}/lines/new?kind=labour", headers=MODAL,
                       data={"description": "x", "quantity": "1", "unit_price": "1"}).status_code == 403
    assert member.get(f"/quotes/{quote.id}/lines/new?kind=labour").status_code == 403
