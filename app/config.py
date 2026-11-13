"""Application configuration.

All settings are read from environment variables so the very same image can
run in development, test and production. Sensible defaults are provided for
local development only - never run production with the default SECRET_KEY.
"""

import os


class Config:
    """Base configuration shared by all environments."""

    #: Signs session cookies and CSRF tokens. MUST be overridden in production.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-key")

    #: SQLAlchemy connection string. ``postgresql+psycopg`` selects psycopg 3.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://vopa:vopa@localhost:5432/vopa"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    #: Detect connections dropped by the database (e.g. after a DB restart).
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    #: Maximum upload size in MB. A complete supplier price list (DBF) can be
    #: well over 100 MB, hence the generous default.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", 300)) * 1024 * 1024

    #: Number of rows shown per page in paginated list views.
    ITEMS_PER_PAGE = 50

    #: Project codes are generated automatically as ``<prefix>/<year>/<seq>``,
    #: e.g. ``PRJ/2026/0001``. The sequence restarts every calendar year.
    PROJECT_CODE_PREFIX = os.environ.get("PROJECT_CODE_PREFIX", "PRJ")

    #: Default number of working hours in one day, used by the team planning
    #: to express workload as a percentage.
    WORKDAY_HOURS = float(os.environ.get("WORKDAY_HOURS", 8))


class TestConfig(Config):
    """Configuration used by the automated test-suite.

    Uses an in-memory SQLite database by default so the tests need no
    infrastructure. Set ``TEST_DATABASE_URL`` to run them against a real
    (empty, throw-away!) PostgreSQL database instead.
    """

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}
    WTF_CSRF_ENABLED = False
