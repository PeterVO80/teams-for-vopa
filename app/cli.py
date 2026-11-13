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
from .models import (
    Article,
    Assignment,
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

    @app.cli.command("ensure-admin")
    def ensure_admin():
        """Create the admin from ADMIN_* env vars when no users exist yet.

        Idempotent: it does nothing once at least one user exists, so it is
        safe to run on every container start.
        """
        if db.session.scalar(sa.select(sa.func.count(User.id))):
            click.echo("Gebruikers bestaan al - geen beheerder aangemaakt.")
            return
        user = User(
            username=os.environ.get("ADMIN_USERNAME", "admin"),
            full_name="Beheerder",
            email=os.environ.get("ADMIN_EMAIL"),
            role=UserRole.ADMIN,
        )
        user.set_password(os.environ.get("ADMIN_PASSWORD", "admin"))
        db.session.add(user)
        db.session.commit()
        click.echo(f"Beheerder '{user.username}' aangemaakt.")

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

    p1 = Project(code="P2026-014", name="Verpakkingslijn L3", customer="Brouwerij De Kroon",
                 start_date=monday - timedelta(days=30), end_date=monday + timedelta(days=60),
                 manager=team[0], description="Retrofit van de PLC-sturing van verpakkingslijn 3.")
    p2 = Project(code="P2026-021", name="Waterzuivering SCADA", customer="Aquafin",
                 start_date=monday, end_date=monday + timedelta(days=90), manager=team[1])
    db.session.add_all([p1, p2])

    articles = [
        Article(article_number="A-1001", description="Siemens S7-1200 CPU 1214C DC/DC/DC",
                manufacturer="Siemens", manufacturer_ref="6ES7214-1AG40-0XB0",
                supplier="Rexel", category="PLC", unit_price=Decimal("385.00")),
        Article(article_number="A-1002", description="Siemens KTP700 Basic HMI",
                manufacturer="Siemens", manufacturer_ref="6AV2123-2GB03-0AX0",
                supplier="Rexel", category="HMI", unit_price=Decimal("612.50")),
        Article(article_number="A-2001", description="Phoenix Contact klem UT 2,5 grijs",
                manufacturer="Phoenix Contact", manufacturer_ref="3044076",
                supplier="Cebeo", category="Klemmen", unit_price=Decimal("0.95")),
        Article(article_number="A-3001", description="Inductieve sensor M12 PNP NO",
                manufacturer="ifm", manufacturer_ref="IFS204", supplier="ifm",
                category="Sensoren", unit_price=Decimal("48.20")),
        Article(article_number="A-4001", description="Kabel LiYY 4x0,75mm²", unit="m",
                supplier="Cebeo", category="Kabel", unit_price=Decimal("1.15")),
    ]
    db.session.add_all(articles)

    order = PurchaseOrder(number="PO-2026-0101", project=p1, supplier="Rexel",
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
        title="Naamgeving tags", slug="naamgeving-tags", parent=root,
        content="| Prefix | Betekenis |\n|---|---|\n| `DI_` | Digitale ingang |\n| `DO_` | Digitale uitgang |\n",
    ))
    db.session.commit()
