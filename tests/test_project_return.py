"""Material ordered for a project but not used: back to stock."""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import (Article, Company, GoodsReceipt, MovementKind, OrderStatus, Project, PurchaseOrder,
                        PurchaseOrderLine, StockLocation, StockMovement)
from app.services import stock as svc
from app.services.material import project_material


@pytest.fixture()
def setup(app):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    supplier = db.session.scalar(sa.select(Company).where(Company.is_supplier.is_(True)))
    article = Article(article_number="RET-1", description="Klemmenstrook", unit="st")
    location = StockLocation(name="Magazijn retour")
    db.session.add_all([article, location])
    db.session.flush()
    order = PurchaseOrder(number="PO-RET-1", supplier_id=supplier.id, project_id=project.id,
                          status=OrderStatus.ORDERED, order_date=date.today())
    line = PurchaseOrderLine(article_id=article.id, description=article.description, quantity=Decimal("20"),
                             unit="st", unit_price=Decimal("2"))
    order.lines.append(line)
    db.session.add(order)
    db.session.flush()
    db.session.add(GoodsReceipt(line_id=line.id, quantity=Decimal("20"), received_on=date.today()))
    db.session.commit()
    return project, article, location


def test_return_to_stock_and_material_overview(client, setup):
    project, article, location = setup
    assert svc.returnable_to_stock(article, project.id) == Decimal("20")
    response = client.post(f"/stock/move/{article.id}", headers={"X-Modal": "1"}, data={
        "article_id": article.id, "kind": "return", "location_id": location.id, "quantity": "6",
        "project_id": project.id, "moved_on": date.today().isoformat(), "to_location_id": 0})
    assert response.get_json() == {"ok": True}
    assert svc.stock_on(article.id, location.id) == Decimal("6")
    movement = db.session.scalar(sa.select(StockMovement).filter_by(article_id=article.id))
    assert movement.kind == MovementKind.RETURN and movement.project_id == project.id
    row = next(r for r in project_material(project.id).rows if r.article and r.article.id == article.id)
    assert (row.qty_received, row.qty_returned, row.qty_returnable) == (Decimal("20"), Decimal("6"), Decimal("14"))
    # Not counted as "taken from stock" for the project.
    assert all(u.article.id != article.id for u in svc.project_usage(project.id))


def test_cannot_return_more_than_delivered(client, setup):
    project, article, location = setup
    svc.register_return(article, location.id, Decimal("15"), user_id=1, project_id=project.id)
    db.session.commit()
    with pytest.raises(svc.StockError, match="maar 5"):
        svc.register_return(article, location.id, Decimal("6"), user_id=1, project_id=project.id)
    with pytest.raises(svc.StockError, match="Kies het project"):
        svc.register_return(article, location.id, Decimal("1"), user_id=1, project_id=None)
    response = client.post(f"/stock/move/{article.id}", headers={"X-Modal": "1"}, data={
        "article_id": article.id, "kind": "return", "location_id": location.id, "quantity": "99",
        "project_id": project.id, "moved_on": date.today().isoformat(), "to_location_id": 0})
    assert response.status_code == 422 and "nog niet naar stock teruggezet" in response.get_data(as_text=True)


def test_material_tab_offers_return_button_with_quantity(client, setup):
    project, article, _ = setup
    html = client.get(f"/projects/{project.id}").get_data(as_text=True)
    assert f"/stock/move/{article.id}?kind=return&amp;project={project.id}&amp;qty=20" in html
    popup = client.get(f"/stock/move/{article.id}?kind=return&project={project.id}&qty=20",
                       headers={"X-Modal": "1"}).get_data(as_text=True)
    assert 'id="kind-return" value="return" checked' in popup and 'value="20"' in popup


def test_deliveries_into_stock_are_not_returnable(app, setup):
    project, article, location = setup
    order = db.session.scalar(sa.select(PurchaseOrder).filter_by(number="PO-RET-1"))
    order.stock_location_id = location.id          # replenishment order: deliveries went into stock already
    db.session.commit()
    assert svc.returnable_to_stock(article, project.id) == Decimal("0")
