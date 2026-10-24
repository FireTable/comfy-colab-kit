"""Identity and authorization.

Cloudflare Access sits in front of the gate. Every request must carry a valid
`Cf-Access-Jwt-Assertion` (RS256, audience = CF_ACCESS_AUD, issuer = team domain).
The email claim (never the plain header alone) is used for owner checks.
Agents may instead use `Authorization: Bearer <AGENT_TOKEN>`; they are treated as owners.
"""
from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TYPE_CHECKING

import jwt
from aiohttp import web

from .config import Settings

if TYPE_CHECKING:
    from .db import GateDB

log = logging.getLogger("gate.auth")
IDENT = web.RequestKey("identity", object) if hasattr(web, "RequestKey") else "identity"


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


def is_owner(email: str, settings: Settings, db: GateDB | None = None) -> bool:
    norm = email.lower()
    # empty OWNER_EMAILS => every authenticated user is an owner
    if not settings.owner_emails:
        return True
    if norm in settings.owner_emails:
        return True
    if db and norm in db.list_owners():
        return True
    return False


def identify(headers: Mapping[str, str], settings: Settings, keys: JWKSCache | None,
             agent_token: str = "", query_token: str = "", cookie_token: str = "",
             db: GateDB | None = None, cf_cookie: str = "") -> Identity:
    token_candidate = None
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token_candidate = auth[7:].strip()
    elif query_token:
        token_candidate = query_token.strip()
    elif cookie_token:
        token_candidate = cookie_token.strip()

    if token_candidate:
        if db:
            tok_info = db.verify_token(token_candidate)
            if tok_info:
                email = tok_info["email"]
                is_own = is_owner(email, settings, db)
                return Identity(email, "agent", is_own)
        if agent_token and hmac.compare_digest(token_candidate, agent_token):
            return Identity("agent", "agent", True)
        raise AuthError(401, "bad_agent_token", "invalid bearer or access token")
    if settings.auth_disabled:
        is_own = is_owner("dev@localhost", settings, db)
        return Identity("dev@localhost", "dev", is_own)
    token = headers.get("Cf-Access-Jwt-Assertion") or cf_cookie or ""
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
        log.warning("Access denied: email '%s' not in ALLOWED_EMAILS (%s)", email, settings.allowed_emails)
        raise AuthError(403, "not_allowed", f"邮箱 '{email}' 未在允许访问列表 (ALLOWED_EMAILS) 中")
    return Identity(email, "user", is_owner(email, settings, db))


def authorize(identity: Identity, action: str, settings: Settings) -> None:
    if action in settings.owner_only_actions and not identity.is_owner:
        raise AuthError(403, "owner_only", f"'{action}' is restricted to owners")
