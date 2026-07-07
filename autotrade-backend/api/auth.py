"""Admin authentication — single-user JWT login.

POST /api/v1/auth/login  → { access_token, token_type }
GET  /api/v1/auth/me     → { email } (requires Bearer token)
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

router = APIRouter(tags=["auth"])

# ── Credentials — loaded from env / .env file (never hardcoded) ───────────────
_ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL",    "")
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
_JWT_SECRET     = os.getenv("JWT_SECRET",     "")
_JWT_ALGORITHM  = "HS256"
_JWT_EXPIRE_DAYS = 30

# Pre-hash the password once at startup so comparisons are constant-time.
_HASHED_PW: bytes = bcrypt.hashpw(_ADMIN_PASSWORD.encode(), bcrypt.gensalt())


# ── Schemas ───────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email:    str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"

class MeResponse(BaseModel):
    email: str


# ── Helpers ───────────────────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

def _make_token(email: str) -> str:
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=_JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)

def _verify_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """FastAPI dependency: enforce a valid admin JWT on a route.

    Use as `Depends(require_auth)` on sensitive/mutating endpoints (real-order
    placement, PAPER↔LIVE switch, manual agent trigger). Returns the caller's
    email or raises 401. The SPA already attaches the token to every request.
    """
    return _verify_token(credentials)


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    email_ok = body.email.strip().lower() == _ADMIN_EMAIL.lower()
    pw_ok    = bcrypt.checkpw(body.password.encode(), _HASHED_PW)
    if not (email_ok and pw_ok):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=_make_token(_ADMIN_EMAIL))


@router.get("/me", response_model=MeResponse)
async def me(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    email = _verify_token(credentials)
    return MeResponse(email=email)
