"""Supplier catalogues: upload a price list, filter, select and import articles.

See ``services/supplier_catalog.py`` for the logic; this module only handles
the screens.
"""

import sqlalchemy as sa
from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import CatalogImportForm, CatalogUploadForm
from ..models import Article, CatalogItem, Company, SupplierCatalog
from ..services import rexel_details
from ..services import supplier_catalog as svc
from . import protected_blueprint
from .relations import add_button as add_relation_button

bp = protected_blueprint("catalog", __name__)

#: Rows per page in the catalogue screen.
PER_PAGE = 100


@bp.route("/", methods=["GET", "POST"])
def index():
    """List of uploaded catalogues + upload form."""
    form = CatalogUploadForm()
    form.supplier_id.choices = Company.choices("supplier", empty="- kies een leverancier -")
    add_relation_button(form.supplier_id, "supplier")
    if not form.is_submitted():
        rexel = Company.by_name("Rexel")
        form.supplier_id.data = rexel.id if rexel else 0
    if form.validate_on_submit():
        try:
            catalog = svc.load_catalog(
                form.file.data, db.session.get(Company, form.supplier_id.data), current_user.id,
                replace=form.replace.data,
            )
            db.session.commit()
        except svc.CatalogFileError as exc:
            db.session.rollback()
            form.file.errors.append(str(exc))
        else:
            flash(f"Prijslijst geladen: {catalog.item_count} artikelen.", "success")
            return redirect(url_for("catalog.detail", catalog_id=catalog.id))

    catalogs = db.session.scalars(sa.select(SupplierCatalog).order_by(SupplierCatalog.created_at.desc())).all()
    # How many articles were imported per supplier (for the overview).
    imported = dict(
        db.session.execute(
            sa.select(Article.supplier_id, sa.func.count(Article.id))
            .where(Article.supplier_ref.is_not(None))
            .group_by(Article.supplier_id)
        ).all()
    )
    return render_template(
        "catalog/index.html", form=form, catalogs=catalogs, imported=imported,
        max_upload_mb=current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
    )


@bp.route("/supplier/<supplier>")
def supplier(supplier: str):
    """Shortcut used by the "Rexel-import" button on the article page.

    Opens the most recent catalogue of that supplier, or the upload page when
    no price list has been loaded yet.
    """
    catalog = db.session.scalar(
        sa.select(SupplierCatalog)
        .join(Company, SupplierCatalog.supplier_id == Company.id)
        .where(sa.func.lower(Company.name) == supplier.lower())
        .order_by(SupplierCatalog.created_at.desc())
        .limit(1)
    )
    if catalog is None:
        flash(f"Er is nog geen prijslijst van {supplier} geladen. Laad hieronder het DBF-bestand.", "info")
        return redirect(url_for("catalog.index"))
    return redirect(url_for("catalog.detail", catalog_id=catalog.id))


def _filters() -> dict:
    """The filter values from the query string (also posted along with an import)."""
    source = request.values
    return {
        "search": source.get("q", "").strip(),
        "manufacturer": source.get("manufacturer", ""),
        "product_group": source.get("group", ""),
        "status": source.get("status", svc.STATUS_ALL),
    }


@bp.route("/<int:catalog_id>")
def detail(catalog_id: int):
    """Filter the catalogue, select items and import them."""
    catalog = db.get_or_404(SupplierCatalog, catalog_id)
    filters = _filters()
    query = svc.filtered_items(catalog, **filters)
    page = db.paginate(query, per_page=PER_PAGE, error_out=False)
    linked = svc.linked_articles(catalog, page.items)

    # Filter options with counts. Product groups are only listed for the
    # chosen manufacturer: a full price list has thousands of them.
    base = sa.select(sa.func.count(CatalogItem.id)).where(CatalogItem.catalog_id == catalog.id)
    manufacturers = db.session.execute(
        base.add_columns(CatalogItem.manufacturer)
        .group_by(CatalogItem.manufacturer).order_by(CatalogItem.manufacturer)
    ).all()
    groups = []
    if filters["manufacturer"]:
        groups = db.session.execute(
            base.add_columns(CatalogItem.product_group_desc)
            .where(CatalogItem.manufacturer == filters["manufacturer"])
            .group_by(CatalogItem.product_group_desc).order_by(CatalogItem.product_group_desc)
        ).all()

    import_form = CatalogImportForm()
    import_form.number_source.choices = list(svc.NUMBER_SOURCES.items())
    categories = db.session.scalars(
        sa.select(Article.category).where(Article.category.is_not(None)).distinct().order_by(Article.category)
    ).all()
    return render_template(
        "catalog/detail.html",
        catalog=catalog,
        page=page,
        linked=linked,
        filters=filters,
        manufacturers=manufacturers,
        groups=groups,
        import_form=import_form,
        categories=categories,
        pending_details=rexel_details.pending_count(),
        missing_details=db.session.scalar(sa.select(sa.func.count(Article.id)).where(
            Article.supplier_id == catalog.supplier_id, Article.active.is_(True),
            Article.url.ilike("%netstore.rexel.be%"), Article.details_status.is_(None))),
    )


@bp.route("/<int:catalog_id>/import", methods=["POST"])
def import_selected(catalog_id: int):
    """Import the ticked items, or every item matching the current filter."""
    catalog = db.get_or_404(SupplierCatalog, catalog_id)
    form = CatalogImportForm()
    form.number_source.choices = list(svc.NUMBER_SOURCES.items())
    filters = _filters()
    back = url_for("catalog.detail", catalog_id=catalog.id, q=filters["search"] or None,
                   manufacturer=filters["manufacturer"] or None, group=filters["product_group"] or None,
                   status=filters["status"] if filters["status"] != svc.STATUS_ALL else None)

    if not form.validate_on_submit():
        flash("Import mislukt: ongeldige gegevens.", "danger")
        return redirect(back)

    if form.scope.data == "filter":
        items = svc.filtered_items(catalog, **filters)
    else:
        ids = [int(i) for i in (form.item_ids.data or "").split(",") if i.strip().isdigit()]
        if not ids:
            flash("Selecteer eerst één of meer artikelen.", "warning")
            return redirect(back)
        items = sa.select(CatalogItem).where(CatalogItem.catalog_id == catalog.id, CatalogItem.id.in_(ids))

    result = svc.import_items(
        catalog, items, number_source=form.number_source.data,
        category=form.category.data, update_existing=form.update_existing.data,
    )
    queued = rexel_details.queue(result.created_articles) if form.fetch_details.data else 0
    db.session.commit()
    flash(f"Import klaar: {result.summary}.", "warning" if result.errors else "success")
    if queued:
        flash(f"De uitgebreide omschrijving wordt opgehaald bij Rexel voor {queued} nieuwe artikelen.", "info")
    for error in result.errors[:10]:
        flash(error, "warning")
    if len(result.errors) > 10:
        flash(f"... en nog {len(result.errors) - 10} meldingen.", "warning")
    # done=1 tells static/js/catalog.js to clear the selection, and makes the
    # details panel start fetching (static/js/article_details.js).
    return redirect(back + ("&" if "?" in back else "?") + "done=1")


@bp.route("/<int:catalog_id>/update-prices", methods=["POST"])
def update_prices(catalog_id: int):
    """Update all previously imported articles with this price list."""
    catalog = db.get_or_404(SupplierCatalog, catalog_id)
    result = svc.update_prices(catalog)
    db.session.commit()
    flash(f"Artikelen van {catalog.supplier} bijgewerkt: {result.updated} gewijzigd, "
          f"{result.unchanged} ongewijzigd.", "success")
    return redirect(url_for("catalog.detail", catalog_id=catalog.id))


@bp.route("/<int:catalog_id>/delete", methods=["POST"])
def delete(catalog_id: int):
    catalog = db.get_or_404(SupplierCatalog, catalog_id)
    svc.delete_catalog(catalog)
    db.session.commit()
    flash("Catalogus verwijderd. Geïmporteerde artikelen blijven behouden.", "success")
    return redirect(url_for("catalog.index"))


# ------------------------------------------- details from the Rexel product page
@bp.route("/details/run", methods=["POST"])
def details_run():
    """Fetch the next batch of queued articles (called repeatedly by article_details.js)."""
    processed = rexel_details.process_pending()
    return jsonify(processed=processed, remaining=rexel_details.pending_count())


@bp.route("/<int:catalog_id>/details/queue", methods=["POST"])
def details_queue(catalog_id: int):
    """Queue every article of this supplier whose details were never fetched (or failed)."""
    catalog = db.get_or_404(SupplierCatalog, catalog_id)
    articles = db.session.scalars(sa.select(Article).where(
        Article.supplier_id == catalog.supplier_id,
        Article.active.is_(True),
        sa.or_(Article.details_status.is_(None),
               Article.details_status.in_([rexel_details.ERROR, rexel_details.NOT_FOUND])),
    ))
    queued = rexel_details.queue(articles)
    db.session.commit()
    flash(f"{queued} artikelen klaargezet om de uitgebreide omschrijving op te halen." if queued
          else "Alle artikelen hebben hun uitgebreide omschrijving al.", "info")
    return redirect(url_for("catalog.detail", catalog_id=catalog.id, details=1))
