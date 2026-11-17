"""Back-up and restore of the complete database, from within the application.

A back-up is a ZIP file::

    manifest.json            format, version, creation time, database
                             (Alembic) revision, row counts per table
    tables/<table>.jsonl     one JSON object per row

It is made by *reflecting* the tables that really exist in the database (not
from the models), so the code can read and write back-ups of any schema
version. Everything the application stores lives in the database (there are
no uploaded files on disk), so this file is a complete copy.

Restore
-------
1. A safety back-up of the current data is made first.
2. If the back-up comes from an older application version, the database
   structure is first brought back to that version (Alembic downgrade), the
   data is loaded, and the normal migrations then bring it up to date again.
   Back-ups from a *newer* version are refused.
3. All current rows are deleted and the rows of the back-up are inserted in
   foreign-key order (self-references such as sub-pages of the wiki are
   filled in afterwards), then the PostgreSQL id sequences are reset.

With the same version, steps 3 run in one transaction: if anything fails,
nothing has changed.

Back-ups are stored in ``BACKUP_DIR`` (a Docker volume in production). The
``backup`` container of the stack makes one automatically every day
(``flask backup-create --loop``) and keeps them ``BACKUP_KEEP_DAYS`` days.
"""

from __future__ import annotations

import base64
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from flask import current_app

from ..extensions import db

FORMAT = "vopa-teams-backup"
FORMAT_VERSION = 1
#: Valid back-up file names (also protects against path tricks like "../").
NAME_RE = re.compile(r"^vopa-teams-[A-Za-z0-9_.-]+\.zip$")
BATCH = 1000


class BackupError(Exception):
    """A back-up could not be read or restored (message shown to the user)."""


@dataclass
class BackupInfo:
    name: str
    path: Path
    size: int
    created_at: datetime | None
    revision: str | None
    rows: int
    kind: str           # "manual", "auto", "safety" or "upload"
    error: str | None = None


# ------------------------------------------------------------------ helpers
def backup_dir() -> Path:
    path = Path(current_app.config["BACKUP_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise BackupError("Ongeldige bestandsnaam.")
    return backup_dir() / name


def _new_path(kind: str) -> Path:
    """A file name that doesn't exist yet (never overwrite a back-up)."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path, n = backup_dir() / f"vopa-teams-{stamp}-{kind}.zip", 1
    while path.exists() or path.with_suffix(".partial").exists():
        n += 1
        path = backup_dir() / f"vopa-teams-{stamp}-{kind}-{n}.zip"
    return path


def _generation_file() -> Path:
    return backup_dir() / ".login-generation"


def session_generation() -> str:
    """Current login generation; changes when a back-up is restored.

    Kept in a file next to the back-ups (not in the database, which a
    restore replaces) so every worker and container sees the same value.
    """
    path = _generation_file()
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        new_session_generation()
        return path.read_text().strip()


def new_session_generation() -> None:
    import secrets
    path = _generation_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(secrets.token_hex(8))
    os.replace(tmp, path)


def current_revision() -> str | None:
    """Alembic revision of the database (None when not managed by Alembic)."""
    if not sa.inspect(db.engine).has_table("alembic_version"):
        return None
    return db.session.scalar(sa.text("SELECT version_num FROM alembic_version"))


def _script_directory():
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(current_app.extensions["migrate"].migrate.get_config())


def _encode(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    raise TypeError(f"Cannot back up value of type {type(value).__name__}")


def _decoder(column: sa.Column):
    """Function turning a JSON value back into the column's Python type."""
    kind = column.type
    if isinstance(kind, sa.DateTime):
        return lambda v: None if v is None else datetime.fromisoformat(v)
    if isinstance(kind, sa.Date):
        return lambda v: None if v is None else date.fromisoformat(v)
    if isinstance(kind, sa.Time):
        return lambda v: None if v is None else time.fromisoformat(v)
    if isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float):
        return lambda v: None if v is None else Decimal(v)
    if isinstance(kind, sa.LargeBinary):
        return lambda v: None if v is None else base64.b64decode(v)
    return lambda v: v


def _tables() -> list[sa.Table]:
    """All tables in the database in foreign-key order (parents first)."""
    metadata = sa.MetaData()
    metadata.reflect(bind=db.engine)
    return [t for t in metadata.sorted_tables if t.name != "alembic_version"]


# ------------------------------------------------------------------ create
def create_backup(kind: str = "manual", created_by: str | None = None) -> BackupInfo:
    """Write a complete back-up to BACKUP_DIR and return its details."""
    db.session.commit()   # don't back up half-finished work of this request
    path = _new_path(kind)
    partial = path.with_suffix(".partial")
    counts: dict[str, int] = {}
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with db.engine.connect() as conn:
            for table in _tables():
                count = 0
                with zf.open(f"tables/{table.name}.jsonl", "w") as out:
                    result = conn.execution_options(stream_results=True, yield_per=BATCH).execute(sa.select(table))
                    for row in result.mappings():
                        out.write(json.dumps(dict(row), default=_encode, ensure_ascii=False).encode() + b"\n")
                        count += 1
                counts[table.name] = count
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": created_by,
            "kind": kind,
            "alembic_revision": current_revision(),
            "tables": counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    os.replace(partial, path)
    return read_info(path)


def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise BackupError("Dit is geen geldig back-upbestand van VOPA Teams.") from exc
    if manifest.get("format") != FORMAT or manifest.get("format_version", 0) > FORMAT_VERSION:
        raise BackupError("Dit is geen (ondersteund) back-upbestand van VOPA Teams.")
    return manifest


def read_info(path: Path) -> BackupInfo:
    try:
        manifest = read_manifest(path)
        error = None
    except BackupError as exc:
        manifest, error = {}, str(exc)
    created = manifest.get("created_at")
    return BackupInfo(
        name=path.name, path=path, size=path.stat().st_size,
        created_at=datetime.fromisoformat(created) if created else None,
        revision=manifest.get("alembic_revision"),
        rows=sum((manifest.get("tables") or {}).values()),
        kind=manifest.get("kind") or "upload", error=error,
    )


def list_backups() -> list[BackupInfo]:
    infos = [read_info(p) for p in backup_dir().glob("vopa-teams-*.zip") if NAME_RE.match(p.name)]
    return sorted(infos, key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def save_upload(storage) -> BackupInfo:
    """Store an uploaded back-up file (checked before it is kept)."""
    path = _new_path("upload")
    storage.save(path)
    try:
        read_manifest(path)
    except BackupError:
        path.unlink(missing_ok=True)
        raise
    return read_info(path)


def prune(kind: str, keep_days: int) -> int:
    """Delete back-ups of ``kind`` older than ``keep_days`` days."""
    limit = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = 0
    for info in list_backups():
        if info.kind == kind and info.created_at and info.created_at < limit:
            info.path.unlink(missing_ok=True)
            removed += 1
    return removed


# ------------------------------------------------------------------ restore
def _check_revision(target: str | None, current: str | None) -> None:
    if target == current or current is None or target is None:
        return
    from alembic.util import CommandError

    script = _script_directory()
    try:
        known = script.get_revision(target) is not None
        newer = {rev.revision for rev in script.walk_revisions(base=target, head=current)} if known else set()
    except CommandError:
        newer = set()
    if current not in newer:
        raise BackupError("Deze back-up komt van een nieuwere versie van de app. "
                          "Werk de app eerst bij en probeer het dan opnieuw.")


def restore_backup(path: Path, created_by: str | None = None) -> BackupInfo:
    """Replace ALL data by the back-up in ``path``; returns the safety back-up."""
    manifest = read_manifest(path)
    target, current = manifest.get("alembic_revision"), current_revision()
    _check_revision(target, current)

    safety = create_backup("safety", created_by)
    # Release the session's connection; loaded objects are reloaded afterwards.
    db.session.commit()
    db.session.expire_all()

    migrate = target != current and target is not None and current is not None
    if migrate:
        import flask_migrate
        flask_migrate.downgrade(revision=target)
    try:
        _load(path)
    finally:
        if migrate:
            import flask_migrate
            flask_migrate.upgrade()
        db.session.expire_all()
        new_session_generation()   # everybody logs in again
    return safety


def _load(path: Path) -> None:
    tables = _tables()
    with zipfile.ZipFile(path) as zf, db.engine.begin() as conn:
        for table in reversed(tables):
            conn.execute(table.delete())
        names = set(zf.namelist())
        for table in tables:
            member = f"tables/{table.name}.jsonl"
            if member not in names:
                continue
            decoders = {c.name: _decoder(c) for c in table.columns}
            # Columns pointing at the same table (wiki sub-pages, ...) are filled
            # in after all rows exist, so the order of the rows doesn't matter.
            self_refs = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
            pk = [c.name for c in table.primary_key.columns]
            later: list[dict] = []
            batch: list[dict] = []
            with zf.open(member) as lines:
                for line in lines:
                    raw = json.loads(line)
                    row = {k: decoders[k](v) for k, v in raw.items() if k in decoders}
                    if self_refs and any(row.get(c) is not None for c in self_refs):
                        later.append({**{k: row[k] for k in pk}, **{c: row.get(c) for c in self_refs}})
                        row.update({c: None for c in self_refs})
                    batch.append(row)
                    if len(batch) >= BATCH:
                        conn.execute(table.insert(), batch)
                        batch = []
            if batch:
                conn.execute(table.insert(), batch)
            for values in later:
                conn.execute(table.update()
                             .where(*[table.c[k] == values[k] for k in pk])
                             .values({c: values[c] for c in self_refs}))
        if conn.dialect.name == "postgresql":
            _reset_sequences(conn, tables)


def _reset_sequences(conn, tables: list[sa.Table]) -> None:
    """Make new ids continue after the restored ones (PostgreSQL)."""
    for table in tables:
        for column in table.primary_key.columns:
            if not isinstance(column.type, sa.Integer):
                continue
            sequence = conn.scalar(sa.text("SELECT pg_get_serial_sequence(:t, :c)"),
                                   {"t": table.name, "c": column.name})
            if sequence:
                conn.execute(sa.text(
                    f'SELECT setval(:s, COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 0) + 1, false)'),
                    {"s": sequence})
