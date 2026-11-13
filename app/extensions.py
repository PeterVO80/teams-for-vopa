"""Flask extension singletons.

Extensions are instantiated here *without* an application and bound later in
:func:`app.create_app`. This avoids circular imports: models and blueprints
import ``db`` from this module instead of from the application package.
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: Deterministic names for indexes and constraints. Without this, PostgreSQL
#: generates names itself and later Alembic migrations cannot reliably
#: reference (e.g. drop) a constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: The SQLAlchemy database handle used throughout the application.
db = SQLAlchemy(model_class=Base)

#: Alembic-based schema migrations (``flask db upgrade`` etc.).
migrate = Migrate()

#: Session based authentication.
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Log in om deze pagina te bekijken."
login_manager.login_message_category = "warning"

#: CSRF protection for all POST requests (forms *and* AJAX calls).
csrf = CSRFProtect()
