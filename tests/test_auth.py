import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from gate.auth import AuthError, JWKSCache, authorize, identify
from gate.config import Settings

TEAM = "team.cloudflareaccess.com"
AUD = "aud-123"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "k1"
    return key, {"keys": [jwk]}


def settings(**kw):
    env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD, "ALLOWED_EMAILS": "a@x.com,b@x.com",
           "OWNER_EMAILS": "a@x.com"}
    env.update(kw)
    return Settings.from_env(env)


def token(key, **claims):
    now = int(time.time())
    body = {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now, "exp": now + 600, "email": "a@x.com"}
    body.update(claims)
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": "k1"})


def ident(key, jwks, tok=None, s=None, agent=""):
    cache = JWKSCache(TEAM, lambda url: jwks)
    h = {"Cf-Access-Jwt-Assertion": tok} if tok else {}
    return identify(h, s or settings(), cache, agent)


def test_valid_owner(keypair):
    key, jwks = keypair
    i = ident(key, jwks, token(key))
    assert i.email == "a@x.com" and i.is_owner


def test_valid_non_owner_cannot_stop_but_can_wake(keypair):
    key, jwks = keypair
    s = settings()
    i = ident(key, jwks, token(key, email="B@x.com"), s)
    assert not i.is_owner
    authorize(i, "wake", s)
    for action in ("stop", "switch", "extend"):
        with pytest.raises(AuthError) as e:
            authorize(i, action, s)
        assert e.value.status == 403


def test_owner_only_actions_configurable(keypair):
    key, jwks = keypair
    s = settings(OWNER_ONLY_ACTIONS="wake")
    i = ident(key, jwks, token(key, email="b@x.com"), s)
    authorize(i, "stop", s)
    with pytest.raises(AuthError):
        authorize(i, "wake", s)


def test_empty_owner_list_means_everyone_is_owner(keypair):
    key, jwks = keypair
    i = ident(key, jwks, token(key, email="b@x.com"), settings(OWNER_EMAILS=""))
    assert i.is_owner


@pytest.mark.parametrize("claims,code", [
    ({"aud": ["other"]}, "invalid_token"),
    ({"iss": "https://evil.example.com"}, "invalid_token"),
    ({"exp": int(time.time()) - 3600}, "invalid_token"),
    ({"email": "c@x.com"}, "not_allowed"),
    ({"email": ""}, "no_email"),
])
def test_rejections(keypair, claims, code):
    key, jwks = keypair
    with pytest.raises(AuthError) as e:
        ident(key, jwks, token(key, **claims))
    assert e.value.code == code


def test_wrong_signing_key(keypair):
    _, jwks = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        ident(other, jwks, token(other))


def test_hs256_downgrade_rejected(keypair):
    key, jwks = keypair
    bad = jwt.encode({"aud": AUD, "email": "a@x.com"}, "s" * 40, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(AuthError) as e:
        ident(key, jwks, bad)
    assert e.value.code == "bad_alg"


def test_missing_token(keypair):
    key, jwks = keypair
    with pytest.raises(AuthError) as e:
        ident(key, jwks, None)
    assert e.value.status == 401


def test_agent_bearer(keypair):
    key, jwks = keypair
    s = settings()
    cache = JWKSCache(TEAM, lambda url: jwks)
    i = identify({"Authorization": "Bearer abc"}, s, cache, "abc")
    assert i.kind == "agent" and i.is_owner
    with pytest.raises(AuthError):
        identify({"Authorization": "Bearer nope"}, s, cache, "abc")


def test_plain_email_header_is_not_trusted(keypair):
    key, jwks = keypair
    cache = JWKSCache(TEAM, lambda url: jwks)
    with pytest.raises(AuthError):
        identify({"Cf-Access-Authenticated-User-Email": "a@x.com"}, settings(), cache)
