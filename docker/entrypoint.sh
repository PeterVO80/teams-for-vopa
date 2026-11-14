#!/bin/sh
# ---------------------------------------------------------------------------
# Container start-up script.
#   1. Wait until PostgreSQL accepts connections.
#   2. Apply pending database migrations (safe to run on every start).
#   3. Create the first administrator if the database has no users yet.
#   4. Start the command given as CMD (gunicorn by default).
# ---------------------------------------------------------------------------
set -e

echo "Waiting for the database..."
python - <<'PY'
import os, sys, time
import sqlalchemy as sa

url = os.environ["DATABASE_URL"]
for attempt in range(60):
    try:
        sa.create_engine(url).connect().close()
        sys.exit(0)
    except Exception as exc:  # database not ready yet
        print(f"  database not ready ({exc.__class__.__name__}), retrying...")
        time.sleep(2)
sys.exit("Database did not become available in time.")
PY

# Only the web container migrates the database; helper containers (such as
# the Outlook synchronisation) set RUN_MIGRATIONS=0 to avoid running the same
# migration twice at the same time.
if [ "${RUN_MIGRATIONS:-1}" != "0" ]; then
    echo "Applying database migrations..."
    flask db upgrade
    flask ensure-admin
fi

exec "$@"
