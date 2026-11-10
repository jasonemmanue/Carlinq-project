from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.clock import utcnow
from app.core.errors import Conflict
from app.db.session import get_db
from app.models import Notification, Ride, RideStatus, User
from app.schemas import NotificationOut, UserOut, UserUpdateIn
from app.services.audit import audit

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me", response_model=UserOut, summary="Profil, langue, domicile (Retour maison)")
def update_me(payload: UserUpdateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    if data.get("email") and db.execute(select(User.id).where(User.email == data["email"],
                                                              User.id != user.id)).first():
        raise Conflict("ACCOUNT_EXISTS", "Email deja utilise")
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    return user


@router.delete("/me", status_code=204, summary="Suppression du compte (anonymisation)")
def delete_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    active = (RideStatus.pending, RideStatus.accepted, RideStatus.in_progress)
    clauses = [Ride.passenger_id == user.id]
    if user.driver:
        clauses.append(Ride.driver_id == user.driver.id)
    if db.execute(select(Ride.id).where(or_(*clauses), Ride.status.in_(active))).first():
        raise Conflict("RIDE_ALREADY_ACTIVE", "Terminez ou annulez votre course avant de supprimer le compte")
    # Les courses et ecritures restent (comptabilite, arbitrage) ; les donnees
    # personnelles sont anonymisees.
    if user.driver:
        user.driver.online = False
    user.deleted_at = utcnow()
    user.is_active = False
    user.full_name = "Compte supprime"
    user.email = None
    user.phone = f"deleted-{user.id}"
    user.home_address = user.home_lat = user.home_lng = None
    audit(db, user.id, "user.delete", "users", user.id)
    db.commit()


@router.get("/me/notifications", response_model=list[NotificationOut])
def my_notifications(unread_only: bool = False, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return db.execute(stmt.order_by(Notification.id.desc()).limit(limit)).scalars().all()


@router.post("/me/notifications/read", status_code=204, summary="Marquer toutes les notifications comme lues")
def read_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = utcnow()
    for n in db.execute(select(Notification).where(Notification.user_id == user.id,
                                                   Notification.read_at.is_(None))).scalars():
        n.read_at = now
    db.commit()
