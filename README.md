# VOPA Teams

Web application for running industrial automation projects. Every customer
assignment is a **project**. All modules hang off that project:

| Module | What it does |
|---|---|
| **Projects** | Automatic project code (`PRJ/2026/0001`, numbering restarts each year), project card with customer, project manager and timeline, plus an overview of the project's tasks, orders and notes. **Project team**: employees are linked to projects; non-admin employees only see the projects they are linked to (or manage) - everywhere in the app - and other projects' planning shows as "Ingepland". Administrators see everything. |
| **Relations** | Customers and suppliers (one company can be both) with address, VAT number and contact details. Projects, articles, purchase orders and supplier price lists choose a relation from a list instead of typing a name, so spelling mistakes can't create duplicates; a "+" next to the list creates a new relation in a popup. Relations in use can't be deleted, only deactivated. |
| **Purchasing** | Purchase orders per project/supplier. Order lines are **linked to the article database** (search on the order, "add to order" from the article list, or link a free-text line afterwards). **Delivery tracking per line** (partial deliveries, delivery notes, who received it); order status updates automatically. **Material overview per project**: per article, what is in draft, ordered, delivered and still outstanding across all the project's orders (project tab + page + Excel export). List of all outstanding deliveries, Excel export per order. **Import of Rexel orders**: upload the "Order Info" Excel file from the Rexel web shop (.xls or .xlsx); each Rexel order number becomes a purchase order (field *Leverancier ordernummer*, unique per supplier, so a file can't be imported twice), lines are linked to articles whose supplier reference equals the *Rexel productreferentie*, other lines become free text. |
| **Articles** | Article database with search and filters, order history per article. **Excel import/export** (round trip: export → edit in Excel → import). **Supplier catalogue**: load a supplier price list (Rexel DBF, hundreds of thousands of articles), filter by manufacturer / product group / search text, select articles across pages (or a whole filter) and import them; a new price list updates the prices of imported articles in one click. Prices with up to 4 decimals (cable per metre). During the Rexel import the **extended description** and the **specifications** are fetched from each article's Rexel Netstore product page (in batches with a progress bar); a new price list keeps that description. |
| **Stock** | Stock per article and location (warehouse, service vans, ...), minimum stock with an "under minimum" list and a reorder button. Movements in popups: receipt, issue (to a project), transfer, inventory count. Full movement history. Replenishment orders with a stock location put their deliveries in stock automatically; material issued to a project shows in the project's Material tab. Excel export. |
| **Punch list** | Kanban board with *New · In progress · On hold · Done · Cancelled*. Drag & drop, filters per project/team member, priority, deadline and estimate. |
| **Team overview** | Planning grid (people × days, weekends included) with load per day and period, absences (leave, sick, training), and open tasks per person. Plan by clicking a day or click-and-dragging over several days; the form opens in a popup. Weekend days are only planned when "Weekenddagen inplannen" is ticked. Optionally an entry is planned **per hour** ("Van (uur)" / "Tot (uur)", e.g. 08:30 - 12:00 every planned day); the hours per day are then calculated, the break excluded. The working day is 08:30 - 17:00 with a break 12:00 - 12:30 (`WORKDAY_START`, `WORKDAY_END`, `BREAK_START`, `BREAK_END`). **Outlook link** (Microsoft 365): appointments from each employee's Outlook calendar appear in the planning (short ones as a calendar icon with tooltip, all-day/multi-day ones as an outlined bar; "out of office" counts as absent), and every planning entry is written to the employee's Outlook calendar as an appointment in the working hours (category "VOPA Teams"). See [Outlook (Microsoft 365)](#outlook-microsoft-365). |
| **Notes** | Markdown notes with a WYSIWYG editor, **tags** (e.g. *Status meeting*, *Customer feedback*), optional project link, search and pinning. |
| **Wiki** | A general wiki plus **a wiki per project**. Hierarchical Markdown pages with WYSIWYG editor, `[[Page title]]` links (looked up in the same wiki first), search and **full version history** (view and restore). |

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

### Deploying with Portainer

Use `portainer-stack.yml` for this. Portainer clones the repository and builds
the image on the Docker host itself.

1. **Stacks → Add stack**, give it a name (e.g. `vopa-teams`), then choose **Repository**.
2. **Repository URL**: `https://github.com/petervo80/teams-for-vopa`.
   **Repository reference**: `refs/heads/main` (or the branch you want to run).
   **Compose path**: `portainer-stack.yml`.
3. For a private repository, turn on **Authentication**: your GitHub username
   plus a *Personal Access Token* with read access to the repository.
4. Under **Environment variables**, add at least:
   `SECRET_KEY`, `POSTGRES_PASSWORD` and `ADMIN_PASSWORD`. Optional:
   `WEB_PORT` (default 8080; Portainer itself usually occupies port 8000), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `POSTGRES_USER`,
   `POSTGRES_DB`, `WORKDAY_HOURS`, `PROJECT_CODE_PREFIX`.
5. **Deploy the stack**. After about 30 seconds the `web` container shows as
   *healthy* and the app is available at `http://<docker-host>:<WEB_PORT>`.

* **Updating**: open the stack, click **Pull and redeploy**, and turn on
  *Re-pull image and redeploy*. Migrations run automatically. You can also
  turn on *GitOps updates* so Portainer polls the repository itself.
* **Demo data or users**: *Containers → vopa-teams-web-1 → Console* (`/bin/sh`),
  then for example `flask seed-demo` or `flask create-user jan --name "Jan Peeters"`.
* **Data** lives in the `<stackname>_vopa_pgdata` volume. Deleting the stack
  does not delete that volume, unless you remove it yourself under *Volumes*.
* Don't change `POSTGRES_PASSWORD` after the first deployment: PostgreSQL only
  sets that password when the database is first created.
* The *Web editor* method doesn't work here, because it can't build an image
  from the source code. Use *Repository*.

### Outlook (Microsoft 365)

The team planning and the employees' Outlook calendars are synchronised both
ways (`app/services/outlook.py`):

* **Outlook → planning**: every 5 minutes the appointments of the last 30 and
  next 183 days are read. Appointments marked *free*, cancelled ones and the
  ones this application wrote itself are skipped. Private appointments show
  as "Privé".
* **Planning → Outlook**: each planning entry becomes an appointment in the
  working hours (or in the hours planned for it) on every planned day - a
  recurring series for more than one day, on weekdays only unless the
  weekends are planned - with category **VOPA Teams**; absences are marked *out of office*. A change in the
  planning is written within seconds; changing or deleting the planning
  updates or removes the appointment. Change the planning in VOPA Teams, not
  in Outlook: changes made in Outlook to these appointments are overwritten.

The link uses an app registration with application permissions, so nobody has
to log in with Microsoft. A Microsoft 365 administrator sets it up once:

1. <https://entra.microsoft.com> → *Applications* → *App registrations* →
   *New registration*. Name e.g. "VOPA Teams", *single tenant*, no redirect URI.
2. In the new app: *API permissions* → *Add a permission* → *Microsoft Graph*
   → *Application permissions* → **Calendars.ReadWrite** → *Add*. Optionally
   also **MailboxSettings.ReadWrite**, so the app can give the "VOPA Teams"
   category its colour in everyone's Outlook (without it everything works,
   the category just keeps Outlook's default colour). Then click
   **Grant admin consent for <your company>**.
3. *Certificates & secrets* → *New client secret* (e.g. 24 months). Copy the
   **Value** immediately (it is shown only once).
4. Note the **Directory (tenant) ID** and **Application (client) ID** on the
   app's *Overview* page.
5. Set the environment variables (Portainer: stack → *Environment variables*,
   then *Update the stack*):

   | Variable | Value |
   |---|---|
   | `MS365_TENANT_ID` | Directory (tenant) ID |
   | `MS365_CLIENT_ID` | Application (client) ID |
   | `MS365_CLIENT_SECRET` | the secret's value |
   | `MS365_TIMEZONE` | optional, Windows time zone (default `Romance Standard Time` = Brussels) |
   | `MS365_CATEGORY_COLOR` | optional, default colour of the "VOPA Teams" category (the colour is normally chosen in the app on the *Outlook-koppeling* page, which overrides this): `rood`, `oranje` (default), `bruin`, `geel`, `groen`, `blauwgroen`, `olijf`, `blauw`, `paars`, `cranberry`, `grijs`, `zwart`, `donkerrood`, `donkeroranje`, `donkergroen`, `donkerblauw`, `donkerpaars` or an Outlook preset (`preset0`-`preset24`); empty = leave the colour alone |
   | `APP_URL` | optional, address of the app (e.g. `http://teams.vopa.local:8080`), used as a link in the appointments |

6. Give every employee their **Microsoft 365 e-mail address** in
   *Gebruikersbeheer*: that is the mailbox that is synchronised. Per employee
   the link can be switched off ("Outlook-agenda koppelen").
7. Check *Admin menu → Outlook-koppeling*: it shows per employee whether the
   link works (and the error otherwise) and has a **Synchroniseer nu** button.

The stack's `outlook-sync` container runs `flask outlook-sync --loop 300`; it
idles while the variables are not set. Run once by hand with
`docker compose exec web flask outlook-sync`.

*Optional, stricter*: by default `Calendars.ReadWrite` gives the app access to
all mailboxes of the organisation. To limit it to the employees' mailboxes, an
Exchange administrator can add an [application access policy](https://learn.microsoft.com/graph/auth-limit-mailbox-access)
(`New-ApplicationAccessPolicy -AccessRight RestrictAccess -AppId <client id> -PolicyScopeGroupId <mail-enabled security group>`).

### Log in with Microsoft 365

With `MICROSOFT_LOGIN` the employees log in with their Microsoft 365 account
(`app/services/microsoft_login.py`, OpenID Connect with PKCE). The company's
Microsoft security then also protects this app: two-step verification
(Authenticator), conditional access, and someone whose Microsoft account is
disabled can no longer log in.

| `MICROSOFT_LOGIN` | Login page |
|---|---|
| `off` (default) | user name + password |
| `on` | "Aanmelden met Microsoft" button, passwords still work - use this to try it out |
| `required` | only Microsoft. Administrators keep their password as an emergency entrance (e.g. Microsoft outage) - give them a long password |

Set-up (same app registration as the Outlook link):

1. Entra → your app → *Authentication* → *Add a platform* → **Web**.
   Redirect URI: `https://<DOMAIN>/auth/microsoft/callback`
   (Microsoft only accepts `https://`, except for `http://localhost`).
   Leave "Access tokens" and "ID tokens" unticked (not needed).
2. *API permissions*: the delegated **User.Read** that is there by default is
   enough (`openid`, `profile` and `email` are included).
3. Set `MICROSOFT_LOGIN=on` and `APP_URL=https://<DOMAIN>`, redeploy, try it,
   then switch to `required`.
4. In *Gebruikersbeheer* every employee needs their Microsoft 365 e-mail
   address. At the first Microsoft login the account is linked by e-mail
   address; afterwards by the Microsoft account's id. People without a user
   in VOPA Teams get no access: an administrator creates them first (with
   Microsoft login on, the password field may stay empty). Changing a
   user's e-mail address unlinks the Microsoft account.

Microsoft logins are not remembered beyond the browser session: after
closing the browser the user clicks the button again (usually without typing
anything, Microsoft still knows them).

### Access per module

Administrators can do everything. For other users the administrator ticks,
per module (Projecten, Punchlijst, Team, Uren, Bestellingen, Artikelen &
catalogi, Stock, Relaties, Notities, Wiki), **Lezen** (view) and/or
**Schrijven** (also add, change and delete; includes read) on the user form
(*Gebruikers > Bewerken*).

* No access: the module is not in the menu, its tabs on the project page and
  its dashboard tiles are hidden, its pages answer 403.
* Read only: everything can be viewed (a task or article opens read-only);
  creating, changing, deleting, importing and dragging on the boards is
  refused by the server and the buttons are hidden (static/js/read_only.js).
* Users that existed before this feature, and new users, start with write
  access everywhere.

Which projects someone sees is a separate rule (members and the project
manager, see `app/access.py`). Code: `app/modules.py`, `app/permissions.py`.

### Contact persons

*Relaties > Contactpersonen*: people (name, first name, function, e-mail,
phone, GSM, notes) linked to a customer or supplier. The relation page lists
its contact persons and adds / edits them in a popup. Inactive contact persons
(no longer working there) stay for the history.

### Quotes (offertes)

Menu **Offertes**: a quote (number OFF-2026-0001, automatic) for a customer,
optionally linked to a project, with lines in a fixed order:

* **Sectie** - a title; the lines below it form a section with a subtotal;
* **Materiaal** - an article from the article database (search box) or free
  text: quantity x sales price. The article's price is the purchase price; the
  sales price = purchase price + markup (or typed by hand). Purchase price and
  margin are internal;
* **Werkuren** - a kind of service (Bespreking met klant / functionele
  beschrijving, PLC & HMI engineer, Technieker bordenbouw, ...) x hours; the
  service's hourly rate is suggested. Services and rates: *Instellingen*.

A **sent or accepted** quote is locked: only its status can change (back to
*Concept* unlocks it), or make a copy for a new version.

Template texts for the accompanying text (*Instellingen*): choose one on the
quote form; it is copied into the quote, where it can be changed or extended.

Per section you choose what the customer sees on the PDF: all details,
only quantities (no prices) or only the subtotal.

Every line can have a note. Lines move up/down; "+ materiaal / + werkuren" on
a section title adds a line at the end of that section. Totals: material,
labour (hours), excl. VAT, VAT, incl. VAT. **Project aanmaken** creates a
project from the quote (name, customer, introduction) and links it; the
project page has an *Offertes* tab. **PDF** gives the quote for the customer
(letter header with logo and addresses, sections, notes, subtotals, totals,
"voor akkoord" block) - without purchase prices, margins or internal notes.
Default hourly rate and markup, and our VAT number / phone / e-mail for the
PDFs: admin menu > *Instellingen*. Access: module *Offertes* (not given automatically to
existing non-admin users, because quotes show purchase prices and margins).

### Time registration and distances to customers

Everybody registers hours under **Uren** (menu) or with *Uren registreren* on a
project: date, project, hours, a description and - for work at the customer -
*Buitendienst* with the kilometres driven. With field service ticked the
customer of the project is filled in and the kilometres become twice the
customer's distance from the office (there and back); both can be changed.

* **Office address**: admin menu > *Instellingen* (company name and address,
  also printed on the timesheet).
* **Distance of a relation** (one way, field *Afstand vanaf kantoor*): calculated
  when a relation with an address is saved, or with *Bereken afstand*. A value
  typed by hand is kept. Without configuration OpenStreetMap is used (free,
  Nominatim + OSRM). Set `GOOGLE_MAPS_API_KEY` (Distance Matrix API enabled) to
  use Google Maps instead.
* **Timesheet (urenstaat)**: *Uren > Urenstaat* or the button on a project. It
  shows the hours per day per employee for a period - by default the previous
  month - and downloads as PDF (ReportLab).

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
│   ├── material.py        #   ordered vs. delivered per project and article
│   ├── numbering.py       #   automatic numbers (PRJ/2026/0001, PO-2026-0001)
│   ├── supplier_catalog.py #  supplier price lists (Rexel DBF): load, filter, import
│   ├── stock.py           #   stock levels and stock movements
│   ├── rexel_details.py   #   extended description + specifications from the Rexel product page
│   ├── markdown_render.py #   Markdown → safe HTML, [[wiki links]]
│   └── workload.py        #   team overview calculations
├── blueprints/            # one blueprint (URL prefix) per module
│   ├── __init__.py        #   protected_blueprint(): login required by default
│   ├── auth.py main.py users.py projects.py articles.py
│   ├── purchasing.py tasks.py team.py notes.py wiki.py
├── templates/             # Jinja templates per module, base.html + _macros.html
└── static/
    ├── css/app.css
    ├── js/                # app.js, modal_form.js, kanban.js, editor.js, team.js, wiki.js, article_picker.js
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
  as absence. Saturdays and Sundays in the period only count when
  `include_weekends` is set; weekend hours come on top of the normal
  capacity.
* Supplier price lists are loaded into `SupplierCatalog` / `CatalogItem`
  (not directly into `Article`). An imported article remembers its supplier
  and `supplier_ref`, so a newer price list finds it again for price updates.
  Another supplier's file format can be added in
  `services/supplier_catalog.py` (a `CatalogFormat` + an entry in `FORMATS`).
* Access to projects is decided in one place, `app/access.py`: admins see
  everything, other employees only projects where they are a member
  (`project_members`) or the manager. Lists use `project_scope()` /
  `visible_projects_condition()`, single records `require_project()` (404).
  Being planned on, getting a task in, or creating a project links the
  person automatically. Migration 0007 gave existing projects a team based
  on their tasks and planning.
* Customers and suppliers are `Company` records (`models/company.py`);
  `Project.customer`, `Article.supplier`, `PurchaseOrder.supplier` and
  `SupplierCatalog.supplier` are relationships to it. Migration 0005 turned
  the former free-text names into companies (names differing only in
  capitals/spaces were merged). The Excel article import only accepts
  supplier names that exist as a supplier.
* Stock is never stored as an editable number: every change is a
  `StockMovement` (+ in, − out) and stock = the sum of the movements. That
  gives a complete audit trail; mistakes are fixed with a count or
  correction movement. A delivery on an order with a `stock_location`
  creates a movement linked to the `GoodsReceipt`, so undoing the delivery
  undoes the stock change too.
* `WikiPage.project_id` is NULL for the general wiki, or points to the
  project whose wiki the page belongs to. Sub-pages are always in the same
  wiki as their parent.
* On every wiki edit, the *previous* version is saved as a `WikiRevision`.

---

## Running on the internet (e.g. Hetzner Cloud)

`portainer-stack.yml` is used both for a test server on the local network
and for a server on the internet: [Caddy](https://caddyserver.com) in front,
no other open ports, and a daily database backup. Set **`DOMAIN`** (e.g.
`teams.vopa.be`) and Caddy gets a Let's Encrypt certificate for it
automatically, redirects http to https, and the app runs in "HTTPS mode".
Without `DOMAIN` it is plain http on `WEB_PORT` (test server).

`DOMAIN` and `APP_URL`: `DOMAIN` is the bare name Caddy needs for the
certificate; `APP_URL` (the full address, used in links in Outlook
appointments and for the Microsoft login) defaults to `https://<DOMAIN>`,
so it only needs to be set without a domain, e.g. `http://192.168.1.10:8080`.

### What the application does in HTTPS mode (automatic with `DOMAIN`)

* Cookies (session, "remember me") are only sent over HTTPS, are not
  readable by JavaScript and are `SameSite=Lax`; sessions expire after 12
  hours, "Aangemeld blijven" after 14 days.
* Refuses to start without a strong `SECRET_KEY` (at least 32 characters).
* Trusts the `X-Forwarded-*` headers of exactly one proxy (Caddy), so the
  real client address is known.
* Security headers on every page (also without HTTPS mode): a Content
  Security Policy that only runs the app's own scripts, no framing
  (clickjacking), `nosniff`, no caching of pages, HSTS.
* Login protection (always): after 5 wrong passwords for a user name, or 20
  from one IP address, logging in is refused for 15 minutes. Failed logins
  are logged. New passwords must have at least 10 characters.
* Already present: CSRF protection on every form and AJAX call, passwords
  stored as salted hashes, Markdown output sanitised (XSS), access control per
  project, PostgreSQL not reachable from outside, the app runs as a non-root
  user in its container.

### Setting up the server (once)

1. **DNS**: create an `A` record (and `AAAA` for IPv6) for e.g.
   `teams.vopa.be` pointing to the server's IP address.
2. **Hetzner Cloud Firewall** (Cloud Console → *Firewalls*), attached to the
   server. Inbound only:
   * TCP 22 (SSH) - **only from your own office/home IP address**;
   * TCP 80 and TCP 443, UDP 443 - from anywhere;
   * nothing else (in particular not Portainer's 9443/9000 and not 5432).

   Use the Hetzner firewall rather than `ufw`: Docker publishes ports past
   `ufw`'s rules.
3. **SSH**: log in with an SSH key only. In `/etc/ssh/sshd_config` set
   `PasswordAuthentication no` and `PermitRootLogin prohibit-password`
   (or create a normal user with `sudo` and use `PermitRootLogin no`), then
   `systemctl restart ssh`. Optionally `apt install fail2ban`.
4. **Automatic security updates**:
   `apt install unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades`.
5. **Backups**: enable *Backups* for the server in the Hetzner Cloud Console
   (daily server images, 7 kept) and/or copy the database dumps off the
   server (see below).

### Starting the application

```bash
git clone https://github.com/petervo80/teams-for-vopa.git /opt/vopa-teams
cd /opt/vopa-teams
cp .env.example .env
nano .env        # DOMAIN, SECRET_KEY, POSTGRES_PASSWORD, ADMIN_PASSWORD, MS365_*
chmod 600 .env
docker compose -f portainer-stack.yml up -d --build
docker compose -f portainer-stack.yml logs -f caddy web   # certificate + start-up
```

Generate the secrets with `openssl rand -hex 32`. Log in at
`https://<DOMAIN>`, **change the admin password immediately** and give every
employee their own account with a strong password.

Updating to a new version:

```bash
cd /opt/vopa-teams && git pull
docker compose -f portainer-stack.yml up -d --build
```

With **Portainer** instead: deploy the stack from the repository with
compose path `portainer-stack.yml` and the variables above. Don't
expose Portainer itself to the internet: keep its port closed in the firewall
and reach it through an SSH tunnel
(`ssh -L 9443:localhost:9443 user@server`, then `https://localhost:9443`).

### Backups

*Admin menu → Back-ups* (`app/services/backup.py`):

* **Back-up maken**: a ZIP file with all data of the application (every
  table; there are no files outside the database). Download it to keep a copy
  elsewhere.
* **Terugzetten**: replaces *all* data by a back-up (type `HERSTEL` to
  confirm). A safety back-up of the current data is made first, so a restore
  can itself be undone. A back-up of an older version of the app is migrated
  automatically; one of a newer version is refused.
* **Opladen**: upload a back-up, e.g. from another server, then restore it.
* The `backup` container makes one automatically every day and deletes
  automatic back-ups older than `BACKUP_KEEP_DAYS` (default 14).
* **Before every update** of the database structure (migrations) the web
  container makes a back-up first (`flask backup-before-upgrade`). If that
  back-up fails, the container stops without touching the database.
* Administrators see a warning on the dashboard (and on the Back-ups page)
  when the newest back-up is older than 26 hours, i.e. the automatic back-ups
  stopped.

Back-ups are stored in the volume `vopa_app_backups` (`/app/backups`). They
contain everything, including password hashes: store downloads safely, and
copy one off the server regularly (a back-up on the same server doesn't
survive losing the server). Command-line equivalents:

```bash
docker compose -f portainer-stack.yml exec web flask backup-create
docker compose -f portainer-stack.yml exec web flask backup-restore vopa-teams-2026-11-15_031500-auto.zip
```

### Moving to another server (e.g. from the local Portainer to Hetzner)

Easiest: on the old server *Back-ups → Back-up maken → download*, on the new
server *Back-ups → Opladen → Terugzetten*. Without the app (or for a
version from before the Back-ups page) use a database dump:

All data (projects, tasks, orders, articles, stock, notes, wiki, planning,
users and their passwords, settings, the Outlook links) is in the PostgreSQL
database; nothing is stored in files. Moving = one database dump.

1. **Stop the old application** (Portainer: stop the containers `web` and
   `outlook-sync` of the stack, keep `db` running). Otherwise people keep
   working in the old copy, and two `outlook-sync` containers would write to
   the same Outlook calendars.
2. **Dump** on the old Docker host (find the database container's name with
   `docker ps`, e.g. `vopa-teams-db-1`):

   ```bash
   docker exec vopa-teams-db-1 pg_dump -U vopa -Fc --no-owner vopa > vopa-teams.dump
   ```
3. **Copy** it to the new server: `scp vopa-teams.dump root@<server>:/opt/vopa-teams/`
4. **Restore** on the new server (start only the database, restore, then the rest):

   ```bash
   cd /opt/vopa-teams
   docker compose -f portainer-stack.yml up -d db
   docker compose -f portainer-stack.yml exec -T db pg_restore -U vopa -d vopa --clean --if-exists --no-owner < vopa-teams.dump
   docker compose -f portainer-stack.yml up -d --build
   ```

   The web container then upgrades the database to the current version
   (migrations). Restoring over a database that the new app already
   initialised works too (`--clean`). Everybody logs in with their old
   password; `ADMIN_PASSWORD` of the new server is not used because users
   already exist.
5. Check the data, then remove the old stack (keep the dump for a while).

### Security notes for the local (Portainer) set-up

* Set a long random `SECRET_KEY` and strong database and admin passwords.
* Behind your own reverse proxy, allow large uploads for supplier price lists
  (e.g. nginx `client_max_body_size 300m;` and a read timeout of a few
  minutes). The app itself accepts `MAX_UPLOAD_MB` (default 300).
