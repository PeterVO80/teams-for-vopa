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
