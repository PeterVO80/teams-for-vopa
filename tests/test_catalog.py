"""Supplier catalogue (Rexel DBF price list): load, filter, select, import.

The tests build their own small DBF files with the same structure as a real
Rexel price list (see :func:`write_rexel_dbf`), so no supplier data has to be
stored in the repository.
"""

import struct
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Article, CatalogItem, SupplierCatalog
from app.services import supplier_catalog as svc

# (name, type, length, decimals) - the field layout of a Rexel price list.
REXEL_FIELDS = [
    ("PROD_GROUP", "C", 25, 0), ("PG_DESC_NL", "C", 50, 0), ("PG_DESC_FR", "C", 50, 0),
    ("SUPPL_REF", "C", 35, 0), ("REXEL_REF", "C", 35, 0), ("DESC_NL", "C", 50, 0),
    ("DESC_FR", "C", 50, 0), ("STOCK_UNIT", "C", 5, 0), ("FABRICANT", "C", 25, 0),
    ("DATE", "C", 10, 0), ("NET_PRICE", "N", 10, 4), ("BRUT_PRICE", "N", 10, 4),
    ("EAN_CODE", "C", 13, 0), ("MIN_ORDQTY", "N", 10, 2), ("URL", "C", 253, 0),
    ("ERP_NUMB", "C", 15, 0),
]

ROWS = [
    {"PROD_GROUP": "216", "PG_DESC_NL": "SIE  216", "SUPPL_REF": "6ES7510-1SK03-0AB0",
     "REXEL_REF": "SIE6ES75101SK030AB0", "DESC_NL": "SIMATIC DP, CPU 1510SP F-1 PN",
     "DESC_FR": "SIMATIC DP, CPU 1510SP F-1 PN", "STOCK_UNIT": "P", "FABRICANT": "SIEMENS",
     "DATE": "2026/11/07", "NET_PRICE": 769.5752, "BRUT_PRICE": 1018.94, "EAN_CODE": "4047623412724",
     "MIN_ORDQTY": 1, "URL": "HTTPS://Netstore.rexel.be/NS/Start?itemcode=E5808005", "ERP_NUMB": "E5808005"},
    {"PROD_GROUP": "255", "PG_DESC_NL": "SIE  255", "SUPPL_REF": "6ES7131-6BF01-0BA0",
     "REXEL_REF": "SIE6ES71316BF010BA0", "DESC_NL": "SIMATIC ET 200SP, DIGITAL INPU",
     "DESC_FR": "SIMATIC ET 200SP, MODULE D'ENTRÉE", "STOCK_UNIT": "P", "FABRICANT": "SIEMENS",
     "DATE": "2026/11/07", "NET_PRICE": 42.5778, "BRUT_PRICE": 55.654, "ERP_NUMB": "E4356628"},
    {"PROD_GROUP": "CBL", "PG_DESC_NL": "CBL", "SUPPL_REF": "VTBS0,75BLRAL5003R100EUP",
     "REXEL_REF": "CBLVTBS0,75BLRAL5003R100EUP", "DESC_NL": "H05V-K ECA 0,75 MM2 450/750V",
     "STOCK_UNIT": "M", "FABRICANT": "CABLES WITH CPR", "DATE": "2026/11/07",
     "NET_PRICE": 0.3876, "BRUT_PRICE": 0.7079, "MIN_ORDQTY": 100, "ERP_NUMB": "E4265545"},
    {"PROD_GROUP": "A02", "PG_DESC_NL": "RIT  A02", "SUPPL_REF": "1360000",
     "REXEL_REF": "RIT1360000", "DESC_NL": "AX KAST 600X600X350 1D 1MPL RA",
     "STOCK_UNIT": "P", "FABRICANT": "RITTAL", "DATE": "2026/11/07",
     "NET_PRICE": 189.9311, "ERP_NUMB": "E4958397"},
]


def write_rexel_dbf(rows, fields=REXEL_FIELDS) -> bytes:
    """Write a minimal dBase III file (the format Rexel uses), encoded cp1252."""
    record_len = 1 + sum(f[2] for f in fields)
    header_len = 32 + 32 * len(fields) + 1
    out = BytesIO()
    out.write(struct.pack("<BBBBIHH20x", 0x03, 126, 11, 7, len(rows), header_len, record_len))
    for name, ftype, length, decimals in fields:
        out.write(struct.pack("<11sc4xBB14x", name.encode(), ftype.encode(), length, decimals))
    out.write(b"\r")
    for row in rows:
        out.write(b" ")  # not deleted
        for name, ftype, length, decimals in fields:
            value = row.get(name, "")
            if ftype == "N":
                text = f"{value:.{decimals}f}" if value != "" else ""
                out.write(text.rjust(length).encode("cp1252"))
            else:
                out.write(str(value).ljust(length)[:length].encode("cp1252"))
    out.write(b"\x1a")
    return out.getvalue()


def _supplier_id(name="Rexel"):
    from app.models import Company
    return Company.by_name(name).id


def upload(client, rows=ROWS, supplier="Rexel", replace=True, name="rexel.dbf"):
    data = {"supplier_id": _supplier_id(supplier), "file": (BytesIO(write_rexel_dbf(rows)), name)}
    if replace:
        data["replace"] = "y"
    return client.post("/articles/catalog/", data=data, content_type="multipart/form-data")


def _catalog():
    return db.session.scalar(sa.select(SupplierCatalog).order_by(SupplierCatalog.id.desc()))


def _item(ref):
    return db.session.scalar(sa.select(CatalogItem).filter_by(supplier_ref=ref))


# --------------------------------------------------------------- reading
def test_read_rexel_dbf(tmp_path):
    path = tmp_path / "prijslijst.dbf"
    path.write_bytes(write_rexel_dbf(ROWS))
    fmt, count, rows = svc.read_dbf(str(path))
    rows = list(rows)
    assert fmt.name == "Rexel" and count == 4
    cpu, di, cable, _ = rows
    assert cpu["manufacturer_ref"] == "6ES7510-1SK03-0AB0" and cpu["unit"] == "st"
    assert cpu["net_price"] == Decimal("769.5752") and cpu["price_date"] == date(2026, 11, 7)
    assert cpu["product_group_desc"] == "SIE 216"          # double space collapsed
    assert cpu["url"].startswith("https://Netstore")         # scheme lower-cased
    assert di["description_fr"] == "SIMATIC ET 200SP, MODULE D'ENTRÉE"[:50]  # cp1252 accent
    assert cable["unit"] == "m" and cable["min_order_qty"] == 100


def test_unknown_file_format_is_rejected(client):
    other = [("NAME", "C", 20, 0), ("PRICE", "N", 10, 2)]
    data = {"supplier_id": _supplier_id(), "file": (BytesIO(write_rexel_dbf([{"NAME": "a", "PRICE": 1}], other)), "x.dbf")}
    html = client.post("/articles/catalog/", data=data, content_type="multipart/form-data").get_data(as_text=True)
    assert "Onbekend prijslijstformaat" in html
    assert db.session.scalar(sa.select(sa.func.count(SupplierCatalog.id))) == 0


def test_not_a_dbf_file(client):
    data = {"supplier_id": _supplier_id(), "file": (BytesIO(b"this is not a dbf file at all" * 10), "x.dbf")}
    html = client.post("/articles/catalog/", data=data, content_type="multipart/form-data").get_data(as_text=True)
    assert "Geen geldig DBF-bestand" in html or "Onbekend prijslijstformaat" in html


# ----------------------------------------------------- upload and filter
def test_upload_and_filter(client):
    response = upload(client)
    catalog = _catalog()
    assert response.status_code == 302 and catalog.item_count == 4
    assert catalog.price_date == date(2026, 11, 7)

    html = client.get(f"/articles/catalog/{catalog.id}").get_data(as_text=True)
    assert "SIEMENS (2)" in html and "RITTAL (1)" in html
    assert "kies eerst fabrikant" in html                    # groups need a manufacturer

    html = client.get(f"/articles/catalog/{catalog.id}?manufacturer=SIEMENS").get_data(as_text=True)
    assert "SIE 255 (1)" in html and "AX KAST" not in html
    html = client.get(f"/articles/catalog/{catalog.id}?manufacturer=SIEMENS&group=SIE+255").get_data(as_text=True)
    assert "DIGITAL INPU" in html and "CPU 1510SP" not in html
    # Every search word must match: description + manufacturer code.
    html = client.get(f"/articles/catalog/{catalog.id}?q=et200sp+6BF01&f=1").get_data(as_text=True)
    assert "1 artikelen gevonden" in html and "DIGITAL INPU" in html


def test_search_by_ean_and_rexel_code(client):
    upload(client)
    catalog = _catalog()
    query = svc.filtered_items(catalog, search="4047623412724")
    assert [i.supplier_ref for i in db.session.scalars(query)] == ["SIE6ES75101SK030AB0"]
    query = svc.filtered_items(catalog, search="E4958397")
    assert [i.supplier_ref for i in db.session.scalars(query)] == ["RIT1360000"]


# --------------------------------------------------------------- import
def test_import_selected_items(client):
    upload(client)
    catalog = _catalog()
    ids = f"{_item('SIE6ES75101SK030AB0').id},{_item('CBLVTBS0,75BLRAL5003R100EUP').id}"
    response = client.post(f"/articles/catalog/{catalog.id}/import", data={
        "scope": "selected", "item_ids": ids, "number_source": "manufacturer_ref",
        "category": "PLC", "update_existing": "y",
    })
    assert response.status_code == 302 and "done=1" in response.headers["Location"]

    cpu = db.session.scalar(sa.select(Article).filter_by(article_number="6ES7510-1SK03-0AB0"))
    assert cpu.supplier.name == "Rexel" and cpu.supplier_ref == "SIE6ES75101SK030AB0"
    assert cpu.unit_price == Decimal("769.5752") and cpu.category == "PLC" and cpu.ean == "4047623412724"
    cable = db.session.scalar(sa.select(Article).filter_by(supplier_ref="CBLVTBS0,75BLRAL5003R100EUP"))
    assert cable.unit == "m" and cable.unit_price == Decimal("0.3876")

    # Status filter: 2 imported, 2 new.
    assert len(db.session.scalars(svc.filtered_items(catalog, status="imported")).all()) == 2
    assert len(db.session.scalars(svc.filtered_items(catalog, status="new")).all()) == 2
    html = client.get(f"/articles/catalog/{catalog.id}").get_data(as_text=True)
    assert html.count("In databank") == 2


def test_import_everything_in_filter(client):
    upload(client)
    catalog = _catalog()
    client.post(f"/articles/catalog/{catalog.id}/import", data={
        "scope": "filter", "manufacturer": "SIEMENS", "status": "all",
        "number_source": "supplier_ref", "update_existing": "y",
    })
    refs = set(db.session.scalars(sa.select(Article.supplier_ref).where(Article.supplier_ref.is_not(None))))
    assert refs == {"SIE6ES75101SK030AB0", "SIE6ES71316BF010BA0"}


def test_import_skips_article_number_already_in_use(client):
    upload(client)
    catalog = _catalog()
    db.session.add(Article(article_number="RIT1360000", description="Iets anders"))
    db.session.commit()
    result = svc.import_items(catalog, svc.filtered_items(catalog, manufacturer="RITTAL"))
    assert result.skipped == 1 and "al in gebruik" in result.errors[0]
    assert db.session.scalar(sa.select(Article).filter_by(article_number="RIT1360000")).description == "Iets anders"


def test_new_price_list_replaces_catalog_and_updates_prices(client):
    upload(client)
    first = _catalog()
    svc.import_items(first, svc.filtered_items(first, manufacturer="SIEMENS"))
    article = db.session.scalar(sa.select(Article).filter_by(supplier_ref="SIE6ES75101SK030AB0"))
    article.article_number, article.category = "PLC-001", "PLC"     # user changes these
    db.session.commit()

    new_rows = [dict(r) for r in ROWS]
    new_rows[0]["NET_PRICE"] = 799.99
    new_rows[0]["DATE"] = "2026/12/01"
    upload(client, rows=new_rows, name="rexel_december.dbf")
    second = _catalog()
    assert db.session.scalar(sa.select(sa.func.count(SupplierCatalog.id))) == 1   # old one replaced
    html = client.get(f"/articles/catalog/{second.id}").get_data(as_text=True)
    assert "Prijs gewijzigd" in html

    client.post(f"/articles/catalog/{second.id}/update-prices")
    db.session.refresh(article)
    assert article.unit_price == Decimal("799.99")
    assert article.article_number == "PLC-001" and article.category == "PLC"     # kept


def test_keep_old_catalog_when_not_replacing(client):
    upload(client)
    upload(client, replace=False)
    assert db.session.scalar(sa.select(sa.func.count(SupplierCatalog.id))) == 2


def test_delete_catalog_keeps_articles(client):
    upload(client)
    catalog = _catalog()
    svc.import_items(catalog, svc.filtered_items(catalog))
    client.post(f"/articles/catalog/{catalog.id}/delete")
    assert db.session.scalar(sa.select(sa.func.count(CatalogItem.id))) == 0
    assert db.session.scalar(sa.select(sa.func.count(Article.id)).where(Article.supplier_ref.is_not(None))) == 4


def test_imported_article_is_found_by_order_line_search(client):
    upload(client)
    catalog = _catalog()
    svc.import_items(catalog, svc.filtered_items(catalog, manufacturer="RITTAL"))
    db.session.commit()
    data = client.get("/articles/search.json?q=RIT1360000").get_json()
    assert data and data[0]["unit_price"] == pytest.approx(189.9311)


def test_price_filter(app):
    price = app.jinja_env.filters["price"]
    assert price(Decimal("385")) == "€ 385,00"
    assert price(Decimal("0.3422")) == "€ 0,3422"
    assert price(Decimal("1018.9400")) == "€ 1.018,94"


def test_catalog_pagination(client):
    """More than one page of results (100 per page) must render page links."""
    rows = [dict(ROWS[0], REXEL_REF=f"REF{i:04d}", SUPPL_REF=f"MFR{i:04d}") for i in range(130)]
    upload(client, rows=rows)
    catalog = _catalog()
    html = client.get(f"/articles/catalog/{catalog.id}?manufacturer=SIEMENS").get_data(as_text=True)
    assert f"/articles/catalog/{catalog.id}?manufacturer=SIEMENS&amp;page=2" in html
    assert client.get(f"/articles/catalog/{catalog.id}?manufacturer=SIEMENS&page=2").status_code == 200
