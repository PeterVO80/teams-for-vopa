"""Two-step login with an authenticator app for local accounts (services/mfa.py, blueprints/auth.py)."""

import re
import time

import pyotp
import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from app.blueprints.auth import PENDING, SETUP_SECRET
from app.extensions import db
from app.models import User
from app.services import mfa


@pytest.fixture()
def required(app):
    app.config["MFA_REQUIRED"] = True
    return app


def user(username):
    return db.session.scalar(sa.select(User).filter_by(username=username))


def password_login(client, username="lies", password="demo1234"):
    return client.post("/auth/login", data={"username": username, "password": password})


def recovery_codes(html: str) -> list[str]:
    return re.findall(r'user-select-all">([a-z2-9]{5}-[a-z2-9]{5})<', html)


def setup_secret(client) -> str:
    with client.session_transaction() as session:
        return mfa.decrypt(session[SETUP_SECRET])


def code_at(secret: str, offset: int = 0) -> str:
    """The code of the 30-second step ``offset`` steps from now."""
    return pyotp.TOTP(secret).at(time.time() + 30 * offset)


def enrol(app, username="lies") -> str:
    """Set up two-step login for ``username`` through the login flow; returns the secret."""
    client = app.test_client()
    assert password_login(client, username).headers["Location"].endswith("/auth/mfa/setup")
    client.get("/auth/mfa/setup")
    secret = setup_secret(client)
    assert "Herstelcodes" in client.post("/auth/mfa/setup", data={"code": code_at(secret)}).get_data(as_text=True)
    return secret


def test_first_login_forces_setup_with_qr_code(required):
    client = required.test_client()
    response = password_login(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/auth/mfa/setup")
    assert client.get("/").status_code == 302                         # password alone: not logged in

    html = client.get("/auth/mfa/setup").get_data(as_text=True)
    assert 'src="data:image/svg+xml' in html and "Microsoft Authenticator" in html
    secret = setup_secret(client)
    assert " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)) in html     # typed by hand
    response = client.post("/auth/mfa/setup", data={"code": "000000" if code_at(secret) != "000000" else "111111"})
    assert "klopt niet" in response.get_data(as_text=True) and client.get("/").status_code == 302

    html = client.post("/auth/mfa/setup", data={"code": code_at(secret)}).get_data(as_text=True)
    codes = recovery_codes(html)
    assert len(codes) == 10 and "Tweestapsverificatie is ingesteld" in html
    assert client.get("/").status_code == 200                          # logged in now
    lies = user("lies")
    assert lies.mfa_enabled and secret not in lies.mfa_secret            # stored encrypted
    assert mfa.decrypt(lies.mfa_secret) == secret
    assert all(code.replace("-", "") not in lies.mfa_recovery_codes for code in codes)   # hashed


def test_login_with_code_and_replay_protection(required):
    secret = enrol(required)
    client = required.test_client()
    assert password_login(client).headers["Location"].endswith("/auth/mfa")
    assert client.get("/").status_code == 302
    response = client.post("/auth/mfa", data={"code": "12345"})
    assert "Ongeldige" in response.get_data(as_text=True)
    code = code_at(secret, 1)                                           # (the current one was used at setup)
    assert client.post("/auth/mfa", data={"code": code}).status_code == 302
    assert client.get("/").status_code == 200
    # The same code can't be used a second time.
    other = required.test_client()
    password_login(other)
    assert "Ongeldige" in other.post("/auth/mfa", data={"code": code}).get_data(as_text=True)


def test_recovery_code_works_once(required):
    enrol(required)
    lies = user("lies")
    codes = mfa.new_recovery_codes(lies)
    db.session.commit()
    client = required.test_client()
    password_login(client)
    response = client.post("/auth/mfa", data={"code": codes[0].upper()}, follow_redirects=True)
    assert "herstelcode gebruikt" in response.get_data(as_text=True) and "nog 9" in response.get_data(as_text=True)
    again = required.test_client()
    password_login(again)
    assert "Ongeldige" in again.post("/auth/mfa", data={"code": codes[0]}).get_data(as_text=True)


def test_wrong_codes_block_the_login(required):
    secret = enrol(required)
    client = required.test_client()
    password_login(client)
    for _ in range(5):
        client.post("/auth/mfa", data={"code": "999999"})
    assert client.post("/auth/mfa", data={"code": code_at(secret, 1)}).status_code == 429
    # Logging in again (right password) is refused as well: it doesn't reset the counter.
    assert password_login(required.test_client()).status_code == 429


def test_second_step_needs_a_recent_password(required):
    enrol(required)
    client = required.test_client()
    assert client.get("/auth/mfa").headers["Location"].endswith("/auth/login")
    password_login(client)
    with client.session_transaction() as session:
        pending = session[PENDING]
        pending["at"] -= 3600                                            # an hour ago
        session[PENDING] = pending
    assert client.get("/auth/mfa").headers["Location"].endswith("/auth/login")


def test_admin_reset_and_cli_reset(client, required):          # (admin logged in before MFA is required)
    secret = enrol(required)
    lies = user("lies")
    html = client.get(f"/users/{lies.id}/edit").get_data(as_text=True)
    assert "Tweestapsverificatie" in html and "Resetten (nieuwe gsm)" in html
    client.post(f"/users/{lies.id}/reset-mfa")
    db.session.refresh(lies)
    assert not lies.mfa_enabled and lies.mfa_recovery_codes is None
    other = required.test_client()
    assert password_login(other).headers["Location"].endswith("/auth/mfa/setup")      # set up again

    enrol(required)
    result = CliRunner().invoke(required.cli, ["reset-mfa", "lies"])
    assert result.exit_code == 0 and "gereset" in result.output
    db.session.refresh(lies)
    assert not lies.mfa_enabled
    assert secret                                                       # (used above)


def test_manage_page_new_recovery_codes_and_new_phone(required):
    secret = enrol(required)
    client = required.test_client()
    password_login(client)
    client.post("/auth/mfa", data={"code": code_at(secret, 1)})
    html = client.get("/auth/mfa/manage").get_data(as_text=True)
    assert "Actief" in html and "Nog <strong>10</strong>" in html
    assert "Ongeldige code" in client.post("/auth/mfa/manage", data={"code": "000001"}).get_data(as_text=True)
    html = client.post("/auth/mfa/manage", data={"code": code_at(secret, -1)}).get_data(as_text=True)
    assert len(recovery_codes(html)) == 10
    # New phone: a new secret replaces the old one after confirmation.
    assert "nieuwe" in client.get("/auth/mfa/setup").get_data(as_text=True)
    new_secret = setup_secret(client)
    client.post("/auth/mfa/setup", data={"code": code_at(new_secret)})
    assert mfa.decrypt(user("lies").mfa_secret) == new_secret


def test_not_required_but_enabled_still_asks_the_code(app):
    enrol_app = app
    enrol_app.config["MFA_REQUIRED"] = True
    secret = enrol(enrol_app)
    enrol_app.config["MFA_REQUIRED"] = False
    client = app.test_client()
    assert password_login(client).headers["Location"].endswith("/auth/mfa")
    assert password_login(app.test_client(), "piet").status_code in (302, 200)          # others: password only
    assert secret


def test_changed_secret_key_invalidates_secret(app):
    with app.test_request_context():
        token = mfa.encrypt("JBSWY3DPEHPK3PXP")
        assert mfa.decrypt(token) == "JBSWY3DPEHPK3PXP"
        app.config["SECRET_KEY"] = "another-key"
        assert mfa.decrypt(token) is None
