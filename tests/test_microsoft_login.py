"""Log in with Microsoft 365 (OpenID Connect), with a fake token endpoint."""

import base64
import json
import time
import urllib.parse

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import User
from app.services import microsoft_login

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def ms(app, monkeypatch):
    app.config.update(MS365_TENANT_ID=TENANT, MS365_CLIENT_ID="client-1", MS365_CLIENT_SECRET="s",
                      MICROSOFT_LOGIN="on", APP_URL="https://teams.example.com")
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    jan.email = "Jan@vopa.be"
    db.session.commit()
    sent = {}

    def fake_token(data):
        sent.update(data)
        return {"id_token": _token(**sent.get("_claims", {}))}

    monkeypatch.setattr(microsoft_login, "_post_token", fake_token)
    return sent


def _token(**overrides):
    claims = {"aud": "client-1", "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "tid": TENANT,
              "oid": "oid-jan", "preferred_username": "jan@vopa.be", "exp": time.time() + 600,
              "nonce": overrides.pop("_nonce", None)}
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


def _start(client):
    response = client.get("/auth/microsoft?next=/team/")
    assert response.status_code == 302
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(response.location).query))
    return response.location, query


def _finish(client, ms, query, **claims):
    claims.setdefault("_nonce", query["nonce"])
    ms["_claims"] = claims
    return client.get(f"/auth/microsoft/callback?code=abc&state={query['state']}")


def test_login_page_shows_microsoft_button(app, ms):
    html = app.test_client().get("/auth/login").get_data(as_text=True)
    assert "Aanmelden met Microsoft" in html and 'name="password"' in html


def test_button_hidden_when_off(app):
    html = app.test_client().get("/auth/login").get_data(as_text=True)
    assert "Aanmelden met Microsoft" not in html
    assert app.test_client().get("/auth/microsoft").status_code == 404


def test_redirect_to_microsoft_with_pkce(app, ms):
    location, query = _start(app.test_client())
    assert location.startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?")
    assert query["client_id"] == "client-1" and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "https://teams.example.com/auth/microsoft/callback"
    assert "openid" in query["scope"]


def test_login_links_account_by_email(app, ms):
    client = app.test_client()
    _, query = _start(client)
    response = _finish(client, ms, query)
    assert response.status_code == 302 and response.location.endswith("/team/")
    assert ms["code_verifier"] and ms["grant_type"] == "authorization_code"
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    assert jan.ms_oid == "oid-jan"
    assert client.get("/").status_code == 200                                # logged in

    # Next time the object id is used, even if the e-mail address in Microsoft changed.
    client = app.test_client()
    _, query = _start(client)
    assert _finish(client, ms, query, preferred_username="j.peeters@vopa.be").status_code == 302
    assert client.get("/").status_code == 200


def test_unknown_account_gets_no_access(app, ms):
    client = app.test_client()
    _, query = _start(client)
    response = _finish(client, ms, query, oid="oid-x", preferred_username="stranger@vopa.be")
    assert response.location.endswith("/auth/login")
    assert "geen gebruiker in VOPA Teams voor stranger@vopa.be" in client.get("/auth/login").get_data(as_text=True)
    assert client.get("/").status_code == 302                                # not logged in


def test_inactive_user_refused(app, ms):
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    jan.active = False
    db.session.commit()
    client = app.test_client()
    _, query = _start(client)
    _finish(client, ms, query)
    assert "gedeactiveerd" in client.get("/auth/login").get_data(as_text=True)


@pytest.mark.parametrize("claims", [
    {"aud": "other-app"},
    {"tid": "99999999-2222-3333-4444-555555555555",
     "iss": "https://login.microsoftonline.com/99999999-2222-3333-4444-555555555555/v2.0"},
    {"exp": time.time() - 10},
    {"_nonce": "wrong"},
])
def test_invalid_tokens_are_refused(app, ms, claims):
    client = app.test_client()
    _, query = _start(client)
    _finish(client, ms, query, **claims)
    assert client.get("/").status_code == 302


def test_wrong_state_is_refused(app, ms):
    client = app.test_client()
    _start(client)
    response = client.get("/auth/microsoft/callback?code=abc&state=forged")
    assert response.location.endswith("/auth/login")
    assert client.get("/").status_code == 302


def test_microsoft_error_is_shown(app, ms):
    client = app.test_client()
    _start(client)
    client.get("/auth/microsoft/callback?error=access_denied&error_description=User+cancelled")
    assert "User cancelled" in client.get("/auth/login").get_data(as_text=True)


def test_required_mode_only_admins_use_passwords(app, ms):
    app.config["MICROSOFT_LOGIN"] = "required"
    client = app.test_client()
    html = client.get("/auth/login").get_data(as_text=True)
    assert "Beheerder: aanmelden met wachtwoord" in html
    response = client.post("/auth/login", data={"username": "jan", "password": "demo1234"})
    assert response.status_code == 200 and "Meld je aan met je Microsoft-account" in response.get_data(as_text=True)
    assert client.get("/").status_code == 302
    admin = app.test_client()
    assert admin.post("/auth/login", data={"username": "admin", "password": "secret123"}).status_code == 302


def test_user_without_password_when_microsoft_login_is_on(app, ms, client):
    response = client.post("/users/new", data={"username": "nieuw", "full_name": "Nieuwe Collega",
                                               "email": "nieuw@vopa.be", "role": "member", "color": "#0d6efd",
                                               "active": "y"})
    assert response.status_code == 302
    user = db.session.scalar(sa.select(User).filter_by(username="nieuw"))
    assert user.password_hash and not user.check_password("")


def test_changing_email_unlinks_microsoft_account(app, ms, client):
    jan = db.session.scalar(sa.select(User).filter_by(username="jan"))
    jan.ms_oid = "oid-jan"
    db.session.commit()
    client.post(f"/users/{jan.id}/edit", data={"username": "jan", "full_name": jan.full_name,
                                               "email": "andere@vopa.be", "role": jan.role.value,
                                               "color": jan.color, "active": "y"})
    db.session.refresh(jan)
    assert jan.ms_oid is None
