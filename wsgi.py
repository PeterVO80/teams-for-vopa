"""WSGI entry point used by gunicorn (``gunicorn wsgi:app``) and ``flask``."""

from app import create_app

app = create_app()
