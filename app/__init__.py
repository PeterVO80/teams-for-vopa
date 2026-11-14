"""VOPA Teams - project management for industrial automation.

This module contains the *application factory* :func:`create_app`. Using a
factory (instead of a global ``app`` object) lets us create differently
configured instances, e.g. one for production and one per test.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Flask, render_template

from .config import Config
from .extensions import csrf, db, login_manager, migrate


def create_app(config_class: type[Config] = Config) -> Flask:
    """Create and configure a Flask application instance."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Bind the extensions to this application instance.
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (registers models for migrations)

    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    from .cli import register_cli

    register_cli(app)
    return app


def _register_blueprints(app: Flask) -> None:
    """Attach every module (blueprint) under its own URL prefix."""
    from .blueprints import (
        articles,
        auth,
        catalog,
        main,
        notes,
        projects,
        purchasing,
        relations,
        stock,
        tasks,
        team,
        users,
        wiki,
    )

    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp, url_prefix="/auth")
    app.register_blueprint(users.bp, url_prefix="/users")
    app.register_blueprint(projects.bp, url_prefix="/projects")
    app.register_blueprint(articles.bp, url_prefix="/articles")
    app.register_blueprint(catalog.bp, url_prefix="/articles/catalog")
    app.register_blueprint(purchasing.bp, url_prefix="/purchasing")
    app.register_blueprint(stock.bp, url_prefix="/stock")
    app.register_blueprint(relations.bp, url_prefix="/relations")
    app.register_blueprint(tasks.bp, url_prefix="/tasks")
    app.register_blueprint(team.bp, url_prefix="/team")
    app.register_blueprint(notes.bp, url_prefix="/notes")
    app.register_blueprint(wiki.bp, url_prefix="/wiki")


def _register_template_helpers(app: Flask) -> None:
    """Jinja filters and globals used by the templates."""
    from . import models
    from .access import visible_project_ids
    from .services.markdown_render import render_markdown

    @app.template_filter("date")
    def format_date(value: date | None, fmt: str = "%d/%m/%Y") -> str:
        """Belgian date notation, e.g. ``13/11/2026``."""
        return value.strftime(fmt) if value else ""

    @app.template_filter("datetime")
    def format_datetime(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
        return value.strftime(fmt) if value else ""

    @app.template_filter("money")
    def format_money(value: Decimal | float | None) -> str:
        """Format an amount as ``€ 1.234,50`` (Belgian notation)."""
        if value is None:
            return ""
        text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"€ {text}"

    @app.template_filter("price")
    def format_price(value: Decimal | float | None) -> str:
        """Unit price with 2 to 4 decimals: ``€ 385,00`` but ``€ 0,3422``."""
        if value is None:
            return ""
        text = f"{Decimal(str(value)):,.4f}".rstrip("0")
        whole, decimals = text.split(".")
        decimals = decimals.ljust(2, "0")
        return f"€ {whole.replace(',', '.')},{decimals}"

    @app.template_filter("text_on")
    def text_color_on(background: str | None) -> str:
        """Black or white text, whichever reads best on ``background`` (#rrggbb)."""
        try:
            r, g, b = (int((background or "#0d6efd")[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return "#fff"
        # Perceived brightness (ITU-R BT.601).
        return "#212529" if (r * 299 + g * 587 + b * 114) / 1000 > 150 else "#fff"

    @app.template_filter("qty")
    def format_quantity(value: Decimal | float | None) -> str:
        """Show ``5`` instead of ``5.00`` but keep real decimals (``2,5``)."""
        if value is None:
            return ""
        value = Decimal(str(value)).normalize()
        text = f"{value:f}"
        return text.replace(".", ",")

    app.jinja_env.filters["markdown"] = render_markdown
    # Usage: {{ page.content|wiki_markdown(page.project_id) }}
    app.jinja_env.filters["wiki_markdown"] = lambda text, project_id=None: render_markdown(
        text, wiki_links=True, wiki_project_id=project_id
    )

    # Make the status enums available in every template (for labels/colours).
    app.jinja_env.globals.update(
        TaskStatus=models.TaskStatus,
        TaskPriority=models.TaskPriority,
        OrderStatus=models.OrderStatus,
        ProjectStatus=models.ProjectStatus,
        AssignmentKind=models.AssignmentKind,
        today=date.today,
        visible_project_ids=visible_project_ids,
    )


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/error.html", code=403, message="Geen toegang."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/error.html", code=404, message="Pagina niet gevonden."), 404

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("errors/error.html", code=413, message="Bestand is te groot."), 413
