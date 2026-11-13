# VOPA Teams

Web application for running industrial automation projects. Every customer
assignment is a **project**. All modules hang off that project:

| Module | What it does |
|---|---|
| **Projects** | Project card with customer, project manager and timeline, plus an overview of the project's tasks, orders and notes. |
| **Purchasing** | Purchase orders per project/supplier, order lines pulled from the article database, **delivery tracking per line** (partial deliveries, delivery notes, who received it). Order status updates automatically. A list of all outstanding deliveries. Excel export per order. |
| **Articles** | Article database with search and filters. **Excel import/export** (round trip: export → edit in Excel → import). |
| **Punch list** | Kanban board with *New · In progress · On hold · Done · Cancelled*. Drag & drop, filters per project/team member, priority, deadline and estimate. |
| **Team overview** | Planning grid (people × working days) with load per day and period, absences (leave, sick, training), and open tasks per person. |
| **Notes** | Markdown notes with a WYSIWYG editor, **tags** (e.g. *Status meeting*, *Customer feedback*), optional project link, search and pinning. |
| **Wiki** | Hierarchical Markdown knowledge base with WYSIWYG editor, `[[Page title]]` links, search and **full version history** (view and restore). |

The UI is in Dutch; the code, comments and documentation are in English.

---

## Technology choices

| Layer | Choice | Why |
|---|---|---|
| Web framework | **Flask 3** (app factory + blueprints) | The team already knows it. Blueprints give one clear package per module. |
| ORM / database | **SQLAlchemy 2** (typed `Mapped[...]` models) on **PostgreSQL 16** | Modern, type-checkable models. psycopg 3 as the driver. |
| Migrations | **Flask-Migrate (Alembic)** | Versioned schema changes; run automatically when the container starts. |
| Auth / security | Flask-Login, Flask-WTF (CSRF), werkzeug password hashing | Every POST, including AJAX, is CSRF-protected. Markdown is sanitised with `nh3` (no XSS). |
| Front-end | Server-rendered Jinja + Bootstrap 5, SortableJS, Toast UI Editor | No separate JS build step or SPA. All libraries are **bundled locally** (`app/static/vendor`), so the app also works without internet access. |
| Excel | openpyxl | Read and write `.xlsx` without Excel installed. |
| Runtime | gunicorn in Docker, `docker compose` with PostgreSQL | One command to start the whole stack. |

**Why Markdown for notes and wiki?** Plain text is easy to search, back up,
diff and export (`.md` download button), and isn't locked into this
application. Users don't have to learn the syntax: the editor works WYSIWYG
by default, with a tab to switch to raw Markdown.

---

## Quick start (Docker)

Requirements: Docker with the Compose plugin.

```bash
cp .env.example .env
# Edit .env: at least SECRET_KEY and the passwords!
docker compose up -d --build
```

Open <http://localhost:8000> and log in with `ADMIN_USERNAME` /
`ADMIN_PASSWORD` from `.env` (default `admin` / `admin`, so change it
straight away via *Change password*).

On every start the container does the following (`docker/entrypoint.sh`):
1. wait until PostgreSQL is reachable;
2. apply pending migrations (`flask db upgrade`);
3. create the first admin if the database has no users yet;
4. start gunicorn.

Optional demo data (projects, articles, tasks, planning, notes, wiki);
demo users have the password `demo1234`:

```bash
docker compose exec web flask seed-demo
```

### Useful commands

```bash
docker compose logs -f web                                     # follow the logs
docker compose exec web flask create-user jan --name "Jan Peeters" --admin
docker compose exec db pg_dump -U vopa vopa > backup.sql       # back up the database
docker compose exec -T db psql -U vopa vopa < backup.sql       # restore a backup
```

The database lives in the Docker volume `pgdata` and survives
`docker compose down` and image upgrades. **`docker compose down -v` deletes
the data.**

### Upgrading to a new version

```bash
git pull
docker compose up -d --build     # migrations run automatically
```

---

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# PostgreSQL via Docker (or use your own server)
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://vopa:vopa@localhost:5432/vopa
```

> To reach the database from your host machine, temporarily add
> `ports: ["5432:5432"]` to the `db` service in `docker-compose.yml`.

```bash
export FLASK_APP=wsgi.py FLASK_DEBUG=1
flask db upgrade
flask create-user admin --name "Admin" --admin
flask seed-demo          # optional
flask run                # http://127.0.0.1:5000, with auto-reload
```

### Tests

```bash
pytest                                                    # in-memory SQLite, no setup needed
TEST_DATABASE_URL=postgresql+psycopg://vopa:vopa@localhost:5432/vopa_test pytest   # against PostgreSQL
```

Use an **empty, throwaway database** for `TEST_DATABASE_URL`. The tests
drop all tables when they finish.

### Changing the database schema

1. Change or add a model in `app/models/`.
2. Generate a migration: `flask db migrate -m "Short description"`.
3. **Review** the generated file in `migrations/versions/`. Alembic
   doesn't detect everything (e.g. column renames).
4. Apply it: `flask db upgrade`, and commit the migration together with the code.

`flask db check` reports whether the models and migrations are in sync.

---

## Project structure

```
app/
├── __init__.py            # create_app(): app factory, blueprints, Jinja filters
├── config.py              # configuration via environment variables
├── extensions.py          # db, migrate, login_manager, csrf (+ naming convention)
├── forms.py               # all WTForms forms (server-side validation)
├── cli.py                 # flask create-user / ensure-admin / seed-demo
├── utils.py               # admin_required, safe redirects, ...
├── models/                # SQLAlchemy models, one file per domain
│   ├── base.py            #   LabeledEnum (value + Dutch label), TimestampMixin
│   ├── user.py  project.py  article.py  purchasing.py
│   ├── task.py  planning.py  knowledge.py (notes, tags, wiki)
├── services/              # business logic with no HTTP dependency
│   ├── excel.py           #   article import/export
│   ├── markdown_render.py #   Markdown → safe HTML, [[wiki links]]
│   └── workload.py        #   team overview calculations
├── blueprints/            # one blueprint (URL prefix) per module
│   ├── __init__.py        #   protected_blueprint(): login required by default
│   ├── auth.py main.py users.py projects.py articles.py
│   ├── purchasing.py tasks.py team.py notes.py wiki.py
├── templates/             # Jinja templates per module, base.html + _macros.html
└── static/
    ├── css/app.css
    ├── js/                # app.js, kanban.js, editor.js, order_lines.js
    └── vendor/            # Bootstrap, icons, SortableJS, Toast UI (see its README)
migrations/                # Alembic migrations
tests/                     # pytest: smoke tests for every page + business rules
docker/entrypoint.sh       # container start-up
Dockerfile, docker-compose.yml, .env.example
```

### Conventions

* **Statuses are `LabeledEnum`s** (`app/models/base.py`): the database value
  (`"in_progress"`) and the Dutch label (`"Wordt uitgevoerd"`) are defined
  together. They're stored as `VARCHAR`, so adding a new status needs no
  database migration.
* **Login is required by default**: every blueprint is created with
  `protected_blueprint()`, so a forgotten decorator can't expose a page.
* **Optional select fields** (e.g. "no project") use `0` in the form;
  `none_if_zero()` converts that to `NULL`.
* **Lists that can grow** are paginated, and aggregates (task counts,
  workload) are calculated with a single grouped query instead of one per row.

### Data model (simplified)

```
User ──< Task >── Project ──< PurchaseOrder ──< PurchaseOrderLine ──< GoodsReceipt
  │                  │                                 │
  └──< Assignment >──┘                              Article
Project ──< Note >──< Tag            WikiPage ──< WikiRevision
                                     WikiPage ──< WikiPage (subpages)
```

* An order's received quantity = the sum of its `GoodsReceipt`s, which gives
  a full delivery history. `PurchaseOrder.refresh_status()` derives
  *Ordered / Partially delivered / Fully delivered* from the lines.
* `Assignment` covers a period with `hours_per_day`, so part-time planning
  (e.g. 4h project A + 4h project B) works. Leave, sick and training count
  as absence.
* On every wiki edit, the *previous* version is saved as a `WikiRevision`.

---

## Security notes for production

* Set a long random `SECRET_KEY` and strong database and admin passwords in `.env`.
* Put the application behind a reverse proxy with HTTPS (e.g. Traefik,
  nginx or Caddy), and don't expose PostgreSQL to the outside world (the
  compose file doesn't publish a DB port).
* Schedule regular `pg_dump` backups of the database.
