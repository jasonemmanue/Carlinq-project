"""Grand livre des portefeuilles : chaque mouvement cree une ecriture WalletTx.

Le solde de l'utilisateur est verrouille (SELECT ... FOR UPDATE) le temps de
l'ecriture, pour que deux mouvements simultanes ne se perdent pas.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, Invalid, ProviderUnavailable
from app.models import User, WalletTx


def require_payment_provider() -> None:
    """Aucun mouvement Mobile Money tant qu'aucun operateur ne confirme le paiement (Phase 2, CinetPay)."""
    if not settings.PAYMENTS_SIMULATED:
        raise ProviderUnavailable("PAYMENT_PROVIDER_UNAVAILABLE", "Le paiement Mobile Money n'est pas encore disponible")


def _lock_user(db: Session, user_id: int) -> User:
    return db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one()


def post(db: Session, user_id: int, amount_xaf: int, kind: str, *, label: str | None = None,
         ride_id: int | None = None, channel: str | None = None,
         goal_settlement_id: int | None = None, allow_negative: bool = False) -> WalletTx:
    if not isinstance(amount_xaf, int) or isinstance(amount_xaf, bool):
        raise Invalid("AMOUNT_NOT_INTEGER", "Les montants sont des entiers XAF")
    user = _lock_user(db, user_id)
    new_balance = user.wallet_balance_xaf + amount_xaf
    if new_balance < 0 and not allow_negative:
        raise Conflict("INSUFFICIENT_BALANCE", "Solde insuffisant",
                       balance_xaf=user.wallet_balance_xaf, required_xaf=-amount_xaf)
    user.wallet_balance_xaf = new_balance
    tx = WalletTx(user_id=user_id, amount_xaf=amount_xaf, balance_after_xaf=new_balance, kind=kind,
                  label=label, ride_id=ride_id, channel=channel, goal_settlement_id=goal_settlement_id)
    db.add(tx)
    db.flush()
    return tx
