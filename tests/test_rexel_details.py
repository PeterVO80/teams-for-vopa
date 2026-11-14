"""Extended description and specifications from the Rexel product page.

No network traffic: fetch_page() is replaced by a fake that returns a page
with the same structure as a Rexel Netstore product page.
"""

import json
import urllib.error

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Article
from app.services import rexel_details as svc

DETAIL_PAGE = """<html><head><title>Rexel Netstore</title></head><body>
<div id="second">
  <div id="description">
    <h2 class="bold">Uitgebreide omschrijving&nbsp;:&nbsp;</h2>
    <p>AX WANDKAST, BHD: 600X600X350 MM, PLAATSTAAL, MET MONTAGEPLAAT, MET EEN DEUR</p>
  </div>
  <div id="table"><table>
    <tr><td>Merk&nbsp;:&nbsp;</td><td class="bold">RITTAL</td></tr>
    <tr><td>Serienaam&nbsp;:&nbsp;</td><td class="bold">AX</td></tr>
  </table></div>
</div>
<div id="table_product"><table class="description_product">
  <tr class="first_row "><td class="label">Breedte&nbsp;:</td><td class="bold details">600&nbsp;mm</td></tr>
  <tr><td class="label">Beschermingsgraad (IP)&nbsp;:</td><td class="bold">IP66</td></tr>
</table></div>
</body></html>"""

EMPTY_PAGE = "<html><body><div id='second'></div></body></html>"
URL = "HTTPS://Netstore.rexel.be/NS/Start?appl=netstore&itemcode={}&LANGUAGE_CODE=NL"


@pytest.fixture()
def fake_rexel(monkeypatch):
    def fetch_page(code):
        if code == "E4958397":
            return DETAIL_PAGE
        if code == "E0000403":
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        return EMPTY_PAGE
    monkeypatch.setattr(svc, "fetch_page", fetch_page)


def make_article(code="E4958397", number="RIT1360000"):
    article = Article(article_number=number, description="AX KAST 600X600X350 1D 1MPL RA", url=URL.format(code))
    db.session.add(article)
    db.session.commit()
    return article


def test_parse_detail_page():
    details = svc.parse_detail_page(DETAIL_PAGE)
    assert details.found
    assert details.description == "AX WANDKAST, BHD: 600X600X350 MM, PLAATSTAAL, MET MONTAGEPLAAT, MET EEN DEUR"
    assert details.specifications == [("Serie", "AX"), ("Breedte", "600 mm"), ("Beschermingsgraad (IP)", "IP66")]
    assert not svc.parse_detail_page(EMPTY_PAGE).found


def test_item_code_from_link():
    assert svc.item_code(URL.format("E5808005")) == "E5808005"
    assert svc.item_code("https://www.example.com/?itemcode=E1") is None
    assert svc.item_code(None) is None


def test_fetch_and_apply(app, fake_rexel):
    article = make_article()
    svc.apply(article, svc.fetch(article))
    assert article.description.startswith("AX WANDKAST, BHD: 600X600X350 MM")
    assert article.details_status == "done"
    assert json.loads(article.specifications)[1] == ["Breedte", "600 mm"]
    assert article.specification_list[0] == ("Serie", "AX")


def test_not_found_and_error_keep_description(app, fake_rexel):
    missing, blocked = make_article("E0000000", "A-1"), make_article("E0000403", "A-2")
    for article in (missing, blocked):
        svc.apply(article, svc.fetch(article))
        assert article.description == "AX KAST 600X600X350 1D 1MPL RA"
    assert missing.details_status == "not_found" and blocked.details_status == "error"


def test_import_queues_and_batches_fetch_details(client, fake_rexel):
    from tests.test_catalog import ROWS, _catalog, upload
    from app.services import supplier_catalog as catalog_svc

    rows = [dict(ROWS[3], URL=URL.format("E4958397"))]           # the Rittal cabinet
    upload(client, rows=rows)
    catalog = _catalog()
    item_id = db.session.scalars(catalog_svc.filtered_items(catalog)).first().id
    client.post(f"/articles/catalog/{catalog.id}/import", data={
        "scope": "selected", "item_ids": str(item_id), "number_source": "supplier_ref",
        "update_existing": "y", "fetch_details": "y",
    })
    article = db.session.scalar(sa.select(Article).filter_by(supplier_ref="RIT1360000"))
    assert article.details_status == "pending"
    data = client.post("/articles/catalog/details/run", json={}).get_json()
    assert data["remaining"] == 0 and data["processed"][0]["status"] == "done"
    db.session.refresh(article)
    extended = article.description
    assert extended.startswith("AX WANDKAST")

    # A new price list (update prices) must not put the short description back.
    client.post(f"/articles/catalog/{catalog.id}/update-prices")
    db.session.refresh(article)
    assert article.description == extended


def test_import_without_option_does_not_queue(client, fake_rexel):
    from tests.test_catalog import ROWS, _catalog, upload
    from app.services import supplier_catalog as catalog_svc

    upload(client, rows=[dict(ROWS[3], URL=URL.format("E4958397"))])
    catalog = _catalog()
    item_id = db.session.scalars(catalog_svc.filtered_items(catalog)).first().id
    client.post(f"/articles/catalog/{catalog.id}/import", data={
        "scope": "selected", "item_ids": str(item_id), "number_source": "supplier_ref", "update_existing": "y"})
    assert db.session.scalar(sa.select(Article).filter_by(supplier_ref="RIT1360000")).details_status is None


def test_queue_all_and_single_fetch(client, fake_rexel):
    from tests.test_catalog import ROWS, _catalog, upload
    from app.models import Company

    upload(client, rows=[dict(ROWS[3], URL=URL.format("E4958397"))])
    catalog = _catalog()
    article = make_article()
    article.supplier = Company.by_name("Rexel")
    db.session.commit()
    html = client.get(f"/articles/catalog/{catalog.id}").get_data(as_text=True)
    assert "hebben nog de korte omschrijving" in html
    client.post(f"/articles/catalog/{catalog.id}/details/queue")
    db.session.refresh(article)
    assert article.details_status == "pending"

    client.post(f"/articles/{article.id}/details")
    db.session.refresh(article)
    assert article.details_status == "done"
    html = client.get(f"/articles/{article.id}/edit").get_data(as_text=True)
    assert "Kenmerken (Rexel)" in html and "IP66" in html
