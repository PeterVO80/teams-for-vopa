"""Relations (customers & suppliers) and their use in other modules."""

import re
from io import BytesIO

import sqlalchemy as sa
from openpyxl import Workbook

from app.extensions import db
from app.models import Article, Company, Project, PurchaseOrder

MODAL = {"X-Modal": "1"}


def test_relations_pages(client):
    html = client.get("/relations/").get_data(as_text=True)
    assert "Brouwerij De Kroon" in html and "Rexel" in html
    assert "Rexel" not in client.get("/relations/?kind=customer").get_data(as_text=True).split("<tbody>")[1]
    rexel = Company.by_name("rexel")                          # case-insensitive lookup
    html = client.get(f"/relations/{rexel.id}").get_data(as_text=True)
    assert "PO-" in html and "artikelen met deze leverancier" in html
    kroon = Company.by_name("Brouwerij De Kroon")
    assert "Verpakkingslijn L3" in client.get(f"/relations/{kroon.id}").get_data(as_text=True)


def test_create_relation_and_prevent_duplicates(client):
    response = client.post("/relations/new", data={"name": "  Siemens   NV ", "is_supplier": "y", "active": "y"})
    assert response.status_code == 302
    company = Company.by_name("Siemens NV")
    assert company.name == "Siemens NV" and company.is_supplier and not company.is_customer
    html = client.post("/relations/new", data={"name": "siemens nv", "is_customer": "y"}).get_data(as_text=True)
    assert "bestaat al" in html
    html = client.post("/relations/new", data={"name": "Niets"}).get_data(as_text=True)
    assert "klant, een leverancier of beide" in html


def test_create_relation_from_another_form_returns_option(client):
    html = client.get("/relations/new?kind=customer&select=customer_id", headers=MODAL).get_data(as_text=True)
    assert "modal-header" in html
    assert re.search(r'<input checked[^>]*id="is_customer"', html)                 # role pre-ticked
    assert not re.search(r'<input checked[^>]*id="is_supplier"', html)
    response = client.post("/relations/new?kind=customer&select=customer_id", headers=MODAL,
                           data={"name": "Duvel Moortgat", "is_customer": "y", "active": "y"})
    company = Company.by_name("Duvel Moortgat")
    assert response.get_json() == {"ok": True, "option": {"select": "customer_id", "value": company.id, "label": "Duvel Moortgat"}}


def test_project_form_uses_customer_list(client):
    aquafin = Company.by_name("Aquafin")
    html = client.get("/projects/new").get_data(as_text=True)
    assert f'<option value="{aquafin.id}">Aquafin</option>' in html
    assert "/relations/new?kind=customer&amp;select=customer_id" in html        # '+' button
    assert ">Rexel</option>" not in html                                          # suppliers not offered
    client.post("/projects/new", data={"name": "Nieuwe lijn", "status": "active", "manager_id": 0, "customer_id": aquafin.id})
    project = db.session.scalar(sa.select(Project).filter_by(name="Nieuwe lijn"))
    assert project.customer == aquafin


def test_order_and_article_use_supplier_list(client):
    cebeo = Company.by_name("Cebeo")
    client.post("/purchasing/new", data={"number": "PO-REL-1", "project_id": 0, "supplier_id": cebeo.id,
                                         "status": "draft", "stock_location_id": 0})
    order = db.session.scalar(sa.select(PurchaseOrder).filter_by(number="PO-REL-1"))
    assert order.supplier == cebeo
    html = client.post("/purchasing/new", data={"number": "PO-REL-2", "project_id": 0, "supplier_id": 0,
                                                "status": "draft", "stock_location_id": 0}).get_data(as_text=True)
    assert "Kies een leverancier" in html
    article = db.session.scalar(sa.select(Article).filter_by(article_number="A-1001"))
    assert "Rexel" in client.get("/articles/?q=rexel").get_data(as_text=True)   # search by supplier name
    assert article.supplier.name == "Rexel"


def test_excel_import_matches_supplier_names(client):
    wb = Workbook()
    wb.active.append(["Artikelnummer", "Omschrijving", "Leverancier"])
    wb.active.append(["X-1", "Met bestaande leverancier", "cebeo"])       # other capitals: fine
    wb.active.append(["X-2", "Met tikfout", "Ceebeo"])                     # unknown: skipped
    upload = BytesIO()
    wb.save(upload)
    upload.seek(0)
    html = client.post("/articles/import", data={"file": (upload, "a.xlsx")},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert db.session.scalar(sa.select(Article).filter_by(article_number="X-1")).supplier.name == "Cebeo"
    assert db.session.scalar(sa.select(Article).filter_by(article_number="X-2")) is None
    assert "leverancier &#39;Ceebeo&#39; bestaat niet" in html


def test_delete_relation_in_use_deactivates(client):
    rexel = Company.by_name("Rexel")
    client.post(f"/relations/{rexel.id}/delete")
    db.session.refresh(rexel)
    assert rexel.active is False
    assert '>Rexel</option>' not in client.get("/purchasing/new").get_data(as_text=True)
    unused = Company(name="Weg ermee", is_customer=True)
    db.session.add(unused)
    db.session.commit()
    client.post(f"/relations/{unused.id}/delete")
    assert Company.by_name("Weg ermee") is None


def test_relation_address_with_box(client):
    response = client.post("/relations/new", data={"name": "Klant Met Bus", "is_customer": "y", "active": "y",
                                                   "street": "Kerkstraat 1", "box": "3", "postal_code": "9000",
                                                   "city": "Gent", "country": "België"})
    assert response.status_code == 302
    company = Company.by_name("Klant Met Bus")
    assert company.box == "3"
    assert company.address_lines == ["Kerkstraat 1 bus 3", "9000 Gent", "België"]
    assert "Kerkstraat 1 bus 3" in client.get(f"/relations/{company.id}").get_data(as_text=True)
    assert 'name="box"' in client.get(f"/relations/{company.id}/edit").get_data(as_text=True)
