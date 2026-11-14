"""All WTForms form classes, grouped per module.

Forms do the server-side validation of user input and, through Flask-WTF,
CSRF protection. Dynamic select options (users, projects) are filled in by
the views right before rendering, because they come from the database.

Convention: optional foreign keys use ``0`` as "nothing selected", because a
``SelectField(coerce=int)`` cannot represent ``None``. Use :func:`none_if_zero`
when copying such a value to a model.
"""

from __future__ import annotations

from datetime import timedelta

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    FloatField,
    HiddenField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

from .models import (
    AssignmentKind,
    OrderStatus,
    ProjectStatus,
    TaskPriority,
    TaskStatus,
    UserRole,
)


class PriceField(DecimalField):
    """Price in EUR with up to 4 decimals (cable is priced per metre: 0.3422).

    Shown with at least 2 and at most 4 decimals: ``385.00``, ``0.3422``.
    """

    def __init__(self, label="Eenheidsprijs (€)", **kwargs):
        kwargs.setdefault("validators", [Optional(), NumberRange(min=0)])
        super().__init__(label, places=None, **kwargs)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        if self.data is None:
            return ""
        text = f"{self.data:.4f}".rstrip("0")
        decimals = len(text.split(".")[1])
        return text + "0" * (2 - decimals) if decimals < 2 else text


def none_if_zero(value: int | None) -> int | None:
    """Translate the ``0`` placeholder of optional selects back to ``None``."""
    return value or None


# ------------------------------------------------------------------- auth
class LoginForm(FlaskForm):
    username = StringField("Gebruikersnaam", validators=[DataRequired()])
    password = PasswordField("Wachtwoord", validators=[DataRequired()])
    remember = BooleanField("Aangemeld blijven")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Huidig wachtwoord", validators=[DataRequired()])
    new_password = PasswordField("Nieuw wachtwoord", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField(
        "Herhaal nieuw wachtwoord",
        validators=[EqualTo("new_password", message="Wachtwoorden komen niet overeen.")],
    )


class UserForm(FlaskForm):
    username = StringField("Gebruikersnaam", validators=[DataRequired(), Length(max=64)])
    full_name = StringField("Volledige naam", validators=[DataRequired(), Length(max=120)])
    email = StringField("E-mail", validators=[Optional(), Email(), Length(max=120)])
    role = SelectField("Rol", choices=UserRole.choices())
    color = StringField(
        "Kleur in planning",
        default="#0d6efd",
        validators=[Regexp(r"^#[0-9a-fA-F]{6}$", message="Gebruik een kleurcode zoals #0d6efd.")],
    )
    active = BooleanField("Actief", default=True)
    password = PasswordField(
        "Wachtwoord",
        description="Laat leeg om het huidige wachtwoord te behouden.",
        validators=[Optional(), Length(min=8)],
    )


# -------------------------------------------------------------- relations
class CompanyForm(FlaskForm):
    """A customer and/or supplier."""

    name = StringField("Naam", validators=[DataRequired(), Length(max=200)])
    is_customer = BooleanField("Klant")
    is_supplier = BooleanField("Leverancier")
    vat_number = StringField("Btw-nummer", validators=[Optional(), Length(max=30)])
    contact_person = StringField("Contactpersoon", validators=[Optional(), Length(max=120)])
    phone = StringField("Telefoon", validators=[Optional(), Length(max=50)])
    email = StringField("E-mail", validators=[Optional(), Email(), Length(max=120)])
    website = StringField("Website", validators=[Optional(), Length(max=200)])
    street = StringField("Straat en nummer", validators=[Optional(), Length(max=200)])
    postal_code = StringField("Postcode", validators=[Optional(), Length(max=20)])
    city = StringField("Gemeente", validators=[Optional(), Length(max=120)])
    country = StringField("Land", default="België", validators=[Optional(), Length(max=80)])
    notes = TextAreaField("Opmerkingen", validators=[Optional()])
    active = BooleanField("Actief", default=True)

    def validate_is_supplier(self, field):
        if not (field.data or self.is_customer.data):
            raise ValidationError("Duid aan of dit een klant, een leverancier of beide is.")


# --------------------------------------------------------------- projects
class ProjectForm(FlaskForm):
    name = StringField("Naam", validators=[DataRequired(), Length(max=200)])
    #: Choices: Company.choices("customer") - 0 = no customer.
    customer_id = SelectField("Klant", coerce=int)
    status = SelectField("Status", choices=ProjectStatus.choices(), default="active")
    manager_id = SelectField("Projectleider", coerce=int)
    start_date = DateField("Startdatum", validators=[Optional()])
    end_date = DateField("Einddatum", validators=[Optional()])
    description = TextAreaField("Omschrijving", validators=[Optional()])

    def validate_end_date(self, field):
        if field.data and self.start_date.data and field.data < self.start_date.data:
            raise ValidationError("Einddatum ligt voor de startdatum.")


# --------------------------------------------------------------- articles
class ArticleForm(FlaskForm):
    article_number = StringField("Artikelnummer", validators=[DataRequired(), Length(max=64)])
    #: Multi-line: extended descriptions from Rexel can be up to 250 characters.
    description = TextAreaField("Omschrijving", validators=[DataRequired(), Length(max=300)], render_kw={"rows": 3})
    manufacturer = StringField("Fabrikant", validators=[Optional(), Length(max=120)])
    manufacturer_ref = StringField("Bestelcode fabrikant", validators=[Optional(), Length(max=120)])
    supplier_id = SelectField("Leverancier", coerce=int)
    supplier_ref = StringField("Referentie leverancier", validators=[Optional(), Length(max=64)])
    category = StringField("Categorie", validators=[Optional(), Length(max=120)])
    unit = StringField("Eenheid", default="st", validators=[DataRequired(), Length(max=20)])
    unit_price = PriceField()
    min_stock = DecimalField(
        "Minimumstock", places=None, validators=[Optional(), NumberRange(min=0)],
        description="Laat leeg als dit artikel niet op stock gehouden wordt.",
    )
    ean = StringField("EAN-code", validators=[Optional(), Length(max=20)])
    url = StringField("Link webshop", validators=[Optional(), Length(max=300)])
    notes = TextAreaField("Opmerkingen", validators=[Optional()])
    active = BooleanField("Actief", default=True)


class CatalogUploadForm(FlaskForm):
    """Upload a supplier price list (Rexel DBF)."""

    supplier_id = SelectField("Leverancier", coerce=int, validators=[DataRequired(message="Kies een leverancier.")])
    file = FileField(
        "Prijslijst (.dbf)",
        validators=[FileRequired(), FileAllowed(["dbf", "DBF"], "Enkel .dbf bestanden.")],
    )
    replace = BooleanField(
        "Vorige prijslijst van deze leverancier vervangen", default=True,
        description="Aanbevolen: de oude catalogus wordt verwijderd, je geïmporteerde artikelen blijven.",
    )


class CatalogImportForm(FlaskForm):
    """Options for importing the selected catalogue items as articles."""

    #: "selected" = the ticked items (``item_ids``), "filter" = everything matching the filter.
    scope = HiddenField(default="selected")
    item_ids = HiddenField()
    number_source = SelectField("Artikelnummer van nieuwe artikelen")
    category = StringField("Categorie voor nieuwe artikelen", validators=[Optional(), Length(max=120)])
    update_existing = BooleanField(
        "Bestaande artikelen bijwerken (omschrijving, prijs, ...)", default=True
    )
    fetch_details = BooleanField("Uitgebreide omschrijving ophalen bij Rexel", default=True)


class ImportForm(FlaskForm):
    file = FileField(
        "Excel-bestand (.xlsx)",
        validators=[FileRequired(), FileAllowed(["xlsx"], "Enkel .xlsx bestanden.")],
    )


# ------------------------------------------------------------- purchasing
class PurchaseOrderForm(FlaskForm):
    number = StringField("Bestelnummer", validators=[DataRequired(), Length(max=40)])
    project_id = SelectField("Project", coerce=int)
    supplier_id = SelectField("Leverancier", coerce=int, validators=[DataRequired(message="Kies een leverancier.")])
    status = SelectField("Status", choices=OrderStatus.choices(), default="draft")
    stock_location_id = SelectField(
        "Op stock leggen in", coerce=int,
        description="Enkel voor stockaanvullingen: leveringen van gekoppelde artikels gaan dan automatisch op stock.",
    )
    order_date = DateField("Besteldatum", validators=[Optional()])
    expected_date = DateField("Verwachte levering", validators=[Optional()])
    notes = TextAreaField("Opmerkingen", validators=[Optional()])


class OrderLineForm(FlaskForm):
    #: Filled via the article search box (hidden field set by JavaScript) -
    #: when empty the free-text description is used.
    article_id = HiddenField()
    description = StringField("Omschrijving", validators=[DataRequired(), Length(max=300)])
    quantity = DecimalField("Aantal", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    unit = StringField("Eenheid", default="st", validators=[DataRequired(), Length(max=20)])
    unit_price = PriceField()


class AddArticleToOrderForm(FlaskForm):
    """Add one article from the article database to an open purchase order."""

    order_id = SelectField("Bestelling", coerce=int, validators=[DataRequired(message="Kies een bestelling.")])
    quantity = DecimalField("Aantal", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    unit_price = PriceField()


class StockMovementForm(FlaskForm):
    """One stock movement (popup in the stock module)."""

    article_id = SelectField("Artikel", coerce=int, validators=[DataRequired(message="Kies een artikel.")])
    kind = SelectField("Soort", choices=[
        ("issue", "Uitgifte (uit stock nemen)"),
        ("receipt", "Ontvangst (op stock leggen)"),
        ("transfer", "Verplaatsen naar andere locatie"),
        ("count", "Inventaris (geteld aantal invoeren)"),
    ])
    location_id = SelectField("Locatie", coerce=int, validators=[DataRequired(message="Kies een locatie.")])
    to_location_id = SelectField("Naar locatie", coerce=int)
    quantity = DecimalField("Aantal", places=None, validators=[DataRequired(), NumberRange(min=0)])
    project_id = SelectField("Project", coerce=int)
    moved_on = DateField("Datum", validators=[DataRequired()])
    note = StringField("Opmerking", validators=[Optional(), Length(max=300)])


class StockLocationForm(FlaskForm):
    name = StringField("Naam", validators=[DataRequired(), Length(max=80)])
    description = StringField("Omschrijving", validators=[Optional(), Length(max=200)])
    active = BooleanField("Actief", default=True)


class ReceiptForm(FlaskForm):
    quantity = DecimalField("Ontvangen aantal", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    received_on = DateField("Ontvangen op", validators=[DataRequired()])
    note = StringField("Opmerking / leveringsbon", validators=[Optional(), Length(max=300)])


# ------------------------------------------------------------------ tasks
class TaskForm(FlaskForm):
    title = StringField("Titel", validators=[DataRequired(), Length(max=200)])
    project_id = SelectField("Project", coerce=int)
    assignee_id = SelectField("Toegewezen aan", coerce=int)
    status = SelectField("Status", choices=TaskStatus.choices(), default="new")
    priority = SelectField("Prioriteit", choices=TaskPriority.choices(), default="normal")
    due_date = DateField("Deadline", validators=[Optional()])
    estimated_hours = FloatField("Geschatte uren", validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField("Omschrijving", validators=[Optional()])


# --------------------------------------------------------------- planning
class AssignmentForm(FlaskForm):
    user_id = SelectField("Medewerker", coerce=int, validators=[DataRequired()])
    kind = SelectField("Type", choices=AssignmentKind.choices(), default="project")
    project_id = SelectField("Project", coerce=int)
    start_date = DateField("Van", validators=[DataRequired()])
    end_date = DateField("Tot en met", validators=[DataRequired()])
    hours_per_day = FloatField(
        "Uren per dag", default=8.0, validators=[DataRequired(), NumberRange(min=0.5, max=24)]
    )
    include_weekends = BooleanField(
        "Weekenddagen inplannen",
        description="Zonder vinkje worden enkel maandag t/m vrijdag van deze periode ingepland.",
    )
    note = StringField("Opmerking", validators=[Optional(), Length(max=300)])

    def validate_end_date(self, field):
        start, end = self.start_date.data, field.data
        if not (start and end):
            return
        if end < start:
            raise ValidationError("Einddatum ligt voor de startdatum.")
        # A weekend-only period without the weekend option would plan nothing.
        only_weekend = all((start + timedelta(days=i)).weekday() >= 5 for i in range((end - start).days + 1))
        if only_weekend and not self.include_weekends.data:
            raise ValidationError("Deze periode bevat enkel weekenddagen: vink 'Weekenddagen inplannen' aan.")

    def validate_project_id(self, field):
        if self.kind.data == AssignmentKind.PROJECT.value and not field.data:
            raise ValidationError("Kies een project voor een projectplanning.")


# -------------------------------------------------------- notes & wiki
class NoteForm(FlaskForm):
    title = StringField("Titel", validators=[DataRequired(), Length(max=200)])
    project_id = SelectField("Project", coerce=int)
    #: Comma separated tag names, e.g. "Status meeting, Klant feedback".
    tags = StringField("Tags", description="Scheid tags met een komma.")
    pinned = BooleanField("Vastpinnen bovenaan")
    #: Markdown produced by the WYSIWYG editor (kept in sync by JavaScript).
    content = TextAreaField("Inhoud")


class WikiPageForm(FlaskForm):
    title = StringField("Titel", validators=[DataRequired(), Length(max=200)])
    #: 0 = general wiki, otherwise the id of the project whose wiki this is.
    project_id = SelectField("Wiki", coerce=int)
    parent_id = SelectField("Bovenliggende pagina", coerce=int)
    content = TextAreaField("Inhoud")


class TagForm(FlaskForm):
    name = StringField("Naam", validators=[DataRequired(), Length(max=60)])
    color = StringField(
        "Kleur",
        default="#6c757d",
        validators=[Regexp(r"^#[0-9a-fA-F]{6}$", message="Gebruik een kleurcode zoals #6c757d.")],
    )
