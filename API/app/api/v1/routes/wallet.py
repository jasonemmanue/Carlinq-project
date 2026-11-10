from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.errors import Forbidden
from app.db.session import get_db
from app.models import User, UserRole, WalletTx
from app.schemas import TopupIn, WalletOut, WalletTxOut, WithdrawIn
from app.services import wallet
from app.services.settings import get_pricing

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("", response_model=WalletOut)
def balance(user: User = Depends(current_user), db: Session = Depends(get_db)):
    minimum = get_pricing(db).min_wallet_xaf
    return WalletOut(balance_xaf=user.wallet_balance_xaf, min_balance_xaf=minimum,
                     can_order=user.wallet_balance_xaf >= minimum)


@router.get("/transactions", response_model=list[WalletTxOut])
def transactions(kind: str | None = None, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    stmt = select(WalletTx).where(WalletTx.user_id == user.id)
    if kind:
        stmt = stmt.where(WalletTx.kind == kind)
    return db.execute(stmt.order_by(WalletTx.id.desc()).limit(limit)).scalars().all()


@router.post("/topup", response_model=WalletTxOut, status_code=201,
             summary="Recharge Orange Money / MTN MoMo (confirmation operateur en Phase 2)")
def topup(payload: TopupIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    wallet.require_payment_provider()
    tx = wallet.post(db, user.id, payload.amount_xaf, "topup", label=f"Recharge {payload.channel}",
                     channel=payload.channel)
    db.commit()
    return tx


@router.post("/withdraw", response_model=WalletTxOut, status_code=201,
             summary="Retrait des gains chauffeur (bonus et parts d'objectif inclus) vers Mobile Money")
def withdraw(payload: WithdrawIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    wallet.require_payment_provider()
    if user.role not in (UserRole.drivers, UserRole.copilote):
        raise Forbidden("WITHDRAW_DRIVERS_ONLY", "Le retrait est reserve aux chauffeurs")
    tx = wallet.post(db, user.id, -payload.amount_xaf, "withdrawal", label=f"Retrait vers {payload.channel}",
                     channel=payload.channel)
    db.commit()
    return tx
