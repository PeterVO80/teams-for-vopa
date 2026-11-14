"""Article database with Excel import/export."""

from datetime import date

import sqlalchemy as sa
from flask import current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from ..extensions import db
from ..forms import ArticleForm, ImportForm, none_if_zero
from ..models import Article, Company, PurchaseOrder, PurchaseOrderLine, StockLocation, StockMovement
from ..services import rexel_details
from ..services import stock as stock_svc
from ..services import excel
from . import protected_blueprint
from .relations import add_button as add_relation_button

bp = protected_blueprint("articles", __name__)

XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filtered_query() -> sa.Select:
    """Build the article query from the search/filter query parameters.

    Shared by the list view and the export so that "export" always exports
    exactly what the user is looking at.
    """
    query = sa.select(Article).order_by(Article.article_number)
    search = request.args.get("q", "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.where(
            sa.or_(
                Article.article_number.ilike(pattern),
                Article.description.ilike(pattern),
                Article.manufacturer.ilike(pattern),
                Article.manufacturer_ref.ilike(pattern),
                Article.supplier.has(Company.name.ilike(pattern)),
                Article.supplier_ref.ilike(pattern),
                Article.ean == search,
            )
        )
    category = request.args.get("category", "").strip()
    if category:
        query = query.where(Article.category == category)
    if not request.args.get("inactive"):
        query = query.where(Article.active.is_(True))
    return query


@bp.route("/")
def index():
    page = db.paginate(
        _filtered_query(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    categories = db.session.scalars(
        sa.select(Article.category).where(Article.category.is_not(None)).distinct().order_by(Article.category)
    ).all()
    return render_template("articles/index.html", page=page, categories=categories)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:article_id>/edit", methods=["GET", "POST"])
def edit(article_id: int | None = None):
    article = db.get_or_404(Article, article_id) if article_id else None
    form = ArticleForm(obj=article)
    form.supplier_id.choices = Company.choices("supplier", article.supplier_id if article else None, empty="- geen leverancier -")
    add_relation_button(form.supplier_id, "supplier")
    if article and not form.is_submitted():
        form.supplier_id.data = article.supplier_id or 0
    if form.validate_on_submit():
        duplicate = db.session.scalar(
            sa.select(Article).where(
                Article.article_number == form.article_number.data.strip(),
                Article.id != (article_id or 0),
            )
        )
        if duplicate:
            form.article_number.errors.append("Dit artikelnummer bestaat al.")
        else:
            if article is None:
                article = Article()
                db.session.add(article)
            form.populate_obj(article)
            article.supplier_id = none_if_zero(form.supplier_id.data)
            article.description = " ".join(article.description.split())  # one line, no double spaces
            article.article_number = article.article_number.strip()
            db.session.commit()
            flash(f"Artikel {article.article_number} opgeslagen.", "success")
            return redirect(url_for("articles.index"))
    # Where has this article been ordered? (newest first)
    order_lines = []
    if article:
        order_lines = db.session.scalars(
            sa.select(PurchaseOrderLine)
            .join(PurchaseOrder)
            .where(PurchaseOrderLine.article_id == article.id)
            .order_by(PurchaseOrder.id.desc())
            .limit(100)
        ).all()
    # Stock per location and the latest movements.
    stock, movements = {}, []
    if article:
        stock = stock_svc.levels([article.id])[article.id]
        movements = db.session.scalars(
            sa.select(StockMovement).where(StockMovement.article_id == article.id)
            .order_by(StockMovement.moved_on.desc(), StockMovement.id.desc()).limit(15)
        ).all()
    return render_template(
        "articles/edit.html",
        form=form,
        article=article,
        order_lines=order_lines,
        stock=stock,
        movements=movements,
        locations={loc.id: loc.name for loc in db.session.scalars(sa.select(StockLocation))},
        title="Artikel bewerken" if article else "Nieuw artikel",
        cancel_url=url_for("articles.index"),
    )


@bp.route("/<int:article_id>/delete", methods=["POST"])
def delete(article_id: int):
    """Delete an article, or deactivate it when it is used on an order."""
    article = db.get_or_404(Article, article_id)
    in_use = db.session.scalar(sa.select(sa.func.count(PurchaseOrderLine.id)).filter_by(article_id=article.id))
    if in_use:
        article.active = False
        flash("Artikel wordt gebruikt in bestellingen en werd daarom op inactief gezet.", "warning")
    else:
        db.session.delete(article)
        flash("Artikel verwijderd.", "success")
    db.session.commit()
    return redirect(url_for("articles.index"))


# ------------------------------------------------------------ Excel I/O
@bp.route("/export")
def export():
    """Download the (filtered) article list as an Excel file."""
    articles = db.session.scalars(_filtered_query()).all()
    return send_file(
        excel.export_articles(articles),
        mimetype=XLSX_MIMETYPE,
        as_attachment=True,
        download_name=f"artikelen_{date.today():%Y%m%d}.xlsx",
    )


@bp.route("/import/template")
def import_template():
    return send_file(
        excel.import_template(),
        mimetype=XLSX_MIMETYPE,
        as_attachment=True,
        download_name="artikelen_importsjabloon.xlsx",
    )


@bp.route("/import", methods=["GET", "POST"])
def import_view():
    form = ImportForm()
    result = None
    if form.validate_on_submit():
        result = excel.import_articles(form.file.data.stream)
        if result.created or result.updated:
            db.session.commit()
            flash(
                f"Import klaar: {result.created} nieuw, {result.updated} bijgewerkt, "
                f"{result.skipped} overgeslagen.",
                "success" if not result.errors else "warning",
            )
        else:
            db.session.rollback()
            flash("Er werd niets geïmporteerd.", "warning")
    return render_template("articles/import.html", form=form, result=result, columns=excel.ARTICLE_COLUMNS)


# ------------------------------------------------------------- JSON API
@bp.route("/search.json")
def search_json():
    """Autocomplete endpoint used when adding lines to a purchase order."""
    term = request.args.get("q", "").strip()
    if len(term) < 2:
        return jsonify([])
    pattern = f"%{term}%"
    articles = db.session.scalars(
        sa.select(Article)
        .where(
            Article.active.is_(True),
            sa.or_(
                Article.article_number.ilike(pattern),
                Article.description.ilike(pattern),
                Article.manufacturer_ref.ilike(pattern),
                Article.supplier_ref.ilike(pattern),
                Article.ean == term,
            ),
        )
        .order_by(Article.article_number)
        .limit(20)
    )
    return jsonify(
        [
            {
                "id": a.id,
                "label": a.label,
                "description": a.description,
                "unit": a.unit,
                "unit_price": float(a.unit_price) if a.unit_price is not None else None,
                "supplier": a.supplier.name if a.supplier else None,
            }
            for a in articles
        ]
    )


@bp.route("/<int:article_id>/details", methods=["POST"])
def fetch_details(article_id: int):
    """Fetch the extended description and specifications from the Rexel page now."""
    article = db.get_or_404(Article, article_id)
    details = rexel_details.fetch(article)
    rexel_details.apply(article, details)
    db.session.commit()
    if details.found:
        flash("Uitgebreide omschrijving en kenmerken opgehaald bij Rexel.", "success")
    else:
        flash(details.error or "Rexel heeft geen uitgebreide gegevens voor dit artikel.", "warning")
    return redirect(url_for("articles.edit", article_id=article.id))
