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
    assert 'value="65.00"' in html and "Uurtarief" in html
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
    client.post(f"/quotes/{quote.id}/status", data={"status": "draft"})          # sent quotes can't be deleted
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


def test_quote_pdf_has_no_internal_prices(client):
    import io

    from pypdf import PdfReader

    AppSetting.put("company.vat", "BE0999.888.777")
    db.session.commit()
    quote = new_quote(client, notes="Intern: korting max 5%")
    section = add(client, quote, "section", description="Schakelkast", note="Volgens schema")
    add(client, quote, "material", section=section, description="Draadgoot", quantity="4", unit="st",
        cost_price="12,5", markup="30", unit_price="", note="Inclusief deksels")
    add(client, quote, "labour", section=section, description="Bekabeling", quantity="8", unit="u", unit_price="65")
    response = client.get(f"/quotes/{quote.id}/pdf")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert f"Offerte {quote.number} Aquafin.pdf" in response.headers["Content-Disposition"]
    content = "".join(page.extract_text() for page in PdfReader(io.BytesIO(response.data)).pages)
    for expected in ("Offerte", quote.number, "Retrofit pompstation", "Vervangen van de PLC", "Schakelkast",
                     "Volgens schema", "Draadgoot", "Inclusief deksels", "Bekabeling",
                     "€ 65,00", "€ 585,00", "€ 707,85", "Voor akkoord", "BE0999.888.777"):
        assert expected in content, expected
    for internal in ("12,5", "Inkoop", "30%", "korting", "Marge"):
        assert internal not in content, internal


def test_sent_or_accepted_quote_is_locked_except_status(client):
    quote = new_quote(client)
    line = add(client, quote, "labour", description="Uren", quantity="2", unit_price="60")
    client.post(f"/quotes/{quote.id}/status", data={"status": "sent"})
    db.session.refresh(quote)
    assert quote.locked

    html = client.get(f"/quotes/{quote.id}").get_data(as_text=True)
    assert "kan niet meer gewijzigd worden" in html
    assert f"/quotes/{quote.id}/edit" not in html and f"/quotes/lines/{line.id}/edit" not in html
    assert f"/quotes/{quote.id}/lines/new" not in html and f"/quotes/{quote.id}/delete" not in html

    # The server refuses every change ...
    assert client.post(f"/quotes/{quote.id}/edit", data={"title": "Gewijzigd", "customer_id": 0, "project_id": 0,
                                                         "status": "draft", "vat_rate": "21"}).status_code == 302
    assert client.post(f"/quotes/{quote.id}/lines/new?kind=labour", headers=MODAL,
                       data={"description": "x", "quantity": "1", "unit_price": "1"}).status_code == 409
    assert client.post(f"/quotes/lines/{line.id}/edit", headers=MODAL,
                       data={"description": "y", "quantity": "9", "unit_price": "1"}).status_code == 409
    assert client.post(f"/quotes/lines/{line.id}/delete", headers=MODAL).status_code == 409
    client.post(f"/quotes/lines/{line.id}/move", data={"direction": "up"})
    client.post(f"/quotes/{quote.id}/delete")
    db.session.refresh(quote)
    db.session.refresh(line)
    assert quote.title == "Retrofit pompstation" and line.description == "Uren" and len(quote.lines) == 1
    assert "kan niet meer gewijzigd worden" in client.get(f"/quotes/lines/{line.id}/edit", headers=MODAL).get_data(as_text=True)

    # ... except the status (and copying, and making a project).
    client.post(f"/quotes/{quote.id}/status", data={"status": "accepted"})
    db.session.refresh(quote)
    assert quote.status == QuoteStatus.ACCEPTED and quote.locked
    client.post(f"/quotes/{quote.id}/copy")
    copy = db.session.scalars(sa.select(Quote).order_by(Quote.id.desc())).first()
    assert copy.id != quote.id and not copy.locked
    client.post(f"/quotes/{quote.id}/status", data={"status": "draft"})
    db.session.refresh(quote)
    assert not quote.locked
    add(client, quote, "labour", description="Extra", quantity="1", unit_price="60")


def test_labour_with_kind_of_service(client):
    from app.models import ServiceType

    engineer = ServiceType(name="PLC & HMI engineer", hourly_rate=Decimal("75"), position=20)
    bordenbouw = ServiceType(name="Technieker bordenbouw", hourly_rate=None, position=30)
    db.session.add_all([engineer, bordenbouw])
    db.session.commit()
    quote = new_quote(client)
    html = client.get(f"/quotes/{quote.id}/lines/new?kind=labour", headers=MODAL).get_data(as_text=True)
    assert "Soort dienst" in html and 'data-rate="75.00"' in html and "PLC &amp; HMI engineer" in html
    assert '<label class="form-label" for="quantity">Aantal uren</label>' in html
    assert '<label class="form-label" for="unit_price">Uurtarief (€, excl. btw)</label>' in html

    # Description and rate empty: taken from the service.
    line = add(client, quote, "labour", service_type_id=str(engineer.id), description="", quantity="10", unit_price="")
    assert line.service_type == engineer and line.description == "PLC & HMI engineer" and line.unit_price == Decimal("75")
    # Own description and rate are kept.
    line = add(client, quote, "labour", service_type_id=str(engineer.id), description="Programmatie lijn 3",
               quantity="5", unit_price="80")
    assert line.description == "Programmatie lijn 3" and line.unit_price == Decimal("80")
    # A service without a rate needs a rate on the line.
    response = client.post(f"/quotes/{quote.id}/lines/new?kind=labour", headers=MODAL,
                           data={"service_type_id": str(bordenbouw.id), "quantity": "8"})
    assert response.status_code == 422 and "Vul de uurprijs in" in response.get_data(as_text=True)


def test_settings_manage_service_types(client):
    from app.models import ServiceType

    used = ServiceType(name="E&I engineer", hourly_rate=Decimal("70"), position=10)
    unused = ServiceType(name="Oud", position=20)
    db.session.add_all([used, unused])
    db.session.commit()
    quote = new_quote(client)
    add(client, quote, "labour", service_type_id=str(used.id), quantity="1", unit_price="")
    html = client.get("/users/settings").get_data(as_text=True)
    assert "Diensten en uurtarieven" in html and 'value="70,00"' in html
    base = {"company.name": "VOPA"}
    client.post("/users/settings", data={**base,
        f"service-{used.id}-name": "E&I engineer", f"service-{used.id}-rate": "72,50", f"service-{used.id}-active": "y",
        f"service-{unused.id}-name": "Oud", f"service-{unused.id}-rate": "", f"service-{unused.id}-delete": "y",
        "service-new-name": "On site inbedrijfname", "service-new-rate": "85"})
    db.session.expire_all()
    assert db.session.get(ServiceType, used.id).hourly_rate == Decimal("72.50")
    assert db.session.get(ServiceType, unused.id) is None
    assert db.session.scalar(sa.select(ServiceType).filter_by(name="On site inbedrijfname")).hourly_rate == Decimal("85")
    # A service that is used on quotes is hidden instead of deleted.
    client.post("/users/settings", data={**base, f"service-{used.id}-name": "E&I engineer", f"service-{used.id}-delete": "y"})
    db.session.expire_all()
    assert db.session.get(ServiceType, used.id).active is False
    # Bad rate: nothing saved.
    response = client.post("/users/settings", follow_redirects=True,
                           data={**base, "service-new-name": "Test", "service-new-rate": "veel"})
    assert "geen geldig bedrag" in response.get_data(as_text=True)
    assert db.session.scalar(sa.select(ServiceType).filter_by(name="Test")) is None
