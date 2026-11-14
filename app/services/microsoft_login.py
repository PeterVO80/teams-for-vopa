"""Log in with the Microsoft 365 (Entra ID) account - OpenID Connect.

Uses the same app registration as the Outlook link (``MS365_TENANT_ID``,
``MS365_CLIENT_ID``, ``MS365_CLIENT_SECRET``). Because Microsoft handles the
sign-in, the company's Microsoft security applies to this application too:
two-step verification (Authenticator), conditional access, and a disabled
Microsoft account can no longer log in.

Flow (authorization code + PKCE, see :func:`authorize_url` and
:func:`complete_login`):

1. ``/auth/microsoft`` stores a random ``state``, ``nonce`` and PKCE verifier
   in the (signed) session and redirects to Microsoft.
2. Microsoft redirects back to ``/auth/microsoft/callback?code=...&state=...``.
3. The code is exchanged for an ID token directly with Microsoft's token
   endpoint (TLS, client secret). Per OpenID Connect Core 3.1.3.7 the TLS
   connection authenticates the token, so the claims are checked
   (audience, issuer, tenant, expiry, nonce) without a JWT library.
4. The Microsoft account is matched to a VOPA Teams user: first by its
   object id (``User.ms_oid``), the first time by e-mail address. Unknown
   people get no access - an administrator creates the user first.

``MICROSOFT_LOGIN`` setting:

* ``off`` (default) - only user name + password;
* ``on`` - "Aanmelden met Microsoft" button, passwords still work;
* ``required`` - only Microsoft for everyone, except administrators, who
  keep their password as an emergency entrance (e.g. Microsoft outage).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import sqlalchemy as sa
from flask import current_app, url_for

from ..extensions import db
from ..models import User
from . import outlook

MODES = ("off", "on", "required")


class LoginError(Exception):
    """Microsoft login failed; the message is shown to the user (Dutch)."""


def mode() -> str:
    value = (current_app.config.get("MICROSOFT_LOGIN") or "off").strip().lower()
    if value not in MODES or not outlook.is_configured():
        return "off"
    return value


def enabled() -> bool:
    return mode() != "off"


def required() -> bool:
    return mode() == "required"


def redirect_uri() -> str:
    """Address Microsoft sends the user back to (must be registered in Entra)."""
    app_url = (current_app.config.get("APP_URL") or "").rstrip("/")
    if app_url:
        return app_url + url_for("auth.microsoft_callback")
    return url_for("auth.microsoft_callback", _external=True)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def authorize_url() -> tuple[str, dict]:
    """URL of Microsoft's sign-in page, plus the values to keep in the session."""
    s = outlook.settings()
    flow = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48)}
    query = urllib.parse.urlencode({
        "client_id": s["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "response_mode": "query",
        "scope": "openid profile email",
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": _b64url(hashlib.sha256(flow["verifier"].encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    return f"https://login.microsoftonline.com/{s['tenant']}/oauth2/v2.0/authorize?{query}", flow


def _post_token(data: dict) -> dict:
    """POST to Microsoft's token endpoint (separate function: replaced in tests)."""
    s = outlook.settings()
    request = urllib.request.Request(
        f"https://login.microsoftonline.com/{s['tenant']}/oauth2/v2.0/token",
        data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except ValueError:
            body = {}
        raise LoginError(f"Microsoft weigerde de aanmelding: {body.get('error_description', exc.code)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LoginError("Microsoft is niet bereikbaar, probeer het later opnieuw.") from exc


def _claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise LoginError("Ongeldig antwoord van Microsoft.") from exc


def complete_login(args: dict, flow: dict | None) -> User:
    """Handle Microsoft's redirect back: returns the VOPA Teams user to log in."""
    if args.get("error"):
        raise LoginError(f"Aanmelden bij Microsoft niet gelukt: {args.get('error_description') or args['error']}")
    if not flow or not args.get("state") or not secrets.compare_digest(args["state"], flow["state"]):
        raise LoginError("De aanmelding is verlopen of ongeldig. Probeer het opnieuw.")
    if not args.get("code"):
        raise LoginError("Ongeldig antwoord van Microsoft.")

    s = outlook.settings()
    token = _post_token({
        "client_id": s["client_id"], "client_secret": s["secret"], "grant_type": "authorization_code",
        "code": args["code"], "redirect_uri": redirect_uri(), "code_verifier": flow["verifier"],
        "scope": "openid profile email",
    })
    claims = _claims(token.get("id_token", ""))

    tid = claims.get("tid", "")
    if (claims.get("aud") != s["client_id"]
            or claims.get("iss") != f"https://login.microsoftonline.com/{tid}/v2.0"
            or claims.get("nonce") != flow["nonce"]
            or float(claims.get("exp", 0)) < time.time()):
        raise LoginError("Ongeldig antwoord van Microsoft.")
    if _looks_like_guid(s["tenant"]) and tid.lower() != s["tenant"].lower():
        raise LoginError("Dit Microsoft-account hoort niet bij jullie organisatie.")

    oid = claims.get("oid") or ""
    addresses = {a.strip().lower() for a in (claims.get("email"), claims.get("preferred_username")) if a}
    user = db.session.scalar(sa.select(User).where(User.ms_oid == oid)) if oid else None
    if user is None and addresses:
        user = db.session.scalar(sa.select(User).where(
            sa.func.lower(User.email).in_(addresses), User.ms_oid.is_(None)))
        if user is not None:
            user.ms_oid = oid      # from now on matched on the (unchangeable) object id
    if user is None:
        shown = next(iter(sorted(addresses)), "dit account")
        raise LoginError(f"Er is geen gebruiker in VOPA Teams voor {shown}. "
                         "Vraag een beheerder om je toe te voegen (met dit e-mailadres).")
    if not user.active:
        raise LoginError("Je account in VOPA Teams is gedeactiveerd.")
    return user


def _looks_like_guid(value: str) -> bool:
    parts = value.split("-")
    return [len(p) for p in parts] == [8, 4, 4, 4, 12]
