"""Import of Rexel order files as purchase orders (services/order_import.py)."""

import io
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from openpyxl import Workbook

from app.extensions import db
from app.models import Article, Company, OrderStatus, PurchaseOrder
from app.services import order_import

HEADER = ["Rexel productreferentie", "Omschrijving", "Mijn referentie", "ERP code", "Eenheid", "Aantal",
          "Netto eenheidsprijs (excl. Ecotax)", "Netto totaalprijs (excl. Ecotax)", "Ecotax eenheidsprijs",
          "Bruto eenheidsprijs (excl. Ecotax)", "Rexel ordernummer", "Mijn orderreferentie",
          "Mijn extra orderreferentie", "Besteldatum", "Leveringsdatum"]


def _file(rows):
    """An order file like Rexel's (all values as text, decimal comma), as .xlsx."""
    wb = Workbook()
    sheet = wb.active
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


ROWS = [
    ["TESTKABEL001", "KABEL XVB 3G2,5", "", "E1000001", "M", "100", "1,2345", "123,45", "", "2,50",
     "9000001", "KEM-BVA-2", "Van Overmeire", "23/11/26", "24/11/26"],
    ["TESTBAND002", "SPANBANDEN 200MM ZW", "", "E1000002", "P", "500", "0,05", "25,00", "", "0,10",
     "9000001", "KEM-BVA-2", "Van Overmeire", "23/11/26", "24/11/26"],
]


@pytest.fixture()
def rexel(app):
    company = db.session.scalar(sa.select(Company).where(Company.name.ilike("%rexel%")))
    if company is None:
        company = Company(name="Rexel Belgium", is_supplier=True)
        db.session.add(company)
    company.is_supplier = True
    db.session.add(Article(article_number="KAB-3G25", description="Kabel XVB 3G2,5 (eigen omschrijving)",
                           supplier_ref="TESTKABEL001", supplier_id=None))
    db.session.commit()
    return company


def test_parse_rexel_file():
    orders = order_import.parse_rexel_order_file(_file(ROWS))
    assert len(orders) == 1
    order = orders[0]
    assert (order.supplier_order_number, order.my_reference, order.my_extra_reference) == (
        "9000001", "KEM-BVA-2", "Van Overmeire")
    assert (order.order_date, order.delivery_date) == (date(2026, 11, 23), date(2026, 11, 24))
    first, second = order.lines
    assert (first.ref, first.quantity, first.unit, first.unit_price) == ("TESTKABEL001", Decimal("100"), "m", Decimal("1.2345"))
    assert (second.unit, second.unit_price) == ("st", Decimal("0.05"))


def test_import_links_known_articles_and_keeps_the_rest_as_text(client, rexel):
    response = client.post("/purchasing/import", data={
        "file": (io.BytesIO(_file(ROWS)), "Rexel_Order_Info.xlsx"),
        "supplier_id": rexel.id, "project_id": 0, "stock_location_id": 0}, content_type="multipart/form-data")
    assert response.status_code == 302
    order = db.session.scalar(sa.select(PurchaseOrder).filter_by(supplier_order_number="9000001"))
    assert response.location.endswith(f"/purchasing/{order.id}")
    assert order.status == OrderStatus.ORDERED and order.supplier_id == rexel.id
    assert (order.order_date, order.expected_date) == (date(2026, 11, 23), date(2026, 11, 24))
    assert "Mijn orderreferentie: KEM-BVA-2" in order.notes
    linked, free = order.lines
    assert linked.article.article_number == "KAB-3G25" and linked.quantity == 100 and linked.unit == "m"
    assert linked.unit_price == Decimal("1.2345")
    assert free.article_id is None and free.description == "TESTBAND002 - SPANBANDEN 200MM ZW"
    html = client.get(f"/purchasing/{order.id}").get_data(as_text=True)
    assert "Leverancier ordernummer" in html and "9000001" in html
    assert "1 gekoppeld aan de artikeldatabank, 1 als vrije tekst" in html


def test_same_order_is_not_imported_twice(client, rexel):
    data = _file(ROWS)
    for _ in range(2):
        client.post("/purchasing/import", data={
            "file": (io.BytesIO(data), "order.xlsx"), "supplier_id": rexel.id, "project_id": 0,
            "stock_location_id": 0}, content_type="multipart/form-data")
    assert db.session.scalar(sa.select(sa.func.count(PurchaseOrder.id)).where(
        PurchaseOrder.supplier_order_number == "9000001")) == 1
    html = client.get("/purchasing/").get_data(as_text=True)
    assert "werd al eerder geïmporteerd" in html


def test_file_with_several_orders_and_one_already_known(client, rexel):
    rows = ROWS + [["TESTBAND002", "SPANBANDEN", "", "", "P", "10", "0,05", "0,50", "", "", "9000002",
                    "", "", "25/11/2026", ""]]
    client.post("/purchasing/import", data={"file": (io.BytesIO(_file(ROWS)), "a.xlsx"), "supplier_id": rexel.id,
                                            "project_id": 0, "stock_location_id": 0}, content_type="multipart/form-data")
    client.post("/purchasing/import", data={"file": (io.BytesIO(_file(rows)), "b.xlsx"), "supplier_id": rexel.id,
                                            "project_id": 0, "stock_location_id": 0}, content_type="multipart/form-data")
    numbers = db.session.scalars(sa.select(PurchaseOrder.supplier_order_number).where(
        PurchaseOrder.supplier_order_number.like("900000%"))).all()
    assert sorted(numbers) == ["9000001", "9000002"]
    second = db.session.scalar(sa.select(PurchaseOrder).filter_by(supplier_order_number="9000002"))
    assert second.order_date == date(2026, 11, 25) and len(second.lines) == 1


def test_wrong_file_gives_a_clear_message(client, rexel):
    response = client.post("/purchasing/import", data={
        "file": (io.BytesIO(b"geen excel"), "order.xls"), "supplier_id": rexel.id, "project_id": 0,
        "stock_location_id": 0}, content_type="multipart/form-data")
    assert response.status_code == 200 and "kan niet gelezen worden" in response.get_data(as_text=True)
    wb = Workbook()
    wb.active.append(["Artikel", "Aantal"])
    buffer = io.BytesIO()
    wb.save(buffer)
    response = client.post("/purchasing/import", data={
        "file": (io.BytesIO(buffer.getvalue()), "x.xlsx"), "supplier_id": rexel.id, "project_id": 0,
        "stock_location_id": 0}, content_type="multipart/form-data")
    assert "Dit lijkt geen Rexel-orderbestand" in response.get_data(as_text=True)


def test_supplier_order_number_unique_in_the_order_form(client, rexel):
    def save(number, supplier_ref):
        return client.post("/purchasing/new", data={
            "number": number, "project_id": 0, "supplier_id": rexel.id, "supplier_order_number": supplier_ref,
            "status": "draft", "stock_location_id": 0})
    assert save("PO-T-1", "7777").status_code == 302
    response = save("PO-T-2", "7777")
    assert response.status_code == 200 and "staat al op bestelling PO-T-1" in response.get_data(as_text=True)
    assert save("PO-T-3", "").status_code == 302 and save("PO-T-4", "").status_code == 302   # empty: no limit


def test_import_page_preselects_rexel(client, rexel):
    html = client.get("/purchasing/import").get_data(as_text=True)
    assert f'<option selected value="{rexel.id}">' in html
