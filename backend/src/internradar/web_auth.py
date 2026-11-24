"""Google sign-in (not Gmail OAuth) and server-side browser sessions."""

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import NAMESPACE_URL, UUID, uuid5

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from internradar.models import utcnow
from internradar.ownership import owner_session

COOKIE = "__Host-internradar"
ISSUER = "https://accounts.google.com"
MAX_AGE = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class WebSettings:
    origin: str
    client_id: str
    client_secret: str
    state_secret: str


@dataclass(frozen=True)
class Principal:
    id: UUID
    role: str


def _digest(token: str) -> bytes:
    return sha256(token.encode()).digest()


def create_session(
    engine: Engine, issuer: str, subject: str, previous_token: str | None = None
) -> str:
    owner = uuid5(NAMESPACE_URL, f"internradar:oidc:{issuer}:{subject}")
    token = token_urlsafe(32)
    with owner_session(engine, owner) as session:
        session.execute(
            text("""
            INSERT INTO account (id, identity_issuer, identity_subject)
            VALUES (:owner, :issuer, :subject) ON CONFLICT (id) DO NOTHING
        """),
            {"owner": owner, "issuer": issuer, "subject": subject},
        )
        session.execute(
            text("""
            INSERT INTO student_profile (owner_user_id, data) VALUES (:owner, '{}')
            ON CONFLICT (owner_user_id) DO NOTHING
        """),
            {"owner": owner},
        )
        session.execute(
            text("""
            DELETE FROM web_session WHERE owner_user_id = :owner AND expires_at <= now()
        """),
            {"owner": owner},
        )
        if previous_token and len(previous_token) == 43:
            session.execute(
                text("DELETE FROM web_session WHERE token_hash = :token"),
                {"token": _digest(previous_token)},
            )
        session.execute(
            text("""
            INSERT INTO web_session (token_hash, owner_user_id, expires_at)
            VALUES (:token, :owner, :expiry)
        """),
            {
                "token": _digest(token),
                "owner": owner,
                "expiry": utcnow() + timedelta(seconds=MAX_AGE),
            },
        )
    return token


def principal_for(engine: Engine, token: str | None) -> Principal:
    if not token or len(token) != 43:
        raise HTTPException(401, "Sign in required")
    with engine.connect() as conn:
        owner = conn.scalar(
            text("""
            SELECT owner_user_id FROM web_session WHERE token_hash = :token AND expires_at > now()
        """),
            {"token": _digest(token)},
        )
    if owner is not None:
        with owner_session(engine, owner) as session:
            role = session.execute(
                text("SELECT role FROM account WHERE id = :id"), {"id": owner}
            ).scalar_one_or_none()
        if role is not None:
            return Principal(owner, role)
    raise HTTPException(401, "Sign in required")


def install_sign_in(app: FastAPI, engine: Engine, settings: WebSettings) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.state_secret,
        session_cookie="__Host-ir-state",
        max_age=600,
        https_only=True,
    )
    oauth = OAuth()
    google = oauth.register(
        name="google",
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )

    @app.get("/auth/login")
    async def login(request: Request):
        return await google.authorize_redirect(request, settings.origin + "/auth/callback")

    @app.get("/auth/callback")
    async def callback(request: Request):
        try:
            result = await google.authorize_access_token(request)
        except OAuthError as exc:
            raise HTTPException(400, "Google sign-in could not be verified") from exc
        claims = result.get("userinfo")  # Authlib verifies signature, nonce, issuer and audience
        if not claims or claims.get("iss") != ISSUER or not claims.get("sub"):
            raise HTTPException(400, "Google sign-in could not be verified")
        token = await run_in_threadpool(
            create_session, engine, ISSUER, claims["sub"], request.cookies.get(COOKIE)
        )
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE, token, max_age=MAX_AGE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    @app.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token and len(token) == 43:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM web_session WHERE token_hash = :token"),
                    {"token": _digest(token)},
                )
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response
