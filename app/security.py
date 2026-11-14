"""Security settings for running the application on the internet.

* ``PROXY_COUNT`` (1 behind Caddy, implied by ``HTTPS=1``): trusts the
  ``X-Forwarded-*`` headers of that many proxies (real client IP for the
  login protection, ``https`` scheme for redirects/cookies).
* ``HTTPS=1`` (on by default when ``DOMAIN`` is set):
  - sends cookies only over HTTPS (session and "remember me");
  - refuses to start with a missing or weak ``SECRET_KEY``.
* Always: security headers on every response, including a Content Security
  Policy that only allows the application's own scripts. Inline ``<script>``
  blocks in templates must carry ``nonce="{{ csp_nonce() }}"``.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from flask import Flask, g
from werkzeug.middleware.proxy_fix import ProxyFix

DEV_SECRET = "dev-insecure-secret-key"


def csp_nonce() -> str:
    """Random value per request that marks the page's own inline scripts."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


def init_security(app: Flask) -> None:
    config = app.config
    config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=12))
    config.setdefault("REMEMBER_COOKIE_DURATION", timedelta(days=14))
    config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  REMEMBER_COOKIE_HTTPONLY=True, REMEMBER_COOKIE_SAMESITE="Lax")

    if config.get("HTTPS"):
        secret = config.get("SECRET_KEY") or ""
        if secret == DEV_SECRET or len(secret) < 32:
            raise RuntimeError("SECRET_KEY ontbreekt of is te kort (minstens 32 tekens). "
                               "Maak er een op de server met: openssl rand -hex 32 "
                               "en vul die in bij de variabelen van de stack.")
        config.update(SESSION_COOKIE_SECURE=True, REMEMBER_COOKIE_SECURE=True, PREFERRED_URL_SCHEME="https")

    proxies = int(config.get("PROXY_COUNT") or (1 if config.get("HTTPS") else 0))
    if proxies:
        # Caddy in front of the app: trust its X-Forwarded-For/-Proto/-Host
        # (and only those of that many proxies, so clients can't fake them).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    app.jinja_env.globals["csp_nonce"] = csp_nonce

    @app.after_request
    def security_headers(response):
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Content-Security-Policy", "; ".join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{csp_nonce()}'",
            "style-src 'self' 'unsafe-inline'",          # style="" attributes (planning colours)
            "img-src 'self' data: blob: https:",          # images in notes / wiki
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]))
        if config.get("HTTPS"):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Pages of logged-in users may hold business data: don't keep them in shared caches.
        if response.mimetype == "text/html":
            headers.setdefault("Cache-Control", "no-store")
        return response
