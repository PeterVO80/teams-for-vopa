"""Supplier catalogues: upload a price list, filter, select and import articles.

See ``services/supplier_catalog.py`` for the logic; this module only handles
the screens.
"""

import sqlalchemy as sa
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import CatalogImportForm, CatalogUploadForm
from ..models import Article, CatalogItem, SupplierCatalog
from ..services import supplier_catalog as svc
from . import protected_blueprint

bp = protected_blueprint("catalog", __name__)

#: Rows per page in the catalogue screen.
PER_PAGE = 100


@bp.route("/", methods=["GET", "POST"])
def index():
    """List of uploaded catalogues + upload form."""
    form = CatalogUploadForm()
    if form.validate_on_submit():
        try:
            catalog = svc.load_catalog(
                form.file.data, form.supplier.data.strip(), current_user.id, replace=form.replace.data
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
            sa.select(Article.supplier, sa.func.count(Article.id))
            .where(Article.supplier_ref.is_not(None))
            .group_by(Article.supplier)
        ).all()
    )
    return render_template(
        "catalog/index.html", form=form, catalogs=catalogs, imported=imported,
        max_upload_mb=current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
    )


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
    db.session.commit()
    flash(f"Import klaar: {result.summary}.", "warning" if result.errors else "success")
    for error in result.errors[:10]:
        flash(error, "warning")
    if len(result.errors) > 10:
        flash(f"... en nog {len(result.errors) - 10} meldingen.", "warning")
    # done=1 tells static/js/catalog.js to clear the selection.
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
