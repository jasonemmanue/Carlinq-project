from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized, current_user
from app.core import ratelimit
from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import Conflict, Forbidden
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, hash_password, verify_password,
)
from app.db.session import get_db
from app.models import User
from app.schemas import LoginIn, RefreshIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Compte inconnu : meme calcul bcrypt, pour que le temps de reponse ne le revele pas.
_DUMMY_HASH = hash_password("compte-inexistant")


def _tokens(user: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(user.id), refresh_token=create_refresh_token(user.id),
                    user=UserOut.model_validate(user))


@router.post("/signup", response_model=TokenOut, status_code=201,
             summary="Inscription Passager, Drivers ou Copilote")
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    clauses = [User.phone == payload.phone]
    if payload.email:
        clauses.append(User.email == payload.email)
    if db.execute(select(User.id).where(or_(*clauses))).first():
        raise Conflict("ACCOUNT_EXISTS", "Inscription impossible avec ces informations")
    user = User(full_name=payload.full_name, phone=payload.phone, email=payload.email,
                password_hash=hash_password(payload.password), role=payload.role,
                wallet_balance_xaf=0, rating_avg=5.0, rating_count=0, is_active=True, language="fr")
    db.add(user)
    db.commit()
    return _tokens(user)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    phone_key = f"login:{payload.phone}"
    ip_key = f"login-ip:{request.client.host if request.client else '?'}"
    ratelimit.check(phone_key, settings.LOGIN_MAX_FAILURES)
    ratelimit.check(ip_key, settings.LOGIN_MAX_FAILURES_PER_IP)
    user = db.execute(select(User).where(User.phone == payload.phone)).scalar_one_or_none()
    valid = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or user.deleted_at or not valid:
        ratelimit.fail(phone_key, settings.LOGIN_LOCK_SECONDS)
        ratelimit.fail(ip_key, settings.LOGIN_LOCK_SECONDS)
        raise Unauthorized("Telephone ou mot de passe incorrect")
    ratelimit.reset(phone_key)
    if not user.is_active:
        raise Forbidden("ACCOUNT_BANNED", "Compte desactive")
    if user.suspended_until and user.suspended_until > utcnow():
        raise Forbidden("ACCOUNT_SUSPENDED", "Compte suspendu", until=user.suspended_until.isoformat())
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, db: Session = Depends(get_db)):
    try:
        user_id = decode_token(payload.refresh_token, "refresh")
    except ValueError:
        raise Unauthorized("Jeton de rafraichissement invalide")
    user = db.get(User, user_id)
    if user is None or user.deleted_at or not user.is_active:
        raise Unauthorized("Compte introuvable")
    if user.suspended_until and user.suspended_until > utcnow():
        raise Forbidden("ACCOUNT_SUSPENDED", "Compte suspendu", until=user.suspended_until.isoformat())
    return _tokens(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
