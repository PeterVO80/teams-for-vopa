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
from decimal import Decimal

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    FieldList,
    FloatField,
    HiddenField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.widgets import TextInput
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
    PdfDetail,
    QuoteStatus,
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


class HoursField(DecimalField):
    """Hours (or km) that also accept a decimal comma: "7,5" = 7.5.

    A text input: type=number refuses a comma in many browsers.
    """

    widget = TextInput()

    def process_formdata(self, valuelist):
        if valuelist:
            valuelist = [str(v).strip().replace(",", ".") for v in valuelist]
        super().process_formdata(valuelist)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        if self.data is None:
            return ""
        return f"{Decimal(str(self.data)).normalize():f}".replace(".", ",")


class AmountField(PriceField):
    """Price with up to 4 decimals that also accepts a decimal comma ("12,50").

    A text input (not type=number, which refuses a comma in many browsers).
    """

    widget = TextInput()

    def process_formdata(self, valuelist):
        if valuelist:
            valuelist = [str(v).strip().replace(" ", "").replace("€", "").replace(",", ".") for v in valuelist]
        super().process_formdata(valuelist)


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
    new_password = PasswordField("Nieuw wachtwoord", validators=[DataRequired(), Length(min=10, message="Minstens 10 tekens.")])
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
    outlook_sync = BooleanField(
        "Outlook-agenda koppelen", default=True,
        description="Leest de Outlook-agenda (mailbox = e-mailadres) in de teamplanning en schrijft de planning naar Outlook.",
    )
    password = PasswordField(
        "Wachtwoord",
        description="Laat leeg om het huidige wachtwoord te behouden.",
        validators=[Optional(), Length(min=10, message="Minstens 10 tekens.")],
    )


# -------------------------------------------------------------- relations
class CompanyForm(FlaskForm):
    """A customer and/or supplier."""

    name = StringField("Naam", validators=[DataRequired(), Length(max=200)])
    is_customer = BooleanField("Klant")
    is_supplier = BooleanField("Leverancier")
    vat_number = StringField("Btw-nummer", validators=[Optional(), Length(max=30)])
    phone = StringField("Telefoon", validators=[Optional(), Length(max=50)])
    email = StringField("E-mail", validators=[Optional(), Email(), Length(max=120)])
    website = StringField("Website", validators=[Optional(), Length(max=200)])
    street = StringField("Straat en nummer", validators=[Optional(), Length(max=200)])
    box = StringField("Bus", validators=[Optional(), Length(max=20)])
    postal_code = StringField("Postcode", validators=[Optional(), Length(max=20)])
    city = StringField("Gemeente", validators=[Optional(), Length(max=120)])
    country = StringField("Land", default="België", validators=[Optional(), Length(max=80)])
    distance_km = DecimalField(
        "Afstand vanaf kantoor (km, enkel)", places=1, validators=[Optional(), NumberRange(min=0, max=5000)],
        description="Wordt berekend uit het adres; je kan ze zelf aanpassen. Buitendienst = heen en terug.",
    )
    notes = TextAreaField("Opmerkingen", validators=[Optional()])
    active = BooleanField("Actief", default=True)

    def validate_is_supplier(self, field):
        if not (field.data or self.is_customer.data):
            raise ValidationError("Duid aan of dit een klant, een leverancier of beide is.")


# --------------------------------------------------------------- projects
class ProjectForm(FlaskForm):
    name = StringField("Naam", validators=[DataRequired(), Length(max=200)])
    #: Choices: Company.choices("customer") - 0 = no customer.
    customer_id = SelectField("Klant", coerce=int, default=0)
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
    supplier_order_number = StringField(
        "Leverancier ordernummer", validators=[Optional(), Length(max=60)],
        description="Het ordernummer van de leverancier zelf (bv. het Rexel ordernummer).",
    )
    status = SelectField("Status", choices=OrderStatus.choices(), default="draft")
    stock_location_id = SelectField(
        "Op stock leggen in", coerce=int,
        description="Enkel voor stockaanvullingen: leveringen van gekoppelde artikels gaan dan automatisch op stock.",
    )
    order_date = DateField("Besteldatum", validators=[Optional()])
    expected_date = DateField("Verwachte levering", validators=[Optional()])
    notes = TextAreaField("Opmerkingen", validators=[Optional()])


class SupplierOrderImportForm(FlaskForm):
    """Upload an order file of a supplier (Rexel "Order Info" Excel)."""

    file = FileField(
        "Orderbestand van Rexel (.xls of .xlsx)",
        validators=[FileRequired(), FileAllowed(["xls", "xlsx", "XLS", "XLSX"], "Enkel .xls of .xlsx bestanden.")],
    )
    supplier_id = SelectField("Leverancier", coerce=int, validators=[DataRequired(message="Kies een leverancier.")])
    project_id = SelectField("Project", coerce=int)
    stock_location_id = SelectField(
        "Op stock leggen in", coerce=int,
        description="Enkel voor stockaanvullingen: leveringen van gekoppelde artikels gaan dan automatisch op stock.",
    )


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
        ("return", "Retour van project (overschot op stock leggen)"),
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


# ------------------------------------------------------------ time registration
class TimeEntryForm(FlaskForm):
    """Hours on a project (blueprints/timesheet.py)."""

    work_date = DateField("Datum", validators=[DataRequired()])
    project_id = SelectField("Project", coerce=int, validators=[DataRequired(message="Kies een project.")])
    hours = HoursField("Uren", places=2, validators=[DataRequired(), NumberRange(min=0.25, max=24)],
                       render_kw={"inputmode": "decimal"})
    description = TextAreaField("Omschrijving / opmerking", validators=[Optional()], render_kw={"rows": 3})
    field_service = BooleanField("Buitendienst (bij de klant)")
    customer_id = SelectField("Klant", coerce=int, default=0)
    km = HoursField("Gereden km (heen en terug)", places=1, validators=[Optional(), NumberRange(min=0, max=5000)],
                    render_kw={"inputmode": "decimal"})


class WeekEntryForm(FlaskForm):
    """Hours on one project for several days of a week at once (blueprints/timesheet.py)."""

    week_start = DateField("Week", validators=[DataRequired()])
    project_id = SelectField("Project", coerce=int, validators=[DataRequired(message="Kies een project.")])
    #: Monday .. Sunday; empty days are skipped.
    hours = FieldList(HoursField(validators=[Optional(), NumberRange(min=0, max=24)],
                                 render_kw={"inputmode": "decimal"}), min_entries=7, max_entries=7)
    description = TextAreaField("Omschrijving / opmerking", validators=[Optional()], render_kw={"rows": 2})
    field_service = BooleanField("Buitendienst (bij de klant)")
    customer_id = SelectField("Klant", coerce=int, default=0)
    km = HoursField("Gereden km per dag (heen en terug)", places=1,
                    validators=[Optional(), NumberRange(min=0, max=5000)], render_kw={"inputmode": "decimal"})


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
    start_time = TimeField("Van (uur)", validators=[Optional()])
    end_time = TimeField("Tot (uur)", validators=[Optional()])
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

    def validate(self, extra_validators=None):
        # Checked here, not in validate_end_time: WTForms skips that for an
        # empty optional field, and "only a start time" must be refused too.
        ok = super().validate(extra_validators)
        start, end = self.start_time.data, self.end_time.data
        if bool(start) != bool(end):
            self.end_time.errors.append("Vul zowel het begin- als het einduur in (of laat beide leeg).")
            return False
        if start and end and end <= start:
            self.end_time.errors.append("Het einduur moet na het beginuur liggen.")
            return False
        return ok

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


# ------------------------------------------------------------------ quotes
class QuoteForm(FlaskForm):
    """Header of a quote (blueprints/quotes.py)."""

    title = StringField("Onderwerp", validators=[DataRequired(), Length(max=200)],
                        description="Bv. \"Retrofit verpakkingslijn L3\" - wordt ook de naam van een project dat je ervan maakt.")
    customer_id = SelectField("Klant", coerce=int, default=0)
    #: Rendered by hand (options per customer, static/js/quote_template.js); checked in blueprints/quotes.py.
    contact_id = SelectField("Contactpersoon (t.a.v.)", coerce=int, default=0, validate_choice=False)
    project_id = SelectField("Project", coerce=int, default=0,
                             description="Optioneel: koppel de offerte aan een bestaand project.")
    status = SelectField("Status", choices=QuoteStatus.choices(), default="draft")
    quote_date = DateField("Offertedatum", validators=[Optional()])
    valid_until = DateField("Geldig tot", validators=[Optional()])
    vat_rate = HoursField("Btw (%)", places=2, default=21, validators=[DataRequired(), NumberRange(min=0, max=100)])
    introduction = TextAreaField("Begeleidende tekst voor de klant", validators=[Optional()], render_kw={"rows": 8},
                                 description="Bovenaan de offerte. Kies hierboven een sjabloon en vul aan waar nodig.")
    notes = TextAreaField("Interne notities", validators=[Optional()], render_kw={"rows": 3},
                          description="Alleen intern, niet voor de klant.")


class QuoteLineForm(FlaskForm):
    """A section title, material line or labour line (blueprints/quotes.py)."""

    kind = HiddenField()
    article_id = HiddenField()
    #: Checked in blueprints/quotes.py (only on labour lines; may be absent).
    service_type_id = SelectField("Soort dienst", coerce=int, default=0, validate_choice=False)
    #: Required, checked in blueprints/quotes.py (labour: the service's name when empty).
    description = StringField("Omschrijving", validators=[Optional(), Length(max=300)])
    quantity = HoursField("Aantal", places=2, validators=[Optional(), NumberRange(min=0)],
                          render_kw={"inputmode": "decimal"})
    unit = StringField("Eenheid", validators=[Optional(), Length(max=20)])
    cost_price = AmountField("Inkoopprijs (€)", render_kw={"inputmode": "decimal"})
    markup = HoursField("Marge (%)", places=1, validators=[Optional(), NumberRange(min=-100, max=1000)],
                        render_kw={"inputmode": "decimal"})
    unit_price = AmountField("Verkoopprijs (€)", render_kw={"inputmode": "decimal"})
    note = TextAreaField("Notitie / commentaar", validators=[Optional()], render_kw={"rows": 2},
                         description="Optioneel: extra uitleg onder de lijn.")
    #: Section titles only (absent on other lines: checked in blueprints/quotes.py).
    pdf_detail = SelectField("Op de offerte-PDF", choices=PdfDetail.choices(), default="full", validate_choice=False,
                             description="Bv. materiaal enkel met aantallen, werkuren met alle details.")


class ContactForm(FlaskForm):
    """A contact person of a customer or supplier (blueprints/relations.py)."""

    company_id = SelectField("Klant / leverancier", coerce=int,
                             validators=[DataRequired(message="Kies de klant of leverancier.")])
    last_name = StringField("Naam", validators=[DataRequired(), Length(max=80)])
    first_name = StringField("Voornaam", validators=[Optional(), Length(max=80)])
    function = StringField("Functie", validators=[Optional(), Length(max=120)],
                           description="Bv. projectleider, aankoper, onderhoudsverantwoordelijke.")
    email = StringField("E-mail", validators=[Optional(), Email(), Length(max=120)])
    phone = StringField("Telefoon", validators=[Optional(), Length(max=50)])
    mobile = StringField("GSM", validators=[Optional(), Length(max=50)])
    notes = TextAreaField("Opmerkingen", validators=[Optional()], render_kw={"rows": 2})
    active = BooleanField("Actief", default=True, description="Uitvinken als de persoon er niet meer werkt.")


class MfaCodeForm(FlaskForm):
    """The 6-digit code of the authenticator app, or a recovery code (blueprints/auth.py)."""

    code = StringField("Code", validators=[DataRequired(message="Vul de code in."), Length(max=20)],
                       render_kw={"autocomplete": "one-time-code", "autofocus": True, "autocapitalize": "off",
                                  "spellcheck": "false", "placeholder": "123456"})
