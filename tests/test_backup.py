"""Back-up and restore from within the application."""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Article, Project, Task, User, WikiPage
from app.services import backup


@pytest.fixture(autouse=True)
def backup_dir(app, tmp_path):
    app.config["BACKUP_DIR"] = str(tmp_path / "backups")
    return tmp_path / "backups"


def _counts():
    return {m.__tablename__: db.session.scalar(sa.select(sa.func.count()).select_from(m))
            for m in (Project, Task, User, WikiPage, Article)}


def test_round_trip_restores_all_data(app):
    article = Article(article_number="ART-1", description="Kabel 3G1,5", unit_price=Decimal("1.2345"))
    db.session.add(article)
    # A wiki page whose parent has a HIGHER id: the restore must not depend on row order.
    child = WikiPage(title="Kind", slug="kind", content="x")
    db.session.add(child)
    db.session.flush()
    parent = WikiPage(title="Ouder", slug="ouder", content="y")
    db.session.add(parent)
    db.session.flush()
    child.parent_id = parent.id
    db.session.commit()
    before = _counts()
    project = db.session.scalar(sa.select(Project))
    project_name, project_start = project.name, project.start_date

    info = backup.create_backup("manual", "admin")
    assert info.rows > 0 and info.kind == "manual"

    # Change things after the back-up.
    project.name = "Gewijzigd"
    db.session.delete(db.session.scalar(sa.select(Task)))
    db.session.add(Task(title="Nieuw na back-up", created_by_id=1))
    db.session.commit()

    safety = backup.restore_backup(info.path, "admin")
    assert safety.kind == "safety" and safety.path.exists()
    db.session.expire_all()

    assert _counts() == before
    project = db.session.scalar(sa.select(Project).filter_by(id=project.id))
    assert (project.name, project.start_date) == (project_name, project_start)
    assert db.session.scalar(sa.select(Task).filter_by(title="Nieuw na back-up")) is None
    restored = db.session.scalar(sa.select(Article).filter_by(article_number="ART-1"))
    assert restored.unit_price == Decimal("1.2345")
    kind = db.session.scalar(sa.select(WikiPage).filter_by(slug="kind"))
    assert kind.parent.slug == "ouder"
    assert isinstance(restored.created_at, datetime)

    # New rows get fresh ids after a restore (sequences reset on PostgreSQL).
    db.session.add(Task(title="Na herstel", created_by_id=1))
    db.session.commit()


def test_restore_of_safety_backup_undoes_restore(app):
    first = backup.create_backup("manual")
    db.session.add(Task(title="Later toegevoegd", created_by_id=1))
    db.session.commit()
    safety = backup.restore_backup(first.path)
    assert db.session.scalar(sa.select(Task).filter_by(title="Later toegevoegd")) is None
    backup.restore_backup(safety.path)
    assert db.session.scalar(sa.select(Task).filter_by(title="Later toegevoegd")) is not None


def test_invalid_files_are_refused(app, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    bad = backup_dir / "vopa-teams-kapot.zip"
    bad.write_bytes(b"no zip")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(bad)
    other = backup_dir / "vopa-teams-ander.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "iets-anders"}))
    with pytest.raises(backup.BackupError):
        backup.read_manifest(other)
    with pytest.raises(backup.BackupError):
        backup.backup_path("../geheim.zip")


def test_newer_backups_are_refused(app):
    backup._check_revision("0012", "0013")                      # older: allowed (migrated)
    with pytest.raises(backup.BackupError, match="nieuwere versie"):
        backup._check_revision("0013", "0012")
    with pytest.raises(backup.BackupError, match="nieuwere versie"):
        backup._check_revision("9999", "0013")


def test_prune_only_removes_old_automatic_backups(app):
    old_auto = backup.create_backup("auto")
    manual = backup.create_backup("manual")
    with zipfile.ZipFile(old_auto.path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        members = {n: zf.read(n) for n in zf.namelist() if n != "manifest.json"}
    manifest["created_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with zipfile.ZipFile(old_auto.path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            zf.writestr(name, data)
    assert backup.prune("auto", 14) == 1
    assert not old_auto.path.exists() and manual.path.exists()


# ------------------------------------------------------------------ page
def test_page_is_admin_only(app):
    client = app.test_client()
    client.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert client.get("/admin/backups/").status_code == 403
    assert client.post("/admin/backups/new").status_code == 403


def test_create_download_upload_and_restore_via_page(client, backup_dir):
    assert client.post("/admin/backups/new").status_code == 302
    html = client.get("/admin/backups/").get_data(as_text=True)
    name = next(p.name for p in backup_dir.glob("*.zip"))
    assert name in html and "manueel" in html

    data = client.get(f"/admin/backups/{name}/download").data
    assert zipfile.ZipFile(io.BytesIO(data)).read("manifest.json")

    response = client.post("/admin/backups/upload", data={"file": (io.BytesIO(data), "kopie.zip")},
                           content_type="multipart/form-data")
    assert response.status_code == 302 and len(list(backup_dir.glob("*.zip"))) == 2
    response = client.post("/admin/backups/upload", data={"file": (io.BytesIO(b"rommel"), "x.zip")},
                           content_type="multipart/form-data", follow_redirects=True)
    assert "geen geldig back-upbestand" in response.get_data(as_text=True)
    assert len(list(backup_dir.glob("*.zip"))) == 2

    db.session.add(Task(title="Wordt teruggedraaid", created_by_id=1))
    db.session.commit()
    response = client.post(f"/admin/backups/{name}/restore", data={"confirm": "nee"})
    assert db.session.scalar(sa.select(Task).filter_by(title="Wordt teruggedraaid")) is not None
    response = client.post(f"/admin/backups/{name}/restore", data={"confirm": "herstel"})
    assert response.status_code == 302 and "/auth/login" in response.location
    db.session.expire_all()
    assert db.session.scalar(sa.select(Task).filter_by(title="Wordt teruggedraaid")) is None
    assert client.get("/").status_code == 302                 # logged out after a restore
    assert any("safety" in p.name for p in backup_dir.glob("*.zip"))


def test_unknown_or_unsafe_names_give_404(client):
    assert client.get("/admin/backups/vopa-teams-bestaat-niet.zip/download").status_code == 404
    assert client.get("/admin/backups/..%2Fapp.py/download").status_code == 404


def test_cli_backup_create(app, backup_dir):
    result = app.test_cli_runner().invoke(args=["backup-create"])
    assert "Back-up:" in result.output and len(list(backup_dir.glob("*.zip"))) == 1


def test_restore_logs_out_everybody(app, client):
    other = app.test_client()
    other.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert other.get("/").status_code == 200
    info = backup.create_backup("manual")
    client.post(f"/admin/backups/{info.name}/restore", data={"confirm": "HERSTEL"})
    assert other.get("/").status_code == 302                  # session from before the restore
    other.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert other.get("/").status_code == 200


def test_download_now_and_upload_with_immediate_restore(client, backup_dir):
    response = client.post("/admin/backups/download-now")
    assert response.status_code == 200 and response.mimetype == "application/zip"
    assert "attachment" in response.headers["Content-Disposition"]
    data = response.data
    assert zipfile.ZipFile(io.BytesIO(data)).read("manifest.json")

    db.session.add(Task(title="Na download", created_by_id=1))
    db.session.commit()
    # Without the confirmation word nothing is uploaded or restored.
    client.post("/admin/backups/upload", data={"file": (io.BytesIO(data), "b.zip"), "restore": "1"},
                content_type="multipart/form-data")
    assert db.session.scalar(sa.select(Task).filter_by(title="Na download")) is not None
    response = client.post("/admin/backups/upload",
                           data={"file": (io.BytesIO(data), "b.zip"), "restore": "1", "confirm": "HERSTEL"},
                           content_type="multipart/form-data")
    assert response.status_code == 302 and "/auth/login" in response.location
    db.session.expire_all()
    assert db.session.scalar(sa.select(Task).filter_by(title="Na download")) is None


def test_backup_warning_when_backups_stop(app, client, backup_dir):
    assert "nog geen enkele back-up" in backup.backup_warning()
    assert "nog geen enkele back-up" in client.get("/").get_data(as_text=True)
    info = backup.create_backup("auto")
    assert backup.backup_warning() is None
    assert "Back-ups:" not in client.get("/").get_data(as_text=True)
    # Make the only back-up 30 hours old.
    with zipfile.ZipFile(info.path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        members = {n: zf.read(n) for n in zf.namelist() if n != "manifest.json"}
    manifest["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with zipfile.ZipFile(info.path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            zf.writestr(name, data)
    assert "Controleer de container 'backup'" in backup.backup_warning()
    jan = app.test_client()
    jan.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert "Back-ups:" not in jan.get("/").get_data(as_text=True)          # only administrators


def test_backup_before_upgrade_skips_when_nothing_to_migrate(app, backup_dir):
    result = app.test_cli_runner().invoke(args=["backup-before-upgrade"])
    assert "Geen database-update nodig" in result.output and result.exit_code == 0
