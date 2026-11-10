from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_driver, current_user, passenger_only
from app.core.errors import Conflict, Invalid
from app.db.session import get_db
from app.models import Driver, Ride, RideMessage, RideStatus, User
from app.schemas import (
    AcceptIn, CancelIn, CompleteIn, EstimateIn, EstimateOut, GpsIn, MessageIn, MessageOut,
    PauseOut, PauseStartIn, RateIn, RideCreateIn, RideOut, StopOut, TrafficOut, TrafficStartIn,
)
from app.services import pricing
from app.services import rides as svc
from app.services import storage
from app.services.notify import notify
from app.services.settings import get_pricing

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post("/estimate", response_model=EstimateOut, summary="Devis (arrets, classe, route degradee)")
def estimate(payload: EstimateIn, _: User = Depends(current_user), db: Session = Depends(get_db)):
    svc.assert_class_for_mode(payload.mode, payload.service_class)
    pts = pricing.route_points((payload.pickup.lat, payload.pickup.lng),
                               [(s.lat, s.lng) for s in payload.stops],
                               (payload.destination.lat, payload.destination.lng))
    degraded = pricing.degraded_percent_for(db, pts) if payload.mode.value == "flexible" else 0
    q = pricing.quote(get_pricing(db), mode=payload.mode.value,
                      service_class=payload.service_class.value if payload.service_class else None,
                      distance_km=pricing.route_distance_km(pts), stops_count=len(payload.stops),
                      places=payload.places, degraded_percent=degraded)
    return EstimateOut(**q.__dict__)


@router.post("", response_model=RideOut, status_code=201, summary="Commander une course")
def create(payload: RideCreateIn, user: User = Depends(passenger_only), db: Session = Depends(get_db)):
    ride = svc.create_ride(db, user, payload)
    db.commit()
    return ride


@router.get("", response_model=list[RideOut], summary="Mes courses (passager ou chauffeur)")
def my_rides(status: RideStatus | None = None, limit: int = Query(30, ge=1, le=100), user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    clauses = [Ride.passenger_id == user.id]
    if user.driver:
        clauses.append(Ride.driver_id == user.driver.id)
    stmt = select(Ride).where(or_(*clauses))
    if status:
        stmt = stmt.where(Ride.status == status)
    return db.execute(stmt.order_by(Ride.id.desc()).limit(limit)).scalars().all()


@router.get("/offers", response_model=list[RideOut], summary="Commandes disponibles (chauffeur)")
def offers(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    svc.assert_can_drive(db, driver)
    return svc.offers_for(db, driver) if driver.online else []


@router.get("/{ride_id}", response_model=RideOut)
def detail(ride_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ride = svc.get_ride(db, ride_id)
    svc.assert_participant(ride, user)
    return ride


@router.post("/{ride_id}/accept", response_model=RideOut)
def accept(ride_id: int, payload: AcceptIn | None = None, driver: Driver = Depends(current_driver),
           db: Session = Depends(get_db)):
    ride = svc.accept(db, driver, ride_id, payload.eta_seconds if payload else None)
    db.commit()
    return ride


@router.post("/{ride_id}/refuse", summary="Refuser (fenetre quotidienne, sinon -5 points)")
def refuse(ride_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    result = svc.refuse(db, driver, ride_id)
    db.commit()
    return result


@router.post("/{ride_id}/start", response_model=RideOut)
def start(ride_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    ride = svc.start(db, driver, ride_id)
    db.commit()
    return ride


@router.post("/{ride_id}/stops/{stop_id}/pass", response_model=StopOut, summary="Arret intermediaire passe")
def pass_stop(ride_id: int, stop_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    stop = svc.pass_stop(db, driver, ride_id, stop_id)
    db.commit()
    return stop


@router.post("/{ride_id}/gps", summary="Trace GPS (preuve d'arbitrage, conservee indefiniment)")
def gps(ride_id: int, payload: GpsIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    count = svc.log_gps(db, driver, ride_id, payload.points)
    db.commit()
    return {"recorded": count}


@router.post("/{ride_id}/pause", response_model=PauseOut, status_code=201, summary="Declarer une Pause Arret")
def pause_start(ride_id: int, payload: PauseStartIn, driver: Driver = Depends(current_driver),
                db: Session = Depends(get_db)):
    pause = svc.pause_start(db, driver, ride_id, payload.lat, payload.lng)
    db.commit()
    return pause


@router.post("/{ride_id}/pause/end", response_model=PauseOut, summary="Fin de Pause Arret (supplement calcule)")
def pause_end(ride_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    pause = svc.pause_end(db, driver, ride_id)
    db.commit()
    return pause


@router.post("/{ride_id}/traffic", response_model=TrafficOut, status_code=201, summary="Debut d'embouteillage")
def traffic_start(ride_id: int, payload: TrafficStartIn | None = None, driver: Driver = Depends(current_driver),
                  db: Session = Depends(get_db)):
    event = svc.traffic_start(db, driver, ride_id, payload.source if payload else "manual")
    db.commit()
    return event


@router.post("/{ride_id}/traffic/end", response_model=TrafficOut, summary="Fin d'embouteillage (supplement)")
def traffic_end(ride_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    event = svc.traffic_end(db, driver, ride_id)
    db.commit()
    return event


@router.post("/{ride_id}/complete", response_model=RideOut,
             summary="Terminer : paiement, commission, points, credit d'objectif")
def complete(ride_id: int, payload: CompleteIn | None = None, driver: Driver = Depends(current_driver),
             db: Session = Depends(get_db)):
    ride = svc.complete(db, driver, ride_id, cash_received=bool(payload and payload.cash_received))
    db.commit()
    return ride


@router.post("/{ride_id}/cancel", response_model=RideOut,
             summary="Annuler (gratuit < 15 s ou chauffeur en retard, sinon frais)")
def cancel(ride_id: int, payload: CancelIn | None = None, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    ride = svc.cancel(db, user, ride_id, payload.reason if payload else None)
    db.commit()
    return ride


@router.post("/{ride_id}/rate", status_code=201, summary="Noter le chauffeur ou le passager")
def rate(ride_id: int, payload: RateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rating = svc.rate(db, user, ride_id, payload.stars, payload.tags, payload.comment)
    db.commit()
    return {"id": rating.id, "stars": rating.stars}


@router.get("/{ride_id}/messages", response_model=list[MessageOut])
def messages(ride_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ride = svc.get_ride(db, ride_id)
    svc.assert_participant(ride, user)
    return db.execute(select(RideMessage).where(RideMessage.ride_id == ride.id)
                      .order_by(RideMessage.id)).scalars().all()


@router.post("/{ride_id}/messages", response_model=MessageOut, status_code=201)
def send_message(ride_id: int, payload: MessageIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    if not payload.text and not payload.photo_url:
        raise Invalid("MESSAGE_EMPTY", "Message vide")
    return _post_message(db, _chat_ride(db, ride_id, user), user, payload.text, payload.photo_url)


def _chat_ride(db: Session, ride_id: int, user: User) -> Ride:
    ride = svc.get_ride(db, ride_id)
    svc.assert_participant(ride, user)
    if ride.status == RideStatus.cancelled or ride.driver_id is None:
        raise Conflict("CHAT_UNAVAILABLE", "Messagerie disponible une fois un chauffeur assigne")
    return ride


def _post_message(db: Session, ride: Ride, user: User, text: str | None, photo_url: str | None) -> RideMessage:
    msg = RideMessage(ride_id=ride.id, sender_id=user.id, text=text, photo_url=photo_url)
    db.add(msg)
    recipient = ride.passenger_id if user.id != ride.passenger_id else db.get(Driver, ride.driver_id).user_id
    notify(db, recipient, "ride_message", user.full_name, text or "Photo", ride_id=ride.id)
    db.commit()
    return msg


@router.post("/{ride_id}/messages/photo", response_model=MessageOut, status_code=201,
             summary="Envoyer une photo (JPEG, PNG ou WebP, 8 Mo maximum)")
def send_photo(ride_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    ride = _chat_ride(db, ride_id, user)
    key = storage.save("messages", ride.id, file, allow_pdf=False)
    return _post_message(db, ride, user, None, storage.url_for(key))
