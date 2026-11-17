"""Application configuration.

All settings are read from environment variables so the very same image can
run in development, test and production. Sensible defaults are provided for
local development only - never run production with the default SECRET_KEY.
"""

import os
import tempfile


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

    #: Log in with the Microsoft 365 account: off / on / required
    #: (services/microsoft_login.py). Uses the MS365_* app registration.
    MICROSOFT_LOGIN = os.environ.get("MICROSOFT_LOGIN", "off")

    #: Public domain name (e.g. teams.vopa.be) when running on the internet.
    DOMAIN = os.environ.get("DOMAIN", "").strip()
    #: Production on the internet behind an HTTPS reverse proxy (see
    #: security.py). Defaults to on as soon as a DOMAIN is set.
    HTTPS = os.environ.get("HTTPS", "1" if DOMAIN else "0").lower() in ("1", "true", "yes")
    #: Number of reverse proxies in front of the app (Caddy in the stack = 1):
    #: their X-Forwarded-* headers give the real client address and scheme.
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", "0") or 0)

    #: Where back-ups are stored (services/backup.py); a Docker volume in production.
    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups"))
    #: Automatic daily back-ups older than this are deleted.
    BACKUP_KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "14") or 14)

    #: Number of rows shown per page in paginated list views.
    ITEMS_PER_PAGE = 50

    #: Project codes are generated automatically as ``<prefix>/<year>/<seq>``,
    #: e.g. ``PRJ/2026/0001``. The sequence restarts every calendar year.
    PROJECT_CODE_PREFIX = os.environ.get("PROJECT_CODE_PREFIX", "PRJ")

    #: Outlook / Microsoft 365 calendar link (services/outlook.py). Active only
    #: when tenant, client id and secret of the app registration are set.
    MS365_TENANT_ID = os.environ.get("MS365_TENANT_ID", "")
    MS365_CLIENT_ID = os.environ.get("MS365_CLIENT_ID", "")
    MS365_CLIENT_SECRET = os.environ.get("MS365_CLIENT_SECRET", "")
    #: Windows (or IANA) time zone of the Outlook appointments.
    MS365_TIMEZONE = os.environ.get("MS365_TIMEZONE", "Romance Standard Time")
    #: Colour of the "VOPA Teams" category in Outlook (oranje, rood, blauw, ...
    #: or an Outlook preset such as "preset1"). Empty = leave the colour alone.
    MS365_CATEGORY_COLOR = os.environ.get("MS365_CATEGORY_COLOR", "oranje")
    #: Public address of the application, used for links in Outlook appointments.
    APP_URL = os.environ.get("APP_URL", "") or (f"https://{DOMAIN}" if DOMAIN else "")

    #: Default number of working hours in one day, used by the team planning
    #: to express workload as a percentage.
    WORKDAY_HOURS = float(os.environ.get("WORKDAY_HOURS", 8))
    #: The working day (services/workday.py): planned days and Outlook
    #: appointments fall in these hours; the break does not count as work.
    WORKDAY_START = os.environ.get("WORKDAY_START", "08:30")
    WORKDAY_END = os.environ.get("WORKDAY_END", "17:00")
    BREAK_START = os.environ.get("BREAK_START", "12:00")
    BREAK_END = os.environ.get("BREAK_END", "12:30")


class TestConfig(Config):
    """Configuration used by the automated test-suite.

    Uses an in-memory SQLite database by default so the tests need no
    infrastructure. Set ``TEST_DATABASE_URL`` to run them against a real
    (empty, throw-away!) PostgreSQL database instead.
    """

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    BACKUP_DIR = os.path.join(tempfile.gettempdir(), "vopa-teams-test-backups")
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}
    WTF_CSRF_ENABLED = False
