"""All WTForms form classes, grouped per module.

Forms do the server-side validation of user input and, through Flask-WTF,
CSRF protection. Dynamic select options (users, projects) are filled in by
the views right before rendering, because they come from the database.

Convention: optional foreign keys use ``0`` as "nothing selected", because a
``SelectField(coerce=int)`` cannot represent ``None``. Use :func:`none_if_zero`
when copying such a value to a model.
"""

from __future__ import annotations

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


# --------------------------------------------------------------- projects
class ProjectForm(FlaskForm):
    code = StringField("Projectcode", validators=[DataRequired(), Length(max=30)])
    name = StringField("Naam", validators=[DataRequired(), Length(max=200)])
    customer = StringField("Klant", validators=[Optional(), Length(max=200)])
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
    description = StringField("Omschrijving", validators=[DataRequired(), Length(max=300)])
    manufacturer = StringField("Fabrikant", validators=[Optional(), Length(max=120)])
    manufacturer_ref = StringField("Bestelcode fabrikant", validators=[Optional(), Length(max=120)])
    supplier = StringField("Leverancier", validators=[Optional(), Length(max=120)])
    category = StringField("Categorie", validators=[Optional(), Length(max=120)])
    unit = StringField("Eenheid", default="st", validators=[DataRequired(), Length(max=20)])
    unit_price = DecimalField("Eenheidsprijs (€)", places=2, validators=[Optional(), NumberRange(min=0)])
    notes = TextAreaField("Opmerkingen", validators=[Optional()])
    active = BooleanField("Actief", default=True)


class ImportForm(FlaskForm):
    file = FileField(
        "Excel-bestand (.xlsx)",
        validators=[FileRequired(), FileAllowed(["xlsx"], "Enkel .xlsx bestanden.")],
    )


# ------------------------------------------------------------- purchasing
class PurchaseOrderForm(FlaskForm):
    number = StringField("Bestelnummer", validators=[DataRequired(), Length(max=40)])
    project_id = SelectField("Project", coerce=int)
    supplier = StringField("Leverancier", validators=[DataRequired(), Length(max=120)])
    status = SelectField("Status", choices=OrderStatus.choices(), default="draft")
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
    unit_price = DecimalField("Eenheidsprijs (€)", places=2, validators=[Optional(), NumberRange(min=0)])


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
    note = StringField("Opmerking", validators=[Optional(), Length(max=300)])

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("Einddatum ligt voor de startdatum.")

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
    parent_id = SelectField("Bovenliggende pagina", coerce=int)
    content = TextAreaField("Inhoud")


class TagForm(FlaskForm):
    name = StringField("Naam", validators=[DataRequired(), Length(max=60)])
    color = StringField(
        "Kleur",
        default="#6c757d",
        validators=[Regexp(r"^#[0-9a-fA-F]{6}$", message="Gebruik een kleurcode zoals #6c757d.")],
    )
