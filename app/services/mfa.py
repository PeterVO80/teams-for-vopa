"""Two-step login for local accounts: codes from an authenticator app (TOTP).

Logging in with a user name and password is followed by a second step: the
6-digit code the user's authenticator app (Microsoft Authenticator, Google
Authenticator, a password manager ...) shows, which changes every 30 seconds
(RFC 6238). With ``MFA_REQUIRED`` (the default) every local account must set
it up: at the next login the app shows a QR code to scan first.
"Aanmelden met Microsoft" is not affected: Microsoft does its own MFA.

* The secret of every user is stored **encrypted** (Fernet, key derived from
  ``SECRET_KEY``), so a database back-up alone doesn't reveal it. Changing
  ``SECRET_KEY`` therefore invalidates the secrets: users set up again (an
  administrator resets them, or ``flask reset-mfa``).
* A code can be used only once (``mfa_last_step``): an intercepted code can't
  be replayed.
* 10 one-time **recovery codes** for a lost phone, stored hashed like passwords.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from ..models.base import utcnow

ISSUER = "VOPA Teams"
RECOVERY_CODES = 10
#: Accept the previous and the next 30-second code too (clock differences of the phone).
VALID_WINDOW = 1


# ------------------------------------------------------------------ settings
def required() -> bool:
    """Must every local account use two-step login? (config MFA_REQUIRED)"""
    return bool(current_app.config.get("MFA_REQUIRED"))


def needed_for(user) -> bool:
    """Does logging in with a password need the second step for ``user``?"""
    return required() or user.mfa_enabled


# ------------------------------------------------------------------ encryption
def _fernet() -> Fernet:
    digest = hashlib.sha256(("vopa-teams-mfa:" + current_app.config["SECRET_KEY"]).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """The plain secret, or None when it can't be decrypted (e.g. SECRET_KEY changed)."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ------------------------------------------------------------------ setup
def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """The QR code as an SVG data URI (no external service, allowed by the CSP: img-src data:)."""
    return segno.make(uri, error="m").svg_data_uri(scale=6, border=2)


def grouped(secret: str) -> str:
    """"JBSW Y3DP EHPK 3PXP" - easier to type by hand than one long string."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ------------------------------------------------------------------ codes
def _clean(code: str | None) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).lower()


def _matching_step(secret: str, code: str) -> int | None:
    """The 30-second time step whose code is ``code`` (within the window), else None."""
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // totp.interval
    for step in range(now - VALID_WINDOW, now + VALID_WINDOW + 1):
        if secrets.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def check_new_secret(secret: str, code: str | None) -> int | None:
    """While setting up: does ``code`` belong to ``secret``? Returns the time step."""
    return _matching_step(secret, _clean(code))


def confirm(user, code: str | None) -> bool:
    """A logged-in user confirms an action with a current app code (no replay check needed)."""
    secret = decrypt(user.mfa_secret)
    return bool(secret) and _matching_step(secret, _clean(code)) is not None


def verify(user, code: str | None) -> str | None:
    """Check a login code: "totp", "recovery" (a recovery code was used up) or None.

    Updates the user (last used step / remaining recovery codes); the caller commits.
    """
    code = _clean(code)
    secret = decrypt(user.mfa_secret)
    if secret:
        step = _matching_step(secret, code)
        if step is not None and step > (user.mfa_last_step or 0):     # each code only once
            user.mfa_last_step = step
            return "totp"
    hashes = json.loads(user.mfa_recovery_codes or "[]")
    for stored in hashes:
        if len(code) == 10 and check_password_hash(stored, code):
            hashes.remove(stored)
            user.mfa_recovery_codes = json.dumps(hashes)
            return "recovery"
    return None


def remaining_recovery_codes(user) -> int:
    return len(json.loads(user.mfa_recovery_codes or "[]"))


def new_recovery_codes(user) -> list[str]:
    """Replace the recovery codes; returns them in plain text (shown once)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"          # no 0/o, 1/l/i look-alikes
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_CODES)]
    user.mfa_recovery_codes = json.dumps([generate_password_hash(c, method="pbkdf2:sha256:100000") for c in codes])
    return [f"{c[:5]}-{c[5:]}" for c in codes]


def enable(user, secret: str, step: int) -> list[str]:
    """Store the confirmed secret; returns new recovery codes."""
    user.mfa_secret = encrypt(secret)
    user.mfa_enabled_at = utcnow()
    user.mfa_last_step = step
    return new_recovery_codes(user)


def reset(user) -> None:
    """Remove two-step login of ``user`` (new phone); set up again at the next login."""
    user.mfa_secret = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_recovery_codes = None
