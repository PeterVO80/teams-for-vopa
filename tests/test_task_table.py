"""Punch list: kanban board or table, with the same filters."""

import sqlalchemy as sa

from app.extensions import db
from app.models import Project, Task, User


def _table(html):
    return html.split('<table class="table table-hover align-middle mb-0 task-table">', 1)[1].split("</table>", 1)[0]


def test_switch_between_kanban_and_table(client):
    html = client.get("/tasks/?view=table&f=1").get_data(as_text=True)
    assert "task-table" in html and 'class="kanban"' not in html and "kanban.js" not in html
    html = client.get("/tasks/?view=kanban&f=1").get_data(as_text=True)
    assert 'class="kanban"' in html and "task-table" not in html


def test_table_uses_the_same_filters(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    other = db.session.scalar(sa.select(Project).filter_by(name="Waterzuivering SCADA"))
    db.session.add_all([Task(title="Taak van Jan op L3", project=project, assignee=jan, created_by_id=1),
                        Task(title="Taak op SCADA", project=other, created_by_id=1)])
    db.session.commit()
    rows = _table(client.get(f"/tasks/?view=table&project={project.id}&assignee=&f=1").get_data(as_text=True))
    assert "Taak van Jan op L3" in rows and "Taak op SCADA" not in rows
    rows = _table(client.get(f"/tasks/?view=table&project=&assignee={jan.id}&f=1").get_data(as_text=True))
    assert "Taak van Jan op L3" in rows and "Taak op SCADA" not in rows


def test_view_and_filters_are_remembered_and_kept_when_filtering(client):
    project = db.session.scalar(sa.select(Project).filter_by(name="Verpakkingslijn L3"))
    client.get("/tasks/?view=table&f=1")
    location = client.get("/tasks/").location                       # back via the menu
    assert "view=table" in location
    html = client.get(f"/tasks/?view=table&project={project.id}&f=1").get_data(as_text=True)
    assert '<input type="hidden" name="view" value="table">' in html  # the filter form keeps the view


def test_table_sorting(client):
    db.session.add_all([Task(title="AAA eerste", created_by_id=1), Task(title="ZZZ laatste", created_by_id=1)])
    db.session.commit()
    rows = _table(client.get("/tasks/?view=table&sort=title&dir=asc&f=1").get_data(as_text=True))
    assert rows.index("AAA eerste") < rows.index("ZZZ laatste")
    rows = _table(client.get("/tasks/?view=table&sort=title&dir=desc&f=1").get_data(as_text=True))
    assert rows.index("ZZZ laatste") < rows.index("AAA eerste")
    html = client.get("/tasks/?view=table&sort=bogus&f=1").get_data(as_text=True)   # unknown: default order
    assert "task-table" in html
