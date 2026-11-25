"""Time registration, distances to customers and the PDF timesheet."""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AppSetting, Company, Project, TimeEntry, User
from app.services import distance
from app.services import timesheet as svc

MODAL = {"X-Modal": "1"}
KROON = "Verpakkingslijn L3"


def project(name=KROON):
    return db.session.scalar(sa.select(Project).filter_by(name=name))


def user(username):
    return db.session.scalar(sa.select(User).filter_by(username=username))


def login(app, username):
    client = app.test_client()
    assert client.post("/auth/login", data={"username": username, "password": "demo1234"}).status_code == 302
    return client


@pytest.fixture()
def routes(monkeypatch):
    """Fake OpenStreetMap: every address is found, every route is 42.35 km."""
    calls = []

    def fake(url):
        calls.append(url)
        if "nominatim" in url:
            return [{"lon": "3.7", "lat": "51.0"}]
        return {"routes": [{"distance": 42350.0}]}

    monkeypatch.setattr(distance, "_get_json", fake)
    return calls


def set_office():
    for key, value in {"office.street": "Industrieweg 1", "office.postal_code": "9000",
                       "office.city": "Gent", "office.country": "België"}.items():
        AppSetting.put(key, value)
    db.session.commit()


def test_settings_page_stores_office_address(client):
    html = client.get("/users/settings").get_data(as_text=True)
    assert "OpenStreetMap" in html
    client.post("/users/settings", data={"company.name": "VOPA bv", "office.street": "Industrieweg 1",
                                         "office.postal_code": "9000", "office.city": "Gent",
                                         "office.country": "België"})
    assert distance.company_name() == "VOPA bv"
    assert distance.office_text() == "Industrieweg 1, 9000 Gent, België"


def test_settings_page_is_for_administrators(app):
    assert login(app, "lies").get("/users/settings").status_code == 403


def test_distance_calculated_when_relation_saved_and_manual_value_kept(client, routes):
    set_office()
    kroon = Company.by_name("Brouwerij De Kroon")
    data = {"name": kroon.name, "is_customer": "y", "active": "y", "street": "Brouwerijstraat 5",
            "postal_code": "8000", "city": "Brugge", "country": "België"}
    client.post(f"/relations/{kroon.id}/edit", data=data)
    db.session.refresh(kroon)
    assert kroon.distance_km == Decimal("42.4") and kroon.distance_auto
    assert any("Brouwerijstraat" in url for url in routes)

    # A value typed by hand is kept, also when the address changes later.
    client.post(f"/relations/{kroon.id}/edit", data={**data, "distance_km": "55"})
    db.session.refresh(kroon)
    assert kroon.distance_km == Decimal("55") and not kroon.distance_auto
    routes.clear()
    client.post(f"/relations/{kroon.id}/edit", data={**data, "distance_km": "55", "city": "Oostende"})
    db.session.refresh(kroon)
    assert kroon.distance_km == Decimal("55") and not routes


def test_no_calculation_without_office_address(client, routes):
    kroon = Company.by_name("Brouwerij De Kroon")
    response = client.post(f"/relations/{kroon.id}/edit", follow_redirects=True,
                           data={"name": kroon.name, "is_customer": "y", "active": "y", "city": "Brugge"})
    assert "Afstand niet berekend" not in response.get_data(as_text=True)
    assert not routes and db.session.get(Company, kroon.id).distance_km is None


def test_calc_distance_endpoint(client, routes):
    assert client.get("/relations/distance?city=Brugge").get_json()["ok"] is False   # no office yet
    set_office()
    data = client.get("/relations/distance?street=Markt+1&postal_code=8000&city=Brugge").get_json()
    assert data["ok"] and data["km"] == 42.4


def test_register_hours_with_field_service(app, routes):
    aquafin = Company.by_name("Aquafin")
    aquafin.distance_km = Decimal("30.5")
    db.session.commit()
    lies = login(app, "lies")
    p = project("Waterzuivering SCADA")
    html = lies.get(f"/time/new?project={p.id}", headers=MODAL).get_data(as_text=True)
    assert f'data-customer="{p.customer_id}"' in html and 'data-distance="30.5"' in html
    response = lies.post("/time/new", headers=MODAL, data={
        "work_date": "2026-10-05", "project_id": p.id, "hours": "7.5", "description": "Inbedrijfstelling",
        "field_service": "y", "customer_id": 0})
    assert response.get_json() == {"ok": True}
    entry = db.session.scalar(sa.select(TimeEntry))
    assert entry.user == user("lies") and entry.customer == aquafin       # customer of the project
    assert entry.km == Decimal("61.0")                                  # there and back
    # Without field service no customer or kilometres are stored.
    lies.post(f"/time/{entry.id}/edit", headers=MODAL, data={
        "work_date": "2026-10-05", "project_id": p.id, "hours": "8", "km": "61"})
    db.session.refresh(entry)
    assert entry.hours == Decimal("8") and entry.km is None and entry.customer_id is None


def test_validation_and_permissions(app, client):
    lies = login(app, "lies")
    response = lies.post("/time/new", headers=MODAL, data={"work_date": "2026-10-05", "project_id": 0, "hours": "30"})
    assert response.status_code == 422
    p = project()
    entry = TimeEntry(user=user("admin"), project=p, work_date=date(2026, 10, 1), hours=Decimal("2"))
    db.session.add(entry)
    db.session.commit()
    assert lies.get(f"/time/{entry.id}/edit").status_code == 403
    assert lies.post(f"/time/{entry.id}/delete").status_code == 403
    assert "Admin Tester" not in lies.get("/time/?month=2026-10").get_data(as_text=True)   # only own hours
    html = client.get(f"/time/?month=2026-10&user=all&project={p.id}").get_data(as_text=True)
    assert "Admin Tester" in html
    client.post(f"/time/{entry.id}/delete")
    assert db.session.get(TimeEntry, entry.id) is None


def test_timesheet_groups_per_day_per_employee(app):
    p = project()
    lies, admin = user("lies"), user("admin")
    db.session.add_all([
        TimeEntry(user=lies, project=p, work_date=date(2026, 10, 6), hours=Decimal("3"), description="Kast bedraden"),
        TimeEntry(user=lies, project=p, work_date=date(2026, 10, 6), hours=Decimal("4.5"), description="Testen",
                  field_service=True, km=Decimal("61")),
        TimeEntry(user=admin, project=p, work_date=date(2026, 10, 6), hours=Decimal("2")),
        TimeEntry(user=lies, project=p, work_date=date(2026, 11, 2), hours=Decimal("8")),   # other month
    ])
    db.session.commit()
    sheet = svc.build(p.id, date(2026, 10, 1), date(2026, 10, 31))
    assert len(sheet.rows) == 2
    row = next(r for r in sheet.rows if r.user == lies)
    assert row.hours == Decimal("7.5") and row.km == Decimal("61") and row.field_service
    assert row.description == "Kast bedraden\nTesten"
    assert sheet.total_hours == Decimal("9.5") and sheet.total_km == Decimal("61")
    assert {u.username: h for u, h, _ in sheet.per_user} == {"lies": Decimal("7.5"), "admin": Decimal("2")}


def test_previous_month_and_report_default(client):
    assert svc.previous_month(date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert svc.month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    start, end = svc.previous_month()
    p = project()
    html = client.get(f"/time/report?project={p.id}").get_data(as_text=True)
    assert f'value="{start.isoformat()}"' in html and f'value="{end.isoformat()}"' in html


def test_pdf_download(client):
    p = project()
    db.session.add(TimeEntry(user=user("lies"), project=p, work_date=date(2026, 10, 6), hours=Decimal("7.5"),
                             description="Inbedrijfstelling <PLC>"))
    db.session.commit()
    response = client.get(f"/time/report.pdf?project={p.id}&start=2026-10-01&end=2026-10-31")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert "2026-10-01 tot 2026-10-31.pdf" in response.headers["Content-Disposition"]
    assert client.get("/time/report.pdf").status_code == 404


def test_project_page_links_to_timesheet(client):
    p = project()
    html = client.get(f"/projects/{p.id}").get_data(as_text=True)
    assert f"/time/report?project={p.id}" in html and f"/time/new?project={p.id}" in html


def test_project_hours_tab_and_dashboard_button(client):
    p = project()
    db.session.add(TimeEntry(user=user("lies"), project=p, work_date=date(2026, 10, 6), hours=Decimal("7.5"),
                             description="Bekabeling kast", field_service=True, km=Decimal("61")))
    db.session.commit()
    html = client.get(f"/projects/{p.id}").get_data(as_text=True)
    assert 'data-bs-target="#tab-hours">Uren (7,5)' in html
    assert "Bekabeling kast" in html and "Lies" in html
    assert "/time/new" in client.get("/").get_data(as_text=True)


def test_edit_without_customer_stores_no_customer(client):
    """The "no customer" choice (0) must never reach the database (foreign key on PostgreSQL)."""
    p = project()
    for field_service in ({}, {"field_service": "y"}):
        response = client.post("/time/new", headers=MODAL, data={
            "work_date": "2026-10-05", "project_id": p.id, "hours": "2", "customer_id": 0, **field_service})
        assert response.status_code == 200
        entry = db.session.scalars(sa.select(TimeEntry).order_by(TimeEntry.id.desc())).first()
        response = client.post(f"/time/{entry.id}/edit", headers=MODAL, data={
            "work_date": "2026-10-06", "project_id": p.id, "hours": "3", "customer_id": 0})
        assert response.get_json() == {"ok": True}
        db.session.refresh(entry)
        assert entry.hours == Decimal("3") and entry.customer_id is None


def test_week_entry_creates_one_entry_per_filled_day(app):
    aquafin = Company.by_name("Aquafin")
    aquafin.distance_km = Decimal("20")
    db.session.commit()
    lies = login(app, "lies")
    p = project("Waterzuivering SCADA")
    html = lies.get(f"/time/week?start=2026-11-18&project={p.id}", headers=MODAL).get_data(as_text=True)
    assert "Week 47" in html and 'name="week_start" value="2026-11-16"' in html      # Monday of that week
    assert 'name="hours-6"' in html and "Ma-vr: 8 uur" in html
    response = lies.post("/time/week", headers=MODAL, data={
        "week_start": "2026-11-16", "project_id": p.id, "hours-0": "8", "hours-1": "7,5", "hours-2": "",
        "hours-3": "4", "description": "Programmatie", "field_service": "y", "customer_id": 0})
    assert response.get_json() == {"ok": True}
    entries = db.session.scalars(sa.select(TimeEntry).order_by(TimeEntry.work_date)).all()
    assert [(e.work_date.day, e.hours) for e in entries] == [(16, Decimal("8")), (17, Decimal("7.5")), (19, Decimal("4"))]
    assert all(e.user == user("lies") and e.description == "Programmatie" for e in entries)
    assert all(e.customer == aquafin and e.km == Decimal("40") for e in entries)            # per day, there and back

    # Already registered hours are shown per day, and a day can't exceed 24 hours.
    html = lies.get("/time/week?start=2026-11-16", headers=MODAL).get_data(as_text=True)
    assert "al 8 u" in html and "al 7,5 u" in html
    response = lies.post("/time/week", headers=MODAL, data={"week_start": "2026-11-16", "project_id": p.id,
                                                            "hours-0": "20"})
    assert response.status_code == 422 and "Meer dan 24 uur op 16/11" in response.get_data(as_text=True)
    response = lies.post("/time/week", headers=MODAL, data={"week_start": "2026-11-16", "project_id": p.id})
    assert response.status_code == 422 and "minstens één dag" in response.get_data(as_text=True)


def test_overview_grouped_per_week_with_empty_workdays(client):
    p = project()
    db.session.add_all([
        TimeEntry(user=user("admin"), project=p, work_date=date(2026, 10, 6), hours=Decimal("3"), description="A"),
        TimeEntry(user=user("admin"), project=p, work_date=date(2026, 10, 6), hours=Decimal("5"), description="B"),
        TimeEntry(user=user("admin"), project=p, work_date=date(2026, 10, 14), hours=Decimal("6")),
    ])
    db.session.commit()
    weeks = svc.weeks(db.session.scalars(sa.select(TimeEntry)).all(), date(2026, 10, 1), date(2026, 10, 31),
                      empty_workdays_until=date(2026, 10, 16))
    assert [w.number for w in weeks] == [42, 41, 40]                         # newest week first
    assert [w.total for w in weeks] == [Decimal("6"), Decimal("8"), Decimal("0")]
    week41 = weeks[1]
    assert [d.day.day for d in week41.days] == [5, 6, 7, 8, 9]             # Mon-Fri, gaps included
    assert week41.days[1].total == Decimal("8") and len(week41.days[1].entries) == 2
    html = client.get("/time/?month=2026-10").get_data(as_text=True)
    assert "Week 41" in html and "Week 42" in html and "geen uren" in html
    assert "Week invullen" in html and "14 u" in html                       # project total for the month


def test_hours_accept_decimal_comma(client):
    p = project()
    response = client.post("/time/new", headers=MODAL, data={"work_date": "2026-10-05", "project_id": p.id,
                                                              "hours": "2,25"})
    assert response.get_json() == {"ok": True}
    assert db.session.scalar(sa.select(TimeEntry)).hours == Decimal("2.25")
