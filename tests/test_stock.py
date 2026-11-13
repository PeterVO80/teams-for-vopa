"""Stock management: levels, movements, purchasing and project integration."""

from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Article, Company, MovementKind, OrderStatus, Project, PurchaseOrder, PurchaseOrderLine, StockLocation, StockMovement
from app.services import stock as svc

MODAL = {"X-Modal": "1"}


def _article(number):
    return db.session.scalar(sa.select(Article).filter_by(article_number=number))


def _location(name):
    return db.session.scalar(sa.select(StockLocation).filter_by(name=name))


# Demo data: A-2001 (terminals) 150 in Magazijn + 40 in the van, minimum 200
#            A-3001 (sensors) 8 in Magazijn, minimum 5; A-4001 (cable) 500 m.

def test_levels_and_below_minimum(app):
    terminals = _article("A-2001")
    warehouse, van = _location("Magazijn"), _location("Bestelwagen Jan")
    levels = svc.levels([terminals.id])[terminals.id]
    assert levels == {warehouse.id: 150, van.id: 40}
    row = svc.rows_for([terminals])[0]
    assert row.total == 190 and row.below_minimum and row.shortage == 10
    assert svc.below_minimum_count() == 1


def test_overview_page_and_filters(client):
    html = client.get("/stock/").get_data(as_text=True)
    assert "A-2001" in html and "A-3001" in html and "1 artikel(en) onder de minimumstock" in html
    html = client.get("/stock/?below=1").get_data(as_text=True)
    assert "A-2001" in html and "A-3001" not in html
    van = _location("Bestelwagen Jan")
    html = client.get(f"/stock/?location={van.id}").get_data(as_text=True)
    assert "A-2001" in html and "A-4001" not in html
    assert client.get("/stock/export").status_code == 200
    assert client.get("/stock/movements").status_code == 200
    assert client.get("/stock/locations").status_code == 200


def test_issue_to_project_and_project_usage(client):
    sensors, warehouse = _article("A-3001"), _location("Magazijn")
    project = db.session.scalar(sa.select(Project))
    response = client.post(f"/stock/move/{sensors.id}", headers=MODAL, data={
        "article_id": sensors.id, "kind": "issue", "location_id": warehouse.id, "to_location_id": 0,
        "quantity": "3", "project_id": project.id, "moved_on": "2026-11-13",
    })
    assert response.get_json() == {"ok": True}
    assert svc.stock_on(sensors.id, warehouse.id) == 5
    usage = svc.project_usage(project.id)
    assert [(u.article.article_number, u.quantity) for u in usage] == [("A-3001", 3)]
    assert "Uit stock gebruikt" in client.get(f"/projects/{project.id}").get_data(as_text=True)

    # Return one to stock -> net usage 2.
    client.post(f"/stock/move/{sensors.id}", headers=MODAL, data={
        "article_id": sensors.id, "kind": "receipt", "location_id": warehouse.id, "to_location_id": 0,
        "quantity": "1", "project_id": project.id, "moved_on": "2026-11-14", "note": "Retour",
    })
    assert svc.project_usage(project.id)[0].quantity == 2


def test_cannot_issue_more_than_available(client):
    sensors, warehouse = _article("A-3001"), _location("Magazijn")
    response = client.post(f"/stock/move/{sensors.id}", headers=MODAL, data={
        "article_id": sensors.id, "kind": "issue", "location_id": warehouse.id, "to_location_id": 0,
        "quantity": "9", "project_id": 0, "moved_on": "2026-11-13",
    })
    assert response.status_code == 422 and "Niet genoeg stock" in response.get_data(as_text=True)
    assert svc.stock_on(sensors.id, warehouse.id) == 8


def test_transfer_between_locations(app):
    terminals, warehouse, van = _article("A-2001"), _location("Magazijn"), _location("Bestelwagen Jan")
    svc.register_transfer(terminals, warehouse.id, van.id, Decimal(50), user_id=None)
    db.session.commit()
    assert svc.stock_on(terminals.id, warehouse.id) == 100 and svc.stock_on(terminals.id, van.id) == 90
    with pytest.raises(svc.StockError):
        svc.register_transfer(terminals, van.id, van.id, Decimal(1), user_id=None)


def test_inventory_count_books_difference(client):
    cable, warehouse = _article("A-4001"), _location("Magazijn")
    client.post(f"/stock/move/{cable.id}", headers=MODAL, data={
        "article_id": cable.id, "kind": "count", "location_id": warehouse.id, "to_location_id": 0,
        "quantity": "480", "project_id": 0, "moved_on": "2026-11-13",
    })
    movement = db.session.scalar(sa.select(StockMovement).filter_by(kind=MovementKind.COUNT))
    assert movement.quantity == -20 and svc.stock_on(cable.id, warehouse.id) == 480
    # Counting the same again records nothing.
    assert svc.register_count(cable, warehouse.id, Decimal(480), user_id=None) is None


def test_generic_popup_lists_stocked_articles(client):
    html = client.get("/stock/move?kind=issue", headers=MODAL).get_data(as_text=True)
    assert "A-2001" in html and "kies een artikel" in html


def test_order_with_stock_location_puts_deliveries_in_stock(client):
    terminals, warehouse = _article("A-2001"), _location("Magazijn")
    order = PurchaseOrder(number="PO-STOCK-1", supplier=Company.by_name("Cebeo"), status=OrderStatus.ORDERED,
                          stock_location_id=warehouse.id)
    order.lines.append(PurchaseOrderLine(article=terminals, description=terminals.description,
                                         quantity=Decimal(100), unit="st"))
    order.lines.append(PurchaseOrderLine(description="Vrije lijn", quantity=Decimal(1), unit="st"))
    db.session.add(order)
    db.session.commit()

    client.post(f"/purchasing/{order.id}/receive-all")
    assert svc.stock_on(terminals.id, warehouse.id) == 250
    assert not svc.rows_for([terminals])[0].below_minimum

    # Undoing the delivery also undoes the stock movement.
    receipt = order.lines[0].receipts[0]
    client.post(f"/purchasing/receipts/{receipt.id}/delete")
    assert svc.stock_on(terminals.id, warehouse.id) == 150


def test_project_order_without_stock_location_does_not_touch_stock(client):
    order = db.session.scalar(sa.select(PurchaseOrder).filter(PurchaseOrder.number.like("PO-%-0101")))
    before = db.session.scalar(sa.select(sa.func.count(StockMovement.id)))
    client.post(f"/purchasing/{order.id}/receive-all")
    assert db.session.scalar(sa.select(sa.func.count(StockMovement.id))) == before


def test_locations_management(client):
    client.post("/stock/locations", data={"name": "Werf Aquafin", "description": "Container"})
    location = _location("Werf Aquafin")
    assert location is not None
    client.post(f"/stock/locations/{location.id}", data={"name": "Werf Aquafin", "description": ""})
    db.session.refresh(location)
    assert location.active is False   # checkbox not sent -> inactive
    response = client.post("/stock/locations", data={"name": "werf aquafin"})
    assert "bestaat al" in response.get_data(as_text=True)


def test_article_page_shows_stock_and_min_stock_field(client):
    terminals = _article("A-2001")
    html = client.get(f"/articles/{terminals.id}/edit").get_data(as_text=True)
    assert "Minimumstock" in html and "Bestelwagen Jan" in html and "Stockbeweging" in html


def test_reorder_popup_prefills_shortage(client):
    terminals = _article("A-2001")
    html = client.get(f"/purchasing/add-article/{terminals.id}?qty=10", headers=MODAL).get_data(as_text=True)
    assert 'value="10.00"' in html


def test_dashboard_shows_below_minimum(client):
    assert "Onder minimumstock" in client.get("/").get_data(as_text=True)


def test_popup_defaults_to_location_with_most_stock(client):
    """'Bestelwagen Jan' sorts first, but the terminals are mostly in 'Magazijn'."""
    terminals, warehouse = _article("A-2001"), _location("Magazijn")
    html = client.get(f"/stock/move/{terminals.id}", headers=MODAL).get_data(as_text=True)
    assert f'<option selected value="{warehouse.id}">Magazijn</option>' in html
