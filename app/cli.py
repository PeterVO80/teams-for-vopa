"""Custom ``flask`` command line commands.

Usage examples (inside the container: ``docker compose exec web flask ...``)::

    flask create-user jan --name "Jan Peeters" --admin
    flask ensure-admin            # used by the Docker entrypoint
    flask seed-demo               # fill an empty database with demo data
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import click
import sqlalchemy as sa
from flask import Flask

from .extensions import db
from .services.markdown_render import slugify
from .models import (
    Article,
    Assignment,
    Company,
    Contact,
    MovementKind,
    StockLocation,
    StockMovement,
    AssignmentKind,
    Note,
    OrderStatus,
    Project,
    PurchaseOrder,
    PurchaseOrderLine,
    Tag,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
    WikiPage,
)


def register_cli(app: Flask) -> None:
    """Register all commands on ``app.cli``."""

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--name", "full_name", prompt="Volledige naam")
    @click.option("--email", default=None)
    @click.option("--admin", is_flag=True, help="Geef beheerdersrechten.")
    @click.password_option()
    def create_user(username, full_name, email, admin, password):
        """Create a new user account."""
        if db.session.scalar(sa.select(User).filter_by(username=username)):
            raise click.ClickException(f"Gebruiker '{username}' bestaat al.")
        user = User(
            username=username,
            full_name=full_name,
            email=email,
            role=UserRole.ADMIN if admin else UserRole.MEMBER,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Gebruiker '{username}' aangemaakt.")

    @app.cli.command("reset-mfa")
    @click.argument("username")
    def reset_mfa(username):
        """Remove a user's two-step login (lost phone, also the last administrator).

        The user sets up the authenticator app again at the next login.
        In the container: docker exec -it <web container> flask reset-mfa <username>
        """
        from .services import mfa

        user = db.session.scalar(sa.select(User).filter_by(username=username))
        if user is None:
            raise click.ClickException(f"Gebruiker '{username}' bestaat niet.")
        mfa.reset(user)
        db.session.commit()
        click.echo(f"Tweestapsverificatie van '{username}' gereset: bij de volgende aanmelding opnieuw instellen.")

    @app.cli.command("ensure-admin")
    def ensure_admin():
        """Create the admin from ADMIN_* env vars when no users exist yet.

        Idempotent: it does nothing once at least one user exists, so it is
        safe to run on every container start.
        """
        if db.session.scalar(sa.select(sa.func.count(User.id))):
            click.echo("Gebruikers bestaan al - geen beheerder aangemaakt.")
            return
        password = os.environ.get("ADMIN_PASSWORD", "admin")
        if app.config.get("HTTPS") and len(password) < 12:
            # On the internet "admin/admin" would be found within minutes.
            raise click.ClickException("ADMIN_PASSWORD is te kort voor een server op internet (minstens 12 tekens).")
        user = User(
            username=os.environ.get("ADMIN_USERNAME", "admin"),
            full_name="Beheerder",
            email=os.environ.get("ADMIN_EMAIL"),
            role=UserRole.ADMIN,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Beheerder '{user.username}' aangemaakt.")

    @app.cli.command("outlook-sync")
    @click.option("--loop", "interval", type=int, default=0,
                  help="Blijf draaien en synchroniseer elke N seconden (0 = één keer).")
    def outlook_sync(interval):
        """Synchronise team planning <-> Outlook (Microsoft 365) calendars."""
        import time

        from .services import outlook

        while True:
            if outlook.is_configured():
                try:
                    report = outlook.sync_all()
                    click.echo(f"Outlook: {report.summary}")
                    for error in report.errors:
                        click.echo(f"  ! {error}")
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    db.session.rollback()
                    click.echo(f"Outlook: synchronisatie mislukt: {exc}")
            elif not interval:
                raise click.ClickException("Microsoft 365 is niet ingesteld (MS365_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET).")
            if not interval:
                break
            db.session.remove()  # fresh session each run
            time.sleep(interval)

    @app.cli.command("backup-create")
    @click.option("--loop", "interval", type=int, default=0,
                  help="Blijf draaien en maak elke N seconden een back-up (0 = één keer).")
    @click.option("--keep-days", type=int, default=None,
                  help="Verwijder automatische back-ups ouder dan zoveel dagen.")
    def backup_create(interval, keep_days):
        """Back up the complete database (as on the Back-ups page)."""
        import time

        from .services import backup

        keep_days = keep_days if keep_days is not None else app.config["BACKUP_KEEP_DAYS"]
        while True:
            try:
                info = backup.create_backup("auto" if interval else "manual", "cli")
                removed = backup.prune("auto", keep_days)
                click.echo(f"Back-up: {info.name} ({info.rows} records, {info.size // 1024} kB)"
                           + (f", {removed} oude verwijderd" if removed else ""))
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                db.session.rollback()
                click.echo(f"Back-up MISLUKT: {exc}")
                if not interval:
                    raise
            if not interval:
                break
            db.session.remove()
            time.sleep(interval)

    @app.cli.command("backup-before-upgrade")
    def backup_before_upgrade():
        """Back up the data before the database is migrated (Docker entrypoint).

        Runs only when migrations are pending, i.e. after an update of the
        app. If the back-up fails, the command fails and the container does
        not migrate the database - better not to start than to risk data.
        """
        from .services import backup

        if not backup.migrations_pending():
            click.echo("Geen database-update nodig: geen extra back-up.")
            return
        info = backup.create_backup("update", "update")
        click.echo(f"Back-up voor de update: {info.name} ({info.rows} records)")

    @app.cli.command("backup-restore")
    @click.argument("name")
    @click.confirmation_option(prompt="Alle huidige gegevens worden vervangen. Doorgaan?")
    def backup_restore(name):
        """Restore a back-up from BACKUP_DIR (emergency use; see the Back-ups page)."""
        from .services import backup

        try:
            safety = backup.restore_backup(backup.backup_path(name), "cli")
        except backup.BackupError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Teruggezet. De vorige gegevens staan in {safety.name}.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Fill an (almost) empty database with realistic demo data."""
        if db.session.scalar(sa.select(sa.func.count(Project.id))):
            raise click.ClickException("Er bestaan al projecten - demo data niet toegevoegd.")
        _seed_demo_data()
        click.echo("Demo data toegevoegd. Demo-gebruikers hebben wachtwoord 'demo1234'.")


def _seed_demo_data() -> None:
    """Create a small but representative data set for demos and testing."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    team = []
    for username, name, color in [
        ("jan", "Jan Peeters", "#0d6efd"),
        ("els", "Els Janssens", "#d63384"),
        ("tom", "Tom Maes", "#198754"),
        ("lies", "Lies Willems", "#fd7e14"),
    ]:
        user = db.session.scalar(sa.select(User).filter_by(username=username))
        if user is None:
            user = User(username=username, full_name=name, color=color)
            user.set_password("demo1234")
            db.session.add(user)
        team.append(user)

    # Relations: customers and suppliers.
    kroon = Company(name="Brouwerij De Kroon", is_customer=True, city="Leuven")
    kroon.contacts.append(Contact(last_name="Wouters", first_name="Bart", function="Productieleider"))
    aquafin = Company(name="Aquafin", is_customer=True, city="Aartselaar")
    rexel = Company(name="Rexel", is_supplier=True, website="www.rexel.be")
    cebeo = Company(name="Cebeo", is_supplier=True)
    ifm = Company(name="ifm", is_supplier=True)
    db.session.add_all([kroon, aquafin, rexel, cebeo, ifm])

    p1 = Project(name="Verpakkingslijn L3", customer=kroon,
                 start_date=monday - timedelta(days=30), end_date=monday + timedelta(days=60),
                 manager=team[0], description="Retrofit van de PLC-sturing van verpakkingslijn 3.")
    p2 = Project(name="Waterzuivering SCADA", customer=aquafin,
                 start_date=monday, end_date=monday + timedelta(days=90), manager=team[1])
    # Project teams: who sees which project (the manager always does).
    p1.members.extend([team[1], team[2]])   # Els, Tom   (Jan is manager)
    p2.members.extend([team[3]])            # Lies       (Els is manager)
    for project in (p1, p2):  # automatic codes: PRJ/<year>/0001, 0002
        project.assign_code()
        db.session.add(project)
        db.session.flush()

    articles = [
        Article(article_number="A-1001", description="Siemens S7-1200 CPU 1214C DC/DC/DC",
                manufacturer="Siemens", manufacturer_ref="6ES7214-1AG40-0XB0",
                supplier=rexel, category="PLC", unit_price=Decimal("385.00")),
        Article(article_number="A-1002", description="Siemens KTP700 Basic HMI",
                manufacturer="Siemens", manufacturer_ref="6AV2123-2GB03-0AX0",
                supplier=rexel, category="HMI", unit_price=Decimal("612.50")),
        Article(article_number="A-2001", description="Phoenix Contact klem UT 2,5 grijs",
                manufacturer="Phoenix Contact", manufacturer_ref="3044076",
                supplier=cebeo, category="Klemmen", unit_price=Decimal("0.95")),
        Article(article_number="A-3001", description="Inductieve sensor M12 PNP NO",
                manufacturer="ifm", manufacturer_ref="IFS204", supplier=ifm,
                category="Sensoren", unit_price=Decimal("48.20")),
        Article(article_number="A-4001", description="Kabel LiYY 4x0,75mm²", unit="m",
                supplier=cebeo, category="Kabel", unit_price=Decimal("1.15")),
    ]
    db.session.add_all(articles)

    # Stock: the migration creates "Magazijn"; the test database (create_all) does not.
    warehouse = db.session.scalar(sa.select(StockLocation).filter_by(name="Magazijn"))
    if warehouse is None:
        warehouse = StockLocation(name="Magazijn", description="Hoofdmagazijn")
        db.session.add(warehouse)
    van = StockLocation(name="Bestelwagen Jan")
    db.session.add(van)
    db.session.flush()
    articles[2].min_stock = Decimal(200)   # terminals: keep at least 200
    articles[3].min_stock = Decimal(5)     # sensors
    for article, location, qty in [(articles[2], warehouse, 150), (articles[2], van, 40),
                                   (articles[3], warehouse, 8), (articles[4], warehouse, 500)]:
        db.session.add(StockMovement(article=article, location=location, kind=MovementKind.RECEIPT,
                                     quantity=Decimal(qty), user=team[0], note="Beginstock"))

    order = PurchaseOrder(number=f"PO-{today:%Y}-0101", project=p1, supplier=rexel,
                          status=OrderStatus.ORDERED, order_date=today - timedelta(days=7),
                          expected_date=today + timedelta(days=3), created_by=team[0])
    for article, qty in [(articles[0], 2), (articles[1], 1)]:
        order.lines.append(PurchaseOrderLine(article=article, description=article.description,
                                             quantity=Decimal(qty), unit=article.unit,
                                             unit_price=article.unit_price))
    db.session.add(order)

    tasks = [
        ("I/O-lijst opmaken", p1, team[0], TaskStatus.DONE, TaskPriority.NORMAL),
        ("PLC-programma conveyor zone 2", p1, team[0], TaskStatus.IN_PROGRESS, TaskPriority.HIGH),
        ("HMI schermen tekenen", p1, team[1], TaskStatus.NEW, TaskPriority.NORMAL),
        ("FAT voorbereiden", p1, team[2], TaskStatus.NEW, TaskPriority.URGENT),
        ("Wachten op netwerkplan klant", p2, team[1], TaskStatus.ON_HOLD, TaskPriority.NORMAL),
        ("SCADA tags importeren", p2, team[3], TaskStatus.IN_PROGRESS, TaskPriority.NORMAL),
    ]
    for pos, (title, project, user, status, prio) in enumerate(tasks):
        db.session.add(Task(title=title, project=project, assignee=user, status=status,
                            priority=prio, position=pos, estimated_hours=8,
                            due_date=today + timedelta(days=pos * 3 - 2), created_by=team[0]))

    db.session.add_all([
        Assignment(user=team[0], project=p1, kind=AssignmentKind.PROJECT,
                   start_date=monday, end_date=monday + timedelta(days=11)),
        Assignment(user=team[1], project=p2, kind=AssignmentKind.PROJECT, hours_per_day=4,
                   start_date=monday, end_date=monday + timedelta(days=4)),
        Assignment(user=team[1], project=p1, kind=AssignmentKind.PROJECT, hours_per_day=4,
                   start_date=monday, end_date=monday + timedelta(days=4)),
        Assignment(user=team[2], kind=AssignmentKind.LEAVE,
                   start_date=monday + timedelta(days=2), end_date=monday + timedelta(days=8)),
        Assignment(user=team[3], project=p2, kind=AssignmentKind.PROJECT,
                   start_date=monday + timedelta(days=7), end_date=monday + timedelta(days=11)),
        # Weekend work: commissioning during the customer's production stop.
        Assignment(user=team[0], project=p1, kind=AssignmentKind.PROJECT, hours_per_day=6,
                   start_date=monday + timedelta(days=5), end_date=monday + timedelta(days=5),
                   include_weekends=True, note="Inbedrijfstelling tijdens productiestop"),
    ])

    status_tag, feedback_tag = Tag(name="Status meeting", color="#0d6efd"), Tag(name="Klant feedback", color="#fd7e14")
    db.session.add_all([status_tag, feedback_tag])
    db.session.add(Note(
        title="Kick-off verpakkingslijn L3", project=p1, author=team[0], tags=[status_tag],
        content="## Aanwezigen\n- Jan, Els (VOPA)\n- Klant: productieleider\n\n"
                "## Afspraken\n- [x] I/O-lijst tegen eind van de week\n- [ ] FAT plannen\n",
    ))
    db.session.add(Note(
        title="Feedback HMI-ontwerp", project=p1, author=team[1], tags=[feedback_tag],
        content="Klant wil **grotere knoppen** voor gebruik met handschoenen.",
    ))

    root = WikiPage(title="Siemens TIA Portal", slug="siemens-tia-portal",
                    content="Verzamelde kennis over TIA Portal.\n\nZie ook [[Naamgeving tags]].")
    db.session.add(root)
    db.session.add(WikiPage(
        title="Netwerkplan en IP-adressen", slug=f"{slugify(p1.code)}-netwerkplan-en-ip-adressen", project=p1,
        content="| Toestel | IP-adres |\n|---|---|\n| PLC conveyor | 192.168.10.10 |\n| HMI | 192.168.10.20 |\n\n"
                "Tag-afspraken: zie [[Naamgeving tags]].",
    ))
    db.session.add(WikiPage(
        title="Naamgeving tags", slug="naamgeving-tags", parent=root,
        content="| Prefix | Betekenis |\n|---|---|\n| `DI_` | Digitale ingang |\n| `DO_` | Digitale uitgang |\n",
    ))
    db.session.commit()
