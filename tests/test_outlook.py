"""Outlook (Microsoft 365) calendar link, with a fake Microsoft Graph client."""

from datetime import date, datetime, timedelta

import urllib.parse

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Assignment, AssignmentKind, CalendarEvent, OutlookLink, User
from app.services import outlook


class FakeGraph:
    """Records requests and answers like Graph does."""

    def __init__(self, events=None, fail_for=()):
        self.events = events or {}          # email -> list of Graph event dicts
        self.fail_for = set(fail_for)
        self.calls = []
        self.counter = 0
        self.categories = {}                 # email -> Outlook categories

    def request(self, method, url, payload=None):
        url = urllib.parse.unquote(url)
        self.calls.append((method, url, payload))
        if any(email in url for email in self.fail_for):
            raise outlook.GraphError("ErrorAccessDenied: Access is denied.")
        if method == "POST":
            self.counter += 1
            return {"id": f"evt-{self.counter}"}
        return {}

    def get_all(self, url):
        url = urllib.parse.unquote(url)
        self.calls.append(("GET", url, None))
        if "masterCategories" in url:
            return list(self.categories.get(url.split("/")[2], []))
        if any(email in url for email in self.fail_for):
            raise outlook.GraphError("ErrorAccessDenied: Access is denied.")
        for email, events in self.events.items():
            if email in url:
                return events
        return []

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    def deletes(self):
        return [c for c in self.calls if c[0] == "DELETE"]


@pytest.fixture(autouse=True)
def _fresh_category_cache():
    outlook._category_done.clear()


@pytest.fixture()
def configured(app):
    app.config.update(MS365_TENANT_ID="t", MS365_CLIENT_ID="c", MS365_CLIENT_SECRET="s",
                      APP_URL="https://teams.example")
    for user in db.session.scalars(sa.select(User)):
        user.email = f"{user.username}@example.com"
    db.session.commit()
    return app


def _graph_event(id, subject, start, end, **extra):
    return {"id": id, "subject": subject, "start": {"dateTime": start + ".0000000"},
            "end": {"dateTime": end + ".0000000"}, "isAllDay": False, "showAs": "busy", **extra}


def _user(username):
    return db.session.scalar(sa.select(User).filter_by(username=username))


def _plan(user, start, end, **kwargs):
    assignment = Assignment(user=user, start_date=start, end_date=end, **kwargs)
    db.session.add(assignment)
    db.session.commit()
    return assignment


# ------------------------------------------------------------- planning -> Outlook
def test_runs_split_unplanned_weekend(app):
    a = Assignment(start_date=date(2026, 11, 13), end_date=date(2026, 11, 18))      # Fri -> Wed
    assert outlook._runs(a) == [(date(2026, 11, 13), date(2026, 11, 13)), (date(2026, 11, 16), date(2026, 11, 18))]
    a.include_weekends = True
    assert outlook._runs(a) == [(date(2026, 11, 13), date(2026, 11, 18))]


def test_event_payload_is_all_day_with_category(app):
    leave = Assignment(start_date=date(2026, 11, 16), end_date=date(2026, 11, 17),
                       kind=AssignmentKind.LEAVE, hours_per_day=4)
    payload = outlook._event_payload(leave, date(2026, 11, 16), date(2026, 11, 17), "Romance Standard Time", "https://x/")
    assert payload["isAllDay"] and payload["showAs"] == "oof" and payload["categories"] == ["VOPA Teams"]
    assert payload["start"]["dateTime"] == "2026-11-16T00:00:00"
    assert payload["end"]["dateTime"] == "2026-11-18T00:00:00"                     # end is exclusive
    assert payload["subject"] == "Verlof (4u/dag)"
    assert "https://x/team/?start=2026-11-16" in payload["body"]["content"]


def test_push_writes_and_replaces_appointments(configured):
    jan = _user("jan")
    a = _plan(jan, date(2026, 11, 13), date(2026, 11, 17), kind=AssignmentKind.TRAINING)
    graph = FakeGraph()
    outlook.push_assignment(graph, a)
    db.session.commit()
    assert len(graph.posts()) == 2 and "/users/jan@example.com/events" in graph.posts()[0][1]
    links = db.session.scalars(sa.select(OutlookLink).where(OutlookLink.assignment_id == a.id)).all()
    assert sorted(link.event_id for link in links) == ["evt-1", "evt-2"]
    assert a.outlook_synced_at is not None
    assert outlook.pending_assignments(date(2026, 11, 1), date(2026, 12, 1)).count(a) == 0

    # A change makes it pending again; pushing deletes the old appointments first.
    a.end_date = date(2026, 11, 13)
    db.session.commit()
    assert a in outlook.pending_assignments(date(2026, 11, 1), date(2026, 12, 1))
    outlook.push_assignment(graph, a)
    db.session.commit()
    assert {c[1].rsplit("/", 1)[1] for c in graph.deletes()} == {"evt-1", "evt-2"}
    assert [link.event_id for link in db.session.scalars(sa.select(OutlookLink))] == ["evt-3"]


def test_push_skips_users_without_sync(configured):
    jan = _user("jan")
    jan.outlook_sync = False
    a = _plan(jan, date(2026, 11, 16), date(2026, 11, 16))
    graph = FakeGraph()
    outlook.push_assignment(graph, a)
    db.session.commit()
    assert graph.posts() == [] and a.outlook_synced_at is not None


def test_deleting_planning_removes_outlook_appointment(configured, client):
    jan = _user("jan")
    a = _plan(jan, date(2026, 11, 16), date(2026, 11, 16))
    graph = FakeGraph()
    outlook.push_assignment(graph, a)
    db.session.commit()
    response = client.post(f"/team/assignments/{a.id}/delete", headers={"X-Modal": "1"})
    assert response.status_code in (200, 302)
    link = db.session.scalar(sa.select(OutlookLink))
    assert link.assignment_id is None                                      # orphan, kept for clean-up
    assert outlook.remove_orphans(graph) == 1
    db.session.commit()
    assert graph.deletes()[-1][1].endswith("/events/evt-1")
    assert db.session.scalar(sa.select(sa.func.count(OutlookLink.id))) == 0


# ------------------------------------------------------------- Outlook -> planning
def test_pull_skips_own_free_and_cancelled_events(configured):
    jan = _user("jan")
    db.session.add(OutlookLink(user_id=jan.id, event_id="own", start_date=date(2026, 11, 16), end_date=date(2026, 11, 16)))
    db.session.commit()
    graph = FakeGraph(events={"jan@example.com": [
        _graph_event("a", "Werfoverleg", "2026-11-16T09:00:00", "2026-11-16T10:30:00",
                     location={"displayName": "Gent"}),
        _graph_event("b", "Dokter", "2026-11-17T08:00:00", "2026-11-17T09:00:00", sensitivity="private"),
        _graph_event("own", "planning", "2026-11-16T00:00:00", "2026-11-17T00:00:00"),
        _graph_event("cat", "planning", "2026-11-16T00:00:00", "2026-11-17T00:00:00", categories=["VOPA Teams"]),
        _graph_event("free", "Webinar", "2026-11-16T12:00:00", "2026-11-16T13:00:00", showAs="free"),
        _graph_event("gone", "Geannuleerd", "2026-11-16T14:00:00", "2026-11-16T15:00:00", isCancelled=True),
    ]})
    assert outlook.pull_user(graph, jan, date(2026, 11, 1), date(2026, 12, 1)) == 2
    db.session.commit()
    events = {e.external_id: e for e in db.session.scalars(sa.select(CalendarEvent))}
    assert set(events) == {"a", "b"}
    assert events["a"].location == "Gent" and events["a"].start == datetime(2026, 11, 16, 9, 0)
    assert events["b"].display_subject == "Privé"

    # A second run replaces the cache: removed appointments disappear.
    graph.events["jan@example.com"] = graph.events["jan@example.com"][:1]
    outlook.pull_user(graph, jan, date(2026, 11, 1), date(2026, 12, 1))
    db.session.commit()
    assert [e.external_id for e in db.session.scalars(sa.select(CalendarEvent))] == ["a"]


def test_sync_all_reports_errors_per_user(configured):
    today = date.today()
    graph = FakeGraph(events={"jan@example.com": [_graph_event(
        "x", "Klantbezoek", f"{today}T13:00:00", f"{today}T15:00:00")]}, fail_for={"els@example.com"})
    report = outlook.sync_all(graph)
    assert report.read == 1 and report.written >= 1
    assert any("Els" in error for error in report.errors)
    assert _user("els").outlook_error.startswith("Lezen uit Outlook:")
    assert _user("jan").outlook_synced_at is not None and _user("jan").outlook_error is None
    # Nothing is pending afterwards for people without errors.
    jan_pending = [a for a in outlook.pending_assignments(today - timedelta(days=30), today + timedelta(days=183))
                   if a.user.username == "jan"]
    assert jan_pending == []


def test_sync_all_when_not_configured(app):
    report = outlook.sync_all(FakeGraph())
    assert report.errors == ["Microsoft 365 is niet ingesteld."]


def test_cli_without_configuration(app):
    result = app.test_cli_runner().invoke(args=["outlook-sync"])
    assert "niet ingesteld" in result.output


# ------------------------------------------------------------------- team planning
def test_board_shows_outlook_appointments(configured, client):
    jan = _user("jan")
    monday = date.today() - timedelta(days=date.today().weekday())
    db.session.add_all([
        CalendarEvent(user_id=jan.id, external_id="1", subject="Werfoverleg Aquafin",
                      start=datetime.combine(monday, datetime.min.time()).replace(hour=9),
                      end=datetime.combine(monday, datetime.min.time()).replace(hour=10)),
        CalendarEvent(user_id=jan.id, external_id="2", subject="Beurs Kortrijk", is_all_day=True,
                      start=datetime.combine(monday + timedelta(days=1), datetime.min.time()),
                      end=datetime.combine(monday + timedelta(days=3), datetime.min.time())),
    ])
    db.session.commit()
    html = client.get(f"/team/?start={monday}").get_data(as_text=True)
    assert "outlook-marker" in html and "Werfoverleg Aquafin" in html
    assert "outlook-bar" in html and "Beurs Kortrijk" in html


def test_outlook_out_of_office_counts_as_absence(configured):
    from app.services.workload import build_overview, planning_period

    jan = _user("jan")
    monday = date(2026, 11, 16)
    db.session.add(CalendarEvent(user_id=jan.id, external_id="oof", subject="Vakantie", is_all_day=True,
                                 show_as="oof", start=datetime(2026, 11, 18), end=datetime(2026, 11, 19)))
    db.session.commit()
    period = planning_period("weeks", monday.isoformat(), 1)
    _, rows = build_overview(period.start, days=period.days)
    row = next(r for r in rows if r.user.id == jan.id)
    cell = next(c for c in row.cells if c.day == date(2026, 11, 18))
    assert cell.is_absent


def test_user_form_and_status_page(configured, client):
    jan = _user("jan")
    html = client.get(f"/users/{jan.id}/edit").get_data(as_text=True)
    assert "Outlook-agenda koppelen" in html
    html = client.get("/users/outlook").get_data(as_text=True)
    assert "Synchroniseer nu" in html and "jan@example.com" in html


def test_status_page_explains_missing_configuration(client):
    html = client.get("/users/outlook").get_data(as_text=True)
    assert "nog niet ingesteld" in html and "Synchroniseer nu" not in html


def test_write_errors_are_shown_per_user(configured):
    class ReadOnly(FakeGraph):
        def request(self, method, url, payload=None):
            if method == "POST":
                raise outlook.GraphError("ErrorAccessDenied: Access is denied.")
            return super().request(method, url, payload)

    jan = _user("jan")
    _plan(jan, date.today(), date.today(), include_weekends=True)
    outlook.sync_all(ReadOnly())
    assert _user("jan").outlook_error.startswith("Schrijven naar Outlook: ErrorAccessDenied")


def test_planning_made_before_email_is_written_later(configured):
    jan = _user("jan")
    jan.email = None
    a = _plan(jan, date.today(), date.today(), include_weekends=True)
    outlook.sync_all(FakeGraph())                       # no mailbox yet: nothing written
    assert db.session.scalar(sa.select(sa.func.count(OutlookLink.id)).where(OutlookLink.assignment_id == a.id)) == 0
    jan.email = "jan@example.com"
    db.session.commit()
    graph = FakeGraph()
    report = outlook.sync_all(graph)
    assert report.written >= 1
    assert any("jan@example.com" in c[1] for c in graph.posts())
    assert db.session.scalar(sa.select(sa.func.count(OutlookLink.id)).where(OutlookLink.assignment_id == a.id)) == 1


def test_changing_email_rewrites_planning(configured, client):
    jan = _user("jan")
    a = _plan(jan, date.today(), date.today(), include_weekends=True)
    outlook.push_assignment(FakeGraph(), a)
    db.session.commit()
    assert a not in outlook.pending_assignments(*outlook.window())
    html = client.get(f"/users/{jan.id}/edit").get_data(as_text=True)
    assert "Outlook-agenda koppelen" in html
    form = {"username": "jan", "full_name": jan.full_name, "email": "jan.peeters@example.com",
            "role": jan.role.value, "color": jan.color, "active": "y", "outlook_sync": "y"}
    response = client.post(f"/users/{jan.id}/edit", data=form)
    assert response.status_code == 302
    assert a in outlook.pending_assignments(*outlook.window())


def test_moving_planning_to_other_day_and_person_moves_appointment(configured, client):
    jan, els = _user("jan"), _user("els")
    a = _plan(jan, date.today(), date.today(), include_weekends=True)
    outlook.sync_all(FakeGraph())
    old_event = db.session.scalar(sa.select(OutlookLink.event_id).where(OutlookLink.assignment_id == a.id))
    new_day = date.today() + timedelta(days=7)
    response = client.post(f"/team/assignments/{a.id}/reschedule", json={
        "start_date": new_day.isoformat(), "end_date": new_day.isoformat(), "user_id": els.id})
    assert response.get_json() == {"ok": True}
    assert a in outlook.pending_assignments(*outlook.window())          # the move is picked up
    graph = FakeGraph()
    outlook.sync_all(graph)
    assert any(c[1].endswith(f"jan@example.com/events/{old_event}") for c in graph.deletes())   # old one removed
    post = next(c for c in graph.posts() if "els@example.com" in c[1] and c[2]["start"]["dateTime"].startswith(new_day.isoformat()))
    assert post[2]["categories"] == ["VOPA Teams"]


def test_subject_contains_remark(app):
    from app.models import Project
    project = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    a = Assignment(start_date=date(2026, 11, 16), end_date=date(2026, 11, 16), project=project, kind=AssignmentKind.PROJECT,
                   note="FAT bij klant", hours_per_day=8)
    assert outlook._subject(a) == "Aquafin - Waterzuivering SCADA - FAT bij klant"
    assert outlook._subject(a, 4) == "Aquafin - Waterzuivering SCADA - FAT bij klant (4u/dag)"
    leave = Assignment(start_date=date(2026, 11, 16), end_date=date(2026, 11, 16),
                       kind=AssignmentKind.LEAVE, note="tandarts")
    assert outlook._subject(leave) == "Verlof: tandarts"                   # remark not repeated


def test_category_colour_setting(app):
    assert outlook.category_preset("Oranje") == "preset1"
    assert outlook.category_preset("donker blauw") == "preset22"
    assert outlook.category_preset("preset24") == "preset24"
    assert outlook.category_preset("") is None and outlook.category_preset("fuchsia") is None


def test_category_is_created_or_recoloured_once(configured):
    jan, els = _user("jan"), _user("els")
    graph = FakeGraph()
    graph.categories["els@example.com"] = [{"id": "c1", "displayName": "VOPA Teams", "color": "preset7"}]
    outlook.ensure_category(graph, jan)
    outlook.ensure_category(graph, els)
    outlook.ensure_category(graph, jan)                                    # cached: no new requests
    assert [(c[0], c[1], c[2]) for c in graph.calls if c[0] in ("POST", "PATCH")] == [
        ("POST", "/users/jan@example.com/outlook/masterCategories", {"displayName": "VOPA Teams", "color": "preset1"}),
        ("PATCH", "/users/els@example.com/outlook/masterCategories/c1", {"color": "preset1"}),
    ]


def test_missing_category_permission_is_not_fatal(configured):
    class NoMailboxSettings(FakeGraph):
        def get_all(self, url):
            if "masterCategories" in url:
                raise outlook.GraphError("ErrorAccessDenied: Access is denied.")
            return super().get_all(url)

    jan = _user("jan")
    _plan(jan, date.today(), date.today(), include_weekends=True)
    report = outlook.sync_all(NoMailboxSettings())
    assert report.written >= 1
    assert any("MailboxSettings.ReadWrite" in e for e in report.errors)
    assert _user("jan").outlook_error is None                              # the link itself works


def test_choose_category_colour_on_outlook_page(configured, client):
    html = client.get("/users/outlook").get_data(as_text=True)
    assert 'id="color-preset1" value="preset1" checked' in html            # default from MS365_CATEGORY_COLOR
    response = client.post("/users/outlook", data={"action": "color", "color": "preset22"})
    assert response.status_code == 302
    assert outlook.category_color() == "preset22"
    html = client.get("/users/outlook").get_data(as_text=True)
    assert 'id="color-preset22" value="preset22" checked' in html

    graph = FakeGraph()
    outlook.ensure_category(graph, _user("jan"))
    assert graph.posts()[0][2] == {"displayName": "VOPA Teams", "color": "preset22"}

    client.post("/users/outlook", data={"action": "color", "color": ""})   # "Niet aanpassen"
    assert outlook.category_color() is None
    assert client.post("/users/outlook", data={"action": "color", "color": "fuchsia"}).status_code == 400


def test_project_page_with_outlook_appointments(configured, client):
    from app.models import Project
    jan = _user("jan")
    monday = date.today() - timedelta(days=date.today().weekday())
    db.session.add(CalendarEvent(user_id=jan.id, external_id="x", subject="Beurs", is_all_day=True,
                                 start=datetime.combine(monday, datetime.min.time()),
                                 end=datetime.combine(monday + timedelta(days=1), datetime.min.time())))
    db.session.commit()
    project = db.session.scalar(sa.select(Project))
    assert client.get(f"/projects/{project.id}?planning_start={monday}").status_code == 200


def test_category_status_is_stored_and_failures_are_retried(configured, monkeypatch):
    jan = _user("jan")

    class Denied(FakeGraph):
        def get_all(self, url):
            if "masterCategories" in url:
                raise outlook.GraphError("ErrorAccessDenied: Access is denied.")
            return super().get_all(url)

    with pytest.raises(outlook.GraphError):
        outlook.ensure_category(Denied(), jan)
    assert jan.outlook_category.startswith("Fout: ErrorAccessDenied")

    graph = FakeGraph()
    outlook.ensure_category(graph, jan)                 # within the hour: not retried yet
    assert graph.calls == []
    clock = outlook.time.time() + outlook.CATEGORY_RECHECK_FAILED + 1
    monkeypatch.setattr(outlook.time, "time", lambda: clock)
    graph.categories["jan@example.com"] = [{"id": "c1", "displayName": "VOPA teams", "color": "preset7"}]
    outlook.ensure_category(graph, jan)                 # retried, existing "VOPA teams" recoloured
    assert ("PATCH", "/users/jan@example.com/outlook/masterCategories/c1", {"color": "preset1"}) in graph.calls
    assert jan.outlook_category.startswith("oranje")


def test_status_page_shows_category_status(configured, client):
    jan = _user("jan")
    jan.outlook_category = "Fout: ErrorAccessDenied: Access is denied."
    db.session.commit()
    html = client.get("/users/outlook").get_data(as_text=True)
    assert "Kleur categorie" in html and "Fout: ErrorAccessDenied" in html
