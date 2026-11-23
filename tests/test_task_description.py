"""Task descriptions in Markdown with the WYSIWYG editor (check lists)."""

import sqlalchemy as sa

from app.extensions import db
from app.models import Task


def test_checklist_counts_ticked_boxes(app):
    task = Task(title="x", description="Te doen:\n\n* [x] kabels trekken\n* [ ] klemmen\n- [X] labels\n1. [ ] test\n\n[ ] geen lijst")
    assert task.checklist == (2, 4)
    assert Task(title="y", description=None).checklist == (0, 0)


def test_edit_page_uses_the_editor(client):
    task = db.session.scalar(sa.select(Task))
    html = client.get(f"/tasks/{task.id}/edit").get_data(as_text=True)
    assert 'data-md-editor' in html and 'data-md-height="360px"' in html
    assert "toastui-editor-all.min.js" in html


def test_board_shows_checklist_progress(client):
    task = db.session.scalar(sa.select(Task))
    task.description = "* [x] stap 1\n* [ ] stap 2\n* [ ] stap 3"
    db.session.commit()
    html = client.get("/tasks/?all=1&f=1").get_data(as_text=True)
    card = html.split(f'data-id="{task.id}"', 1)[1].split("</article>", 1)[0]
    assert "1/3" in card and "1 van 3 afgevinkt" in card


def test_markdown_description_is_saved_as_is(client):
    task = db.session.scalar(sa.select(Task))
    client.post(f"/tasks/{task.id}/edit", data={
        "title": task.title, "project_id": task.project_id or 0, "assignee_id": task.assignee_id or 0,
        "status": task.status.value, "priority": task.priority.value,
        "description": "Checklist\n\n* [ ] eerste\n* [x] tweede"})
    db.session.refresh(task)
    assert task.description == "Checklist\n\n* [ ] eerste\n* [x] tweede" and task.checklist == (1, 2)


def test_new_task_is_assigned_to_the_creator_by_default(app, client):
    admin_html = client.get("/tasks/new").get_data(as_text=True)
    from app.models import User
    admin = db.session.scalar(sa.select(User).filter_by(username="admin"))
    assert f'<option selected value="{admin.id}">' in admin_html
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    other = app.test_client()
    other.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert f'<option selected value="{jan.id}">' in other.get("/tasks/new").get_data(as_text=True)
    # An explicit choice in the link still wins.
    assert f'<option selected value="{jan.id}">' in client.get(f"/tasks/new?assignee={jan.id}").get_data(as_text=True)
