"""Cycle de vie d'une course : pending -> accepted -> in_progress -> completed | cancelled.

Chaque transition est journalisee dans `ride_status_logs`. Les chronometres
(Pause Arret, embouteillage) sont calcules cote serveur : source de verite.
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_date, utcnow
from app.core.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import (
    CarlinqMode, Driver, DriverValidation, PaymentMethod, Ride, RideGpsPoint, RidePause, RideRating,
    RideRefusal, RideStatus, RideStatusLog, RideStop, TrafficEvent, User, UserRole, Zone,
)
from app.services import goals as goals_svc
from app.services import points as points_svc
from app.services import pricing, wallet
from app.services.notify import notify
from app.services.settings import PricingConfig, get_pricing

ACTIVE = (RideStatus.accepted, RideStatus.in_progress)


def get_ride(db: Session, ride_id: int, *, lock: bool = False) -> Ride:
    stmt = select(Ride).where(Ride.id == ride_id)
    if lock:
        stmt = stmt.with_for_update()
    ride = db.execute(stmt).scalar_one_or_none()
    if ride is None:
        raise NotFound("Course", ride_id)
    return ride


def _transition(db: Session, ride: Ride, to: RideStatus, actor_id: int | None, note: str | None = None):
    db.add(RideStatusLog(ride_id=ride.id, from_status=ride.status.value if ride.status else None,
                         to_status=to.value, actor_id=actor_id, note=note))
    ride.status = to


def _expect(ride: Ride, *statuses: RideStatus):
    if ride.status not in statuses:
        raise Conflict("RIDE_INVALID_STATE", f"Action impossible : course {ride.status.value}",
                       status=ride.status.value, expected=[s.value for s in statuses])


def _own_driver(ride: Ride, driver: Driver):
    if ride.driver_id != driver.id:
        raise Forbidden("NOT_RIDE_DRIVER", "Vous n'etes pas le chauffeur de cette course")


def assert_participant(ride: Ride, user: User):
    if user.role == UserRole.admin:
        return
    if ride.passenger_id == user.id:
        return
    if user.driver and ride.driver_id == user.driver.id:
        return
    raise Forbidden("NOT_RIDE_PARTICIPANT", "Vous ne participez pas a cette course")


# --------------------------------------------------------------------------- creation

def assert_class_for_mode(mode: CarlinqMode, service_class) -> None:
    if mode == CarlinqMode.flexible and service_class is None:
        raise Invalid("SERVICE_CLASS_REQUIRED", "Classe requise en Carlinq Flexible")


def create_ride(db: Session, passenger: User, data) -> Ride:
    cfg = get_pricing(db)
    mode = data.mode
    assert_class_for_mode(mode, data.service_class)
    if data.payment_method in (PaymentMethod.orange_money, PaymentMethod.mtn_momo):
        wallet.require_payment_provider()
    # Verrou du passager : deux commandes simultanees ne creent qu'une course.
    db.execute(select(User.id).where(User.id == passenger.id).with_for_update())
    if mode == CarlinqMode.taxi and data.service_class:
        raise Invalid("CLASS_NOT_APPLICABLE_TO_MODE", "Pas de classe en Carlinq Taxi")
    if mode == CarlinqMode.taxi and data.taxi_zone_id is None:
        raise Invalid("TAXI_ZONE_REQUIRED", "Choisissez une zone de stationnement")
    if data.taxi_zone_id is not None:
        zone = db.get(Zone, data.taxi_zone_id)
        if zone is None or not zone.is_active:
            raise NotFound("Zone", data.taxi_zone_id)
    if db.execute(select(Ride.id).where(Ride.passenger_id == passenger.id,
                                        Ride.status.in_((RideStatus.pending, *ACTIVE)))).first():
        raise Conflict("RIDE_ALREADY_ACTIVE", "Vous avez deja une course en cours")

    stops = [(s.lat, s.lng) for s in data.stops]
    pts = pricing.route_points((data.pickup.lat, data.pickup.lng), stops,
                               (data.destination.lat, data.destination.lng))
    degraded = pricing.degraded_percent_for(db, pts) if mode == CarlinqMode.flexible else 0
    q = pricing.quote(cfg, mode=mode.value, service_class=data.service_class.value if data.service_class else None,
                      distance_km=pricing.route_distance_km(pts), stops_count=len(stops),
                      places=data.places, degraded_percent=degraded)

    if data.payment_method == PaymentMethod.wallet:
        # Portefeuille : solde minimum 500 XAF et suffisant pour l'estimation.
        needed = max(cfg.min_wallet_xaf, q.total_xaf)
        if passenger.wallet_balance_xaf < needed:
            raise Conflict("INSUFFICIENT_BALANCE", "Solde du portefeuille insuffisant",
                           balance_xaf=passenger.wallet_balance_xaf, required_xaf=needed)

    ride = Ride(
        passenger_id=passenger.id, mode=mode,
        service_class=data.service_class if mode == CarlinqMode.flexible else None,
        places=data.places, taxi_zone_id=data.taxi_zone_id,
        pickup_label=data.pickup.label, pickup_lat=data.pickup.lat, pickup_lng=data.pickup.lng,
        destination_label=data.destination.label, destination_lat=data.destination.lat,
        destination_lng=data.destination.lng, distance_km=q.distance_km,
        base_xaf=q.base_xaf, places_supplement_xaf=q.places_supplement_xaf,
        stop_supplement_xaf=q.stop_supplement_xaf, degraded_percent=q.degraded_percent,
        degraded_supplement_xaf=q.degraded_supplement_xaf, total_xaf=q.total_xaf,
        commission_xaf=q.commission_xaf, payment_method=data.payment_method,
        status=RideStatus.pending,
    )
    ride.stops = [RideStop(order_index=i, label=s.label, lat=s.lat, lng=s.lng,
                           supplement_xaf=q.stop_supplement_per_stop_xaf)
                  for i, s in enumerate(data.stops)]
    db.add(ride)
    db.flush()
    db.add(RideStatusLog(ride_id=ride.id, from_status=None, to_status=RideStatus.pending.value,
                         actor_id=passenger.id))
    return ride


# --------------------------------------------------------------------------- chauffeur

def assert_can_drive(db: Session, driver: Driver, cfg: PricingConfig | None = None):
    if driver.validation_status != DriverValidation.approved:
        raise Forbidden("DRIVER_NOT_APPROVED", "Compte chauffeur non valide ou suspendu",
                        status=driver.validation_status.value)
    if driver.user.role == UserRole.copilote:
        if driver.premium_until is None or driver.premium_until < utcnow():
            raise Forbidden("PREMIUM_REQUIRED", "Pack Premium requis pour recevoir des commandes")


def offers_for(db: Session, driver: Driver, limit: int = 20) -> list[Ride]:
    refused = select(RideRefusal.ride_id).where(RideRefusal.driver_id == driver.id)
    stmt = (select(Ride).where(Ride.status == RideStatus.pending, Ride.mode == driver.mode,
                               Ride.id.not_in(refused))
            .order_by(Ride.created_at).limit(limit))
    if driver.mode == CarlinqMode.flexible and driver.service_class:
        stmt = stmt.where(Ride.service_class == driver.service_class)
    return list(db.execute(stmt).scalars())


def refusal_window(db: Session, driver: Driver, now: datetime | None = None) -> dict:
    cfg = get_pricing(db)
    today = local_date(now)
    if driver.refusal_day != today:
        driver.refusal_day = today
        driver.refusal_seconds_used = 0
    left = max(0, cfg.refusal_window_seconds - driver.refusal_seconds_used)
    return {"seconds_total": cfg.refusal_window_seconds, "seconds_used": driver.refusal_seconds_used,
            "seconds_left": left, "cost_per_refusal_seconds": cfg.refusal_cost_seconds}


def accept(db: Session, driver: Driver, ride_id: int, eta_seconds: int | None, now: datetime | None = None) -> Ride:
    now = now or utcnow()
    assert_can_drive(db, driver)
    if not driver.online:
        raise Conflict("DRIVER_OFFLINE", "Passez en ligne pour accepter une course")
    # Verrou du chauffeur : deux acceptations simultanees ne donnent qu'une course active.
    db.execute(select(Driver.id).where(Driver.id == driver.id).with_for_update())
    if db.execute(select(Ride.id).where(Ride.driver_id == driver.id, Ride.status.in_(ACTIVE))).first():
        raise Conflict("DRIVER_HAS_ACTIVE_RIDE", "Terminez votre course en cours avant d'en accepter une autre")
    ride = get_ride(db, ride_id, lock=True)  # verrou : un seul chauffeur peut accepter
    _expect(ride, RideStatus.pending)
    if ride.mode != driver.mode:
        raise Conflict("RIDE_MODE_MISMATCH", "Cette course ne correspond pas a votre mode")
    if ride.mode == CarlinqMode.flexible and ride.service_class != driver.service_class:
        raise Conflict("RIDE_CLASS_MISMATCH", "Cette course ne correspond pas a votre classe")
    ride.driver_id = driver.id
    ride.accepted_at = now
    ride.eta_seconds = eta_seconds or 300
    _transition(db, ride, RideStatus.accepted, driver.user_id)
    notify(db, ride.passenger_id, "ride_accepted", "Chauffeur en route",
           f"{driver.user.full_name} arrive ({driver.vehicle_brand} {driver.vehicle_model}, {driver.vehicle_plate}).",
           ride_id=ride.id)
    return ride


def refuse(db: Session, driver: Driver, ride_id: int, now: datetime | None = None) -> dict:
    cfg = get_pricing(db)
    ride = get_ride(db, ride_id)
    _expect(ride, RideStatus.pending)
    if db.execute(select(RideRefusal.id).where(RideRefusal.ride_id == ride.id,
                                               RideRefusal.driver_id == driver.id)).first():
        raise Conflict("RIDE_ALREADY_REFUSED", "Course deja refusee")
    window = refusal_window(db, driver, now)
    penalized = window["seconds_left"] < cfg.refusal_cost_seconds
    if penalized:
        driver.refusal_seconds_used = cfg.refusal_window_seconds
        points_svc.add_points(db, driver, points_svc.UNAUTHORIZED_REFUSAL, "refusal",
                              "Refus hors fenetre de refus quotidienne", ride.id)
    else:
        driver.refusal_seconds_used += cfg.refusal_cost_seconds
    db.add(RideRefusal(ride_id=ride.id, driver_id=driver.id, penalized=penalized))
    return {"penalized": penalized, "points": driver.points, **refusal_window(db, driver, now)}


def start(db: Session, driver: Driver, ride_id: int, now: datetime | None = None) -> Ride:
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    _expect(ride, RideStatus.accepted)
    ride.started_at = now or utcnow()
    _transition(db, ride, RideStatus.in_progress, driver.user_id)
    return ride


def pass_stop(db: Session, driver: Driver, ride_id: int, stop_id: int, now: datetime | None = None) -> RideStop:
    ride = get_ride(db, ride_id)
    _own_driver(ride, driver)
    _expect(ride, RideStatus.in_progress)
    stop = next((s for s in ride.stops if s.id == stop_id), None)
    if stop is None:
        raise NotFound("Arret", stop_id)
    stop.passed_at = stop.passed_at or now or utcnow()
    return stop


def log_gps(db: Session, driver: Driver, ride_id: int, points: list, now: datetime | None = None) -> int:
    ride = get_ride(db, ride_id)
    _own_driver(ride, driver)
    _expect(ride, *ACTIVE)
    for p in points:
        db.add(RideGpsPoint(ride_id=ride.id, lat=p.lat, lng=p.lng, speed_kmh=p.speed_kmh,
                            recorded_at=p.recorded_at or now or utcnow()))
    return len(points)


# --------------------------------------------------------------------------- Pause Arret

def pause_start(db: Session, driver: Driver, ride_id: int, lat: float, lng: float,
                now: datetime | None = None) -> RidePause:
    cfg = get_pricing(db)
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    _expect(ride, RideStatus.in_progress)
    if any(p.ended_at is None for p in ride.pauses):
        raise Conflict("PAUSE_ALREADY_ACTIVE", "Une Pause Arret est deja en cours")
    if len(ride.pauses) >= cfg.max_impromptu_pauses:
        raise Conflict("PAUSE_QUOTA_REACHED", f"Maximum {cfg.max_impromptu_pauses} Pauses Arret par course")
    pause = RidePause(ride_id=ride.id, started_at=now, lat=lat, lng=lng)
    db.add(pause)
    db.add(RideGpsPoint(ride_id=ride.id, lat=lat, lng=lng, recorded_at=now))
    notify(db, ride.passenger_id, "pause_started", "Pause Arret", "Votre chauffeur a declare une Pause Arret.",
           ride_id=ride.id)
    db.flush()
    return pause


def pause_end(db: Session, driver: Driver, ride_id: int, now: datetime | None = None) -> RidePause:
    cfg = get_pricing(db)
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    pause = next((p for p in ride.pauses if p.ended_at is None), None)
    if pause is None:
        raise Conflict("NO_ACTIVE_PAUSE", "Aucune Pause Arret en cours")
    pause.ended_at = now
    pause.supplement_xaf = pricing.per_minute_supplement(int((now - pause.started_at).total_seconds()),
                                                         cfg.pause_rate_per_minute_xaf)
    ride.pause_supplement_xaf = sum(p.supplement_xaf for p in ride.pauses)
    return pause


# --------------------------------------------------------------------------- embouteillage

def traffic_start(db: Session, driver: Driver, ride_id: int, source: str = "manual",
                  now: datetime | None = None) -> TrafficEvent:
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    _expect(ride, RideStatus.in_progress)
    if any(t.ended_at is None for t in ride.traffic_events):
        raise Conflict("TRAFFIC_ALREADY_ACTIVE", "Un embouteillage est deja en cours")
    event = TrafficEvent(ride_id=ride.id, started_at=now, source=source)
    db.add(event)
    db.flush()
    return event


def traffic_end(db: Session, driver: Driver, ride_id: int, now: datetime | None = None) -> TrafficEvent:
    cfg = get_pricing(db)
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    event = next((t for t in ride.traffic_events if t.ended_at is None), None)
    if event is None:
        raise Conflict("NO_ACTIVE_TRAFFIC", "Aucun embouteillage en cours")
    event.ended_at = now
    event.supplement_xaf = pricing.per_minute_supplement(int((now - event.started_at).total_seconds()),
                                                         cfg.traffic_rate_per_minute_xaf,
                                                         cfg.traffic_tolerance_seconds)
    ride.traffic_supplement_xaf = sum(t.supplement_xaf for t in ride.traffic_events)
    return event


# --------------------------------------------------------------------------- fin de course

def complete(db: Session, driver: Driver, ride_id: int, cash_received: bool = False,
             now: datetime | None = None) -> Ride:
    cfg = get_pricing(db)
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _own_driver(ride, driver)
    _expect(ride, RideStatus.in_progress)
    # Chronometres encore ouverts : cloture automatique.
    if any(p.ended_at is None for p in ride.pauses):
        pause_end(db, driver, ride.id, now)
    if any(t.ended_at is None for t in ride.traffic_events):
        traffic_end(db, driver, ride.id, now)
    if ride.payment_method == PaymentMethod.cash and not cash_received:
        raise Conflict("CASH_NOT_CONFIRMED", "Confirmez la reception du paiement direct pour cloturer")

    ride.total_xaf = (ride.base_xaf + ride.places_supplement_xaf + ride.stop_supplement_xaf
                      + ride.degraded_supplement_xaf + ride.traffic_supplement_xaf + ride.pause_supplement_xaf)
    ride.commission_xaf = pricing.commission(cfg, ride.total_xaf)
    ride.driver_earning_xaf = ride.total_xaf - ride.commission_xaf
    ride.completed_at = now

    label = f"Course #{ride.id}"
    if ride.payment_method == PaymentMethod.cash:
        # Paiement direct : la commission est prelevee sur le portefeuille chauffeur (declaratif).
        wallet.post(db, driver.user_id, -ride.commission_xaf, "commission", label=f"Commission {label} (especes)",
                    ride_id=ride.id, allow_negative=True)
        ride.payment_status = "declared"
    else:
        # Paiement via l'app : debit du passager, credit du chauffeur net de commission.
        # Le solde peut devenir negatif si les supplements depassent : le passager
        # devra recharger avant sa prochaine course (minimum 500 XAF).
        wallet.post(db, ride.passenger_id, -ride.total_xaf, "ride_payment", label=label, ride_id=ride.id,
                    channel=None if ride.payment_method == PaymentMethod.wallet else ride.payment_method.value,
                    allow_negative=True)
        wallet.post(db, driver.user_id, ride.driver_earning_xaf, "ride_earning",
                    label=f"{label} ({cfg.commission_percent} % de commission deduits)", ride_id=ride.id)
        ride.payment_status = "paid"

    _transition(db, ride, RideStatus.completed, driver.user_id)
    driver.completed_rides += 1
    points_svc.add_points(db, driver, points_svc.RIDE_COMPLETED, "ride_completed", ride_id=ride.id)
    goals_svc.credit_ride(db, driver, ride, now)
    notify(db, ride.passenger_id, "ride_completed", "Course terminee",
           f"Total : {ride.total_xaf} XAF. Merci d'avoir voyage avec Carlinq !", ride_id=ride.id)
    return ride


def cancel(db: Session, user: User, ride_id: int, reason: str | None, now: datetime | None = None) -> Ride:
    """Annulation passager : gratuite < 15 s ou si le chauffeur est en retard.
    Annulation chauffeur : -5 points (annulation injustifiee)."""
    cfg = get_pricing(db)
    now = now or utcnow()
    ride = get_ride(db, ride_id, lock=True)
    _expect(ride, RideStatus.pending, *ACTIVE)
    driver = db.get(Driver, ride.driver_id) if ride.driver_id else None

    if user.id == ride.passenger_id:
        by = "passenger"
        elapsed = (now - ride.created_at).total_seconds()
        late = bool(ride.accepted_at and ride.status == RideStatus.accepted and
                    (now - ride.accepted_at).total_seconds() > (ride.eta_seconds or 0) + cfg.late_tolerance_seconds)
        free = ride.status == RideStatus.pending or elapsed <= cfg.free_cancel_seconds or late
        if not free and cfg.cancellation_fee_xaf:
            ride.cancellation_fee_xaf = cfg.cancellation_fee_xaf
            wallet.post(db, ride.passenger_id, -cfg.cancellation_fee_xaf, "cancellation_fee",
                        label=f"Frais d'annulation course #{ride.id}", ride_id=ride.id, allow_negative=True)
            if driver:
                wallet.post(db, driver.user_id, cfg.cancellation_fee_xaf, "cancellation_fee",
                            label=f"Indemnite d'annulation course #{ride.id}", ride_id=ride.id)
        if driver:
            notify(db, driver.user_id, "ride_cancelled", "Course annulee",
                   "Le passager a annule la course.", ride_id=ride.id)
    elif driver and user.id == driver.user_id:
        by = "driver"
        points_svc.add_points(db, driver, points_svc.UNJUSTIFIED_CANCELLATION, "cancellation",
                              reason or "Annulation par le chauffeur", ride.id)
        notify(db, ride.passenger_id, "ride_cancelled", "Course annulee",
               "Le chauffeur a annule. Aucun frais ne vous est facture.", ride_id=ride.id)
    elif user.role == UserRole.admin:
        by = "admin"
    else:
        raise Forbidden("NOT_RIDE_PARTICIPANT", "Vous ne participez pas a cette course")

    ride.cancelled_at = now
    ride.cancelled_by = by
    ride.cancel_reason = reason
    _transition(db, ride, RideStatus.cancelled, user.id, reason)
    return ride


def rate(db: Session, user: User, ride_id: int, stars: int, tags: list[str], comment: str | None) -> RideRating:
    ride = get_ride(db, ride_id)
    _expect(ride, RideStatus.completed)
    assert_participant(ride, user)
    driver = db.get(Driver, ride.driver_id)
    ratee_id = driver.user_id if user.id == ride.passenger_id else ride.passenger_id
    if db.execute(select(RideRating.id).where(RideRating.ride_id == ride.id,
                                              RideRating.rater_id == user.id)).first():
        raise Conflict("ALREADY_RATED", "Course deja notee")
    rating = RideRating(ride_id=ride.id, rater_id=user.id, ratee_id=ratee_id, stars=stars, tags=tags,
                        comment=comment)
    db.add(rating)
    ratee = db.get(User, ratee_id)
    ratee.rating_avg = round((ratee.rating_avg * ratee.rating_count + stars) / (ratee.rating_count + 1), 2)
    ratee.rating_count += 1
    return rating


def stats_for_driver(db: Session, driver: Driver, since: datetime) -> dict:
    row = db.execute(
        select(func.count(Ride.id), func.coalesce(func.sum(Ride.driver_earning_xaf), 0))
        .where(Ride.driver_id == driver.id, Ride.status == RideStatus.completed, Ride.completed_at >= since)
    ).one()
    return {"rides": row[0], "earnings_xaf": int(row[1])}
