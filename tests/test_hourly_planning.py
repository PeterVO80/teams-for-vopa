"""Planning per hour and the working day (services/workday.py)."""

from datetime import date, time

import sqlalchemy as sa

from app.extensions import db
from app.models import Assignment, AssignmentKind, User
from app.services import backup
from app.services.workday import default_times, worked_hours, workday


def test_worked_hours_skip_the_break(app):
    assert workday().hours == 8.0                                         # 08:30 - 17:00, break 12:00 - 12:30
    assert worked_hours(time(8, 30), time(12, 0)) == 3.5
    assert worked_hours(time(8, 30), time(13, 0)) == 4.0
    assert worked_hours(time(12, 15), time(14, 0)) == 1.5
    assert worked_hours(time(13, 0), time(17, 0)) == 4.0


def test_default_times_follow_the_working_day(app):
    assert default_times(8) == (time(8, 30), time(17, 0))
    assert default_times(3) == (time(8, 30), time(11, 30))
    assert default_times(4) == (time(8, 30), time(13, 0))
    app.config.update(WORKDAY_START="07:30", WORKDAY_END="16:00")
    assert default_times(8) == (time(7, 30), time(16, 0))


def test_plan_per_hour_calculates_hours(client):
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    response = client.post("/team/assignments/new", headers={"X-Modal": "1"}, data={
        "user_id": jan.id, "kind": "other", "project_id": 0, "start_date": "2026-11-23", "end_date": "2026-11-24",
        "hours_per_day": "8", "start_time": "08:30", "end_time": "12:00", "note": "Werfbezoek"})
    assert response.get_json() == {"ok": True}
    a = db.session.scalar(sa.select(Assignment).filter_by(note="Werfbezoek"))
    assert (a.start_time, a.end_time, a.hours_per_day) == (time(8, 30), time(12, 0), 3.5)
    html = client.get("/team/?start=2026-11-23").get_data(as_text=True)
    assert "08:30-12:00" in html


def test_per_hour_validation(client):
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    data = {"user_id": jan.id, "kind": "other", "project_id": 0, "start_date": "2026-11-23",
            "end_date": "2026-11-23", "hours_per_day": "8"}
    response = client.post("/team/assignments/new", headers={"X-Modal": "1"}, data={**data, "start_time": "10:00"})
    assert response.status_code == 422 and "zowel het begin- als het einduur" in response.get_data(as_text=True)
    response = client.post("/team/assignments/new", headers={"X-Modal": "1"},
                           data={**data, "start_time": "14:00", "end_time": "10:00"})
    assert response.status_code == 422 and "na het beginuur" in response.get_data(as_text=True)


def test_form_shows_working_day_and_time_fields(client):
    html = client.get("/team/assignments/new?day=2026-11-23", headers={"X-Modal": "1"}).get_data(as_text=True)
    assert 'name="start_time"' in html and 'name="end_time"' in html
    assert 'data-break="12:00-12:30"' in html and "08:30" in html


def test_backup_keeps_times(app, tmp_path):
    app.config["BACKUP_DIR"] = str(tmp_path)
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    db.session.add(Assignment(user=jan, kind=AssignmentKind.OTHER, start_date=date(2026, 11, 25),
                              end_date=date(2026, 11, 25), hours_per_day=2, start_time=time(14, 0),
                              end_time=time(16, 0), note="Tijdtest"))
    db.session.commit()
    info = backup.create_backup("manual")
    backup.restore_backup(info.path)
    a = db.session.scalar(sa.select(Assignment).filter_by(note="Tijdtest"))
    assert (a.start_time, a.end_time) == (time(14, 0), time(16, 0))
