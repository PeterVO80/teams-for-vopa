# ---------------------------------------------------------------------------
# VOPA Teams - production image
#   docker build -t vopa-teams .
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Python behaviour: no .pyc files, unbuffered logs (visible in `docker logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi.py

WORKDIR /app

# Install dependencies first: this layer is cached as long as
# requirements.txt does not change, which makes rebuilds fast.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user instead of root.
RUN useradd --create-home --uid 1000 appuser \
    && chmod +x docker/entrypoint.sh \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# The entrypoint waits for the database and applies migrations,
# then executes the CMD below.
ENTRYPOINT ["docker/entrypoint.sh"]
# --timeout 600: loading a complete supplier price list (hundreds of
# thousands of articles) can take a few minutes.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "600", "--access-logfile", "-", "wsgi:app"]
