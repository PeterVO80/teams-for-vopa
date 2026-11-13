"""Stock management: overview, movements, locations.

The logic (stock levels, validation of movements) lives in
``services/stock.py``; this module only handles the screens.
"""

from datetime import date

import sqlalchemy as sa
from flask import current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import StockLocationForm, StockMovementForm, none_if_zero
from ..models import Article, MovementKind, Project, StockLocation, StockMovement
from ..services import stock as svc
from ..utils import int_arg, is_modal_request
from . import protected_blueprint

bp = protected_blueprint("stock", __name__)


def _locations(active_only: bool = True) -> list[StockLocation]:
    query = sa.select(StockLocation).order_by(StockLocation.name)
    if active_only:
        query = query.where(StockLocation.active.is_(True))
    return db.session.scalars(query).all()


def _overview_filters() -> dict:
    return {
        "search": request.args.get("q", "").strip(),
        "location_id": int_arg("location"),
        "below_minimum": bool(request.args.get("below")),
        "category": request.args.get("category", ""),
    }


@bp.route("/")
def index():
    """Stock per article and location, with filters."""
    filters = _overview_filters()
    page = db.paginate(svc.overview_query(**filters), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    rows = svc.rows_for(page.items)
    categories = db.session.scalars(
        sa.select(Article.category).where(Article.category.is_not(None)).distinct().order_by(Article.category)
    ).all()
    return render_template(
        "stock/index.html", page=page, rows=rows, filters=filters, locations=_locations(),
        categories=categories, below_count=svc.below_minimum_count(),
    )


@bp.route("/export")
def export():
    filters = _overview_filters()
    articles = db.session.scalars(svc.overview_query(**filters)).all()
    return send_file(
        svc.export_overview(svc.rows_for(articles), _locations()),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"stock_{date.today():%Y%m%d}.xlsx",
    )


@bp.route("/movements")
def movements():
    """Full movement history, filterable per article, location, project and kind."""
    query = sa.select(StockMovement).order_by(StockMovement.moved_on.desc(), StockMovement.id.desc())
    article_id, location_id, project_id = int_arg("article"), int_arg("location"), int_arg("project")
    kind = request.args.get("kind", "")
    if article_id:
        query = query.where(StockMovement.article_id == article_id)
    if location_id:
        query = query.where(StockMovement.location_id == location_id)
    if project_id:
        query = query.where(StockMovement.project_id == project_id)
    if kind in MovementKind._value2member_map_:
        query = query.where(StockMovement.kind == MovementKind(kind))
    page = db.paginate(query, per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    return render_template(
        "stock/movements.html", page=page, locations=_locations(active_only=False),
        projects=Project.open_choices(include_empty=False),
        article=db.session.get(Article, article_id) if article_id else None,
        location_id=location_id, project_id=project_id, kind=kind,
    )


@bp.route("/move", methods=["GET", "POST"])
@bp.route("/move/<int:article_id>", methods=["GET", "POST"])
def move(article_id: int | None = None):
    """Popup to register a stock movement.

    ``/move/<article_id>`` is opened from an article (any article can be put
    in stock); ``/move`` lets you pick one of the articles that are already in
    the stock overview, e.g. from the project page (``?project=``).
    """
    article = db.get_or_404(Article, article_id) if article_id else None
    form = StockMovementForm()
    if article:
        form.article_id.choices = [(article.id, article.label)]
    else:
        stocked = db.session.scalars(svc.overview_query()).all()
        form.article_id.choices = [(0, "- kies een artikel -")] + [(a.id, a.label) for a in stocked]
    form.location_id.choices = StockLocation.choices()
    form.to_location_id.choices = StockLocation.choices(include_empty="-")
    form.project_id.choices = Project.open_choices()

    # Current stock per location, shown in the popup as a reference.
    current = svc.levels([article.id])[article.id] if article else {}

    if not form.is_submitted():
        form.article_id.data = article.id if article else 0
        form.kind.data = request.args.get("kind", "issue")
        form.project_id.data = int_arg("project") or 0
        # Default location: the one asked for, else where this article has the
        # most stock, else the first location.
        active_ids = [loc_id for loc_id, _ in form.location_id.choices]
        stocked = [loc_id for loc_id, qty in sorted(current.items(), key=lambda kv: -kv[1]) if loc_id in active_ids]
        form.location_id.data = int_arg("location") or (stocked or active_ids or [0])[0]
        form.moved_on.data = date.today()

    if form.validate_on_submit():
        chosen = db.session.get(Article, form.article_id.data)
        args = dict(user_id=current_user.id, moved_on=form.moved_on.data, note=form.note.data)
        try:
            if form.kind.data == "issue":
                svc.register_issue(chosen, form.location_id.data, form.quantity.data,
                                   project_id=none_if_zero(form.project_id.data), **args)
                message = f"{form.quantity.data.normalize():f} {chosen.unit} {chosen.article_number} uit stock genomen."
            elif form.kind.data == "receipt":
                svc.register_receipt(chosen, form.location_id.data, form.quantity.data,
                                     project_id=none_if_zero(form.project_id.data), **args)
                message = f"{form.quantity.data.normalize():f} {chosen.unit} {chosen.article_number} op stock gelegd."
            elif form.kind.data == "transfer":
                if not form.to_location_id.data:
                    raise svc.StockError("Kies de locatie waar het materiaal naartoe gaat.")
                svc.register_transfer(chosen, form.location_id.data, form.to_location_id.data, form.quantity.data, **args)
                message = f"{chosen.article_number} verplaatst."
            else:
                movement = svc.register_count(chosen, form.location_id.data, form.quantity.data, **args)
                message = (f"Inventaris {chosen.article_number}: verschil {movement.quantity.normalize():+f} geboekt."
                           if movement else f"Inventaris {chosen.article_number}: telling klopt, niets gewijzigd.")
        except svc.StockError as exc:
            form.quantity.errors.append(str(exc))
        else:
            db.session.commit()
            flash(message, "success")
            if is_modal_request():
                return jsonify(ok=True)
            return redirect(url_for("stock.index"))

    status = 422 if form.errors else 200
    return render_template("stock/_move_modal.html", form=form, article=article, current=current,
                           locations={loc.id: loc.name for loc in _locations(active_only=False)}), status


# ---------------------------------------------------------------- locations
@bp.route("/locations", methods=["GET", "POST"])
def locations():
    form = StockLocationForm()
    if form.validate_on_submit():
        if db.session.scalar(sa.select(StockLocation).where(sa.func.lower(StockLocation.name) == form.name.data.strip().lower())):
            form.name.errors.append("Deze locatie bestaat al.")
        else:
            db.session.add(StockLocation(name=form.name.data.strip(), description=form.description.data or None))
            db.session.commit()
            flash("Locatie toegevoegd.", "success")
            return redirect(url_for("stock.locations"))
    # Number of articles in stock per location.
    counts = dict(db.session.execute(
        sa.select(StockMovement.location_id, sa.func.count(sa.distinct(StockMovement.article_id)))
        .group_by(StockMovement.location_id)
    ).all())
    return render_template("stock/locations.html", form=form, locations=_locations(active_only=False), counts=counts)


@bp.route("/locations/<int:location_id>", methods=["POST"])
def update_location(location_id: int):
    location = db.get_or_404(StockLocation, location_id)
    form = StockLocationForm()
    if form.validate_on_submit():
        clash = db.session.scalar(sa.select(StockLocation).where(
            sa.func.lower(StockLocation.name) == form.name.data.strip().lower(), StockLocation.id != location.id))
        if clash:
            flash("Een andere locatie heeft al deze naam.", "danger")
        else:
            location.name = form.name.data.strip()
            location.description = form.description.data or None
            location.active = form.active.data
            db.session.commit()
            flash("Locatie bijgewerkt.", "success")
    return redirect(url_for("stock.locations"))
