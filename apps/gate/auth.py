"""Identity and authorization.

Cloudflare Access sits in front of the gate. Every request must carry a valid
`Cf-Access-Jwt-Assertion` (RS256, audience = CF_ACCESS_AUD, issuer = team domain).
The email claim (never the plain header alone) is used for owner checks.
Agents may instead use `Authorization: Bearer <AGENT_TOKEN>`; they are treated as owners.
"""
from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import jwt

from .config import Settings


class AuthError(Exception):
    def __init__(self, status: int, code: str, msg: str):
        super().__init__(msg)
        self.status, self.code, self.msg = status, code, msg


@dataclass(frozen=True)
class Identity:
    email: str
    kind: str  # "user" | "agent" | "dev"
    is_owner: bool


class JWKSCache:
    """Fetches and caches Cloudflare Access signing keys (certs endpoint)."""

    def __init__(self, team_domain: str, fetch: Callable[[str], Mapping[str, Any]], ttl: float = 3600):
        self.url = f"https://{team_domain}/cdn-cgi/access/certs"
        self.fetch, self.ttl = fetch, ttl
        self._keys: dict[str, Any] = {}
        self._at = 0.0

    def key(self, kid: str):
        if kid not in self._keys or time.time() - self._at > self.ttl:
            data = self.fetch(self.url)
            self._keys = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(k) for k in data.get("keys", [])}
            self._at = time.time()
        if kid not in self._keys:
            raise AuthError(401, "unknown_kid", "Access token signed by unknown key")
        return self._keys[kid]


def verify_access_jwt(token: str, settings: Settings, keys: JWKSCache, leeway: int = 30) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise AuthError(401, "bad_token", "malformed Access token") from e
    if header.get("alg") != "RS256":
        raise AuthError(401, "bad_alg", "unexpected token algorithm")
    try:
        return jwt.decode(
            token,
            key=keys.key(header.get("kid", "")),
            algorithms=["RS256"],
            audience=settings.cf_access_aud,
            issuer=f"https://{settings.cf_access_team_domain}",
            leeway=leeway,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except AuthError:
        raise
    except jwt.PyJWTError as e:
        raise AuthError(401, "invalid_token", f"Access token rejected: {type(e).__name__}") from e


def is_owner(email: str, settings: Settings) -> bool:
    # empty OWNER_EMAILS => every authenticated user is an owner
    return not settings.owner_emails or email.lower() in settings.owner_emails


def identify(headers: Mapping[str, str], settings: Settings, keys: JWKSCache | None, agent_token: str = "") -> Identity:
    auth = headers.get("Authorization", "")
    if agent_token and auth.startswith("Bearer "):
        if hmac.compare_digest(auth[7:].strip(), agent_token):
            return Identity("agent", "agent", True)
        raise AuthError(401, "bad_agent_token", "invalid bearer token")
    if settings.auth_disabled:
        return Identity("dev@localhost", "dev", True)
    token = headers.get("Cf-Access-Jwt-Assertion") or ""
    if not token:
        raise AuthError(401, "no_access_token", "missing Cloudflare Access token")
    if keys is None:
        raise AuthError(500, "no_jwks", "JWKS not configured")
    claims = verify_access_jwt(token, settings, keys)
    email = str(claims.get("email") or "").lower()
    if not email:
        # service tokens have no email; they are only for the backend hostname
        raise AuthError(403, "no_email", "token has no user email")
    if settings.allowed_emails and email not in settings.allowed_emails:
        raise AuthError(403, "not_allowed", "email not in ALLOWED_EMAILS")
    return Identity(email, "user", is_owner(email, settings))


def authorize(identity: Identity, action: str, settings: Settings) -> None:
    if action in settings.owner_only_actions and not identity.is_owner:
        raise AuthError(403, "owner_only", f"'{action}' is restricted to owners")
