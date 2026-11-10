from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_driver, current_user, require_roles
from app.core.clock import local_date, utcnow, week_start
from app.core.errors import Conflict, Invalid, NotFound
from app.db.session import get_db
from app.models import (
    CarlinqMode, Driver, DriverDocument, DriverRoute, DriverValidation, PointEvent, RoadReport,
    ServiceClass, User, UserRole,
)
from app.schemas import (
    DocumentIn, DocumentOut, DriverOut, DriverPublicOut, DriverRegisterIn, LocationIn, OnlineIn,
    PointEventOut, PremiumIn, RoadReportIn, RoadReportOut, RouteIn, RouteOut,
)
from app.services import goals as goals_svc
from app.services import rides as rides_svc
from app.services import storage
from app.services import wallet
from app.services.pricing import haversine_km
from app.services.settings import get_pricing

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post("/me", response_model=DriverOut, status_code=201,
             summary="Creer le profil chauffeur (en attente de validation admin)")
def register(payload: DriverRegisterIn, db: Session = Depends(get_db),
             user: User = Depends(require_roles(UserRole.drivers, UserRole.copilote))):
    if user.driver:
        raise Conflict("DRIVER_PROFILE_EXISTS", "Profil chauffeur deja cree")
    if payload.mode == CarlinqMode.flexible and payload.service_class is None:
        raise Invalid("SERVICE_CLASS_REQUIRED", "Classe requise en Carlinq Flexible")
    if payload.mode == CarlinqMode.taxi and payload.service_class is not None:
        raise Invalid("CLASS_NOT_APPLICABLE_TO_MODE", "Pas de classe en Carlinq Taxi")
    if user.role == UserRole.copilote and not payload.company_type:
        raise Invalid("COMPANY_TYPE_REQUIRED", "Type de profil Copilote requis")
    if db.execute(select(Driver.id).where(Driver.vehicle_plate == payload.vehicle_plate)).first():
        raise Conflict("PLATE_EXISTS", "Immatriculation deja enregistree")
    driver = Driver(user_id=user.id, validation_status=DriverValidation.pending, points=80,
                    completed_rides=0, online=False, refusal_seconds_used=0, **payload.model_dump())
    db.add(driver)
    db.commit()
    return driver


@router.get("/me", response_model=DriverOut)
def me(driver: Driver = Depends(current_driver)):
    return driver


@router.get("/me/dashboard", summary="Tableau de bord chauffeur (5.2.1)")
def dashboard(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    now = utcnow()
    today = rides_svc.stats_for_driver(db, driver, now - timedelta(hours=24))
    week = rides_svc.stats_for_driver(db, driver, now - timedelta(days=7))
    goal = goals_svc.goal_of(db, driver.id, week_start(now))
    window = rides_svc.refusal_window(db, driver, now)
    db.commit()
    return {
        "driver": DriverOut.model_validate(driver),
        "role": driver.user.role.value,
        "today": today,
        "week": week,
        "points": driver.points,
        "refusal_window": window,
        "goal": None if goal is None else {
            "id": goal.id, "target_rides": goal.target_rides, "progress_rides": goal.progress_rides,
            "bonus_xaf": goal.bonus_xaf, "status": goal.status.value,
        },
        "premium_active": bool(driver.premium_until and driver.premium_until > now),
        "wallet_balance_xaf": driver.user.wallet_balance_xaf,
    }


@router.post("/me/online", response_model=DriverOut, summary="En ligne / hors ligne")
def set_online(payload: OnlineIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    if payload.online:
        rides_svc.assert_can_drive(db, driver)
    driver.online = payload.online
    db.commit()
    return driver


@router.post("/me/location", status_code=204)
def update_location(payload: LocationIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    driver.lat, driver.lng = payload.lat, payload.lng
    driver.location_updated_at = utcnow()
    db.commit()


@router.get("/me/refusal-window", summary="Fenetre de refus quotidienne restante")
def refusal_window(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    window = rides_svc.refusal_window(db, driver)
    db.commit()
    return window


@router.get("/me/points", response_model=list[PointEventOut])
def points_history(limit: int = Query(50, ge=1, le=200), driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    return db.execute(select(PointEvent).where(PointEvent.driver_id == driver.id)
                      .order_by(PointEvent.id.desc()).limit(limit)).scalars().all()


# ------------------------------------------------------------------ documents

@router.post("/me/documents", response_model=DocumentOut, status_code=201)
def upload_document(payload: DocumentIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    doc = DriverDocument(driver_id=driver.id, **payload.model_dump())
    db.add(doc)
    db.commit()
    return doc


@router.post("/me/documents/upload", response_model=DocumentOut, status_code=201,
             summary="Envoyer un document (JPEG, PNG, WebP ou PDF, 8 Mo maximum)")
def upload_document_file(doc_type: str = Form(pattern="^(cni|permis|carte_grise|assurance|autre)$"),
                         file: UploadFile = File(...), driver: Driver = Depends(current_driver),
                         db: Session = Depends(get_db)):
    key = storage.save("documents", driver.id, file, allow_pdf=True)
    doc = DriverDocument(driver_id=driver.id, doc_type=doc_type, file_url=storage.url_for(key))
    db.add(doc)
    db.commit()
    return doc


@router.get("/me/documents", response_model=list[DocumentOut])
def list_documents(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    return db.execute(select(DriverDocument).where(DriverDocument.driver_id == driver.id)
                      .order_by(DriverDocument.id)).scalars().all()


# ------------------------------------------------------------------ itineraires (3 par jour)

@router.get("/me/routes", response_model=list[RouteOut], summary="Itineraires du jour (3 slots)")
def list_routes(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    return db.execute(select(DriverRoute).where(DriverRoute.driver_id == driver.id,
                                                DriverRoute.day == local_date())
                      .order_by(DriverRoute.slot)).scalars().all()


@router.put("/me/routes/{slot}", response_model=RouteOut, summary="Tracer ou remplacer un slot (1 a 3)")
def put_route(slot: int, payload: RouteIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    if slot not in (1, 2, 3):
        raise Invalid("ROUTE_SLOT_INVALID", "3 itineraires par jour : slots 1, 2 ou 3")
    today = local_date()
    route = db.execute(select(DriverRoute).where(DriverRoute.driver_id == driver.id, DriverRoute.day == today,
                                                 DriverRoute.slot == slot)).scalar_one_or_none()
    if route is None:
        route = DriverRoute(driver_id=driver.id, day=today, slot=slot, **payload.model_dump())
        db.add(route)
    else:
        for k, v in payload.model_dump().items():
            setattr(route, k, v)
    db.commit()
    return route


@router.delete("/me/routes/{slot}", status_code=204)
def delete_route(slot: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    route = db.execute(select(DriverRoute).where(DriverRoute.driver_id == driver.id,
                                                 DriverRoute.day == local_date(),
                                                 DriverRoute.slot == slot)).scalar_one_or_none()
    if route is None:
        raise NotFound("Itineraire", slot)
    db.delete(route)
    db.commit()


# ------------------------------------------------------------------ Pack Premium

@router.post("/me/premium", response_model=DriverOut, summary="Souscrire / renouveler le Pack Premium")
def subscribe_premium(payload: PremiumIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    price = get_pricing(db).premium_monthly_xaf * payload.months
    if payload.channel == "wallet":
        wallet.post(db, driver.user_id, -price, "premium", label=f"Pack Premium {payload.months} mois")
    else:
        wallet.require_payment_provider()
        # Paiement Mobile Money : l'encaissement est confirme par l'operateur
        # (integration CinetPay en Phase 2) ; on trace le paiement recu.
        wallet.post(db, driver.user_id, price, "topup", label="Paiement Pack Premium", channel=payload.channel)
        wallet.post(db, driver.user_id, -price, "premium", label=f"Pack Premium {payload.months} mois",
                    channel=payload.channel)
    now = utcnow()
    start = driver.premium_until if driver.premium_until and driver.premium_until > now else now
    driver.premium_until = start + timedelta(days=30 * payload.months)
    db.commit()
    return driver


# ------------------------------------------------------------------ routes degradees

@router.post("/me/road-reports", response_model=RoadReportOut, status_code=201,
             summary="Signaler une route degradee")
def report_road(payload: RoadReportIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    report = RoadReport(driver_id=driver.id, **payload.model_dump())
    db.add(report)
    db.commit()
    return report


# ------------------------------------------------------------------ cote passager

@router.get("/nearby", response_model=list[DriverPublicOut], summary="Chauffeurs en ligne a proximite")
def nearby(lat: float, lng: float, mode: CarlinqMode, service_class: ServiceClass | None = None,
           radius_km: float = Query(5, gt=0, le=30), _: User = Depends(current_user),
           db: Session = Depends(get_db)):
    stmt = select(Driver).where(Driver.online.is_(True), Driver.mode == mode, Driver.lat.is_not(None),
                                Driver.validation_status == DriverValidation.approved)
    if service_class:
        stmt = stmt.where(Driver.service_class == service_class)
    drivers = db.execute(stmt).scalars().all()
    close = [(haversine_km((lat, lng), (d.lat, d.lng)), d) for d in drivers]
    return [d for dist, d in sorted(close, key=lambda x: x[0]) if dist <= radius_km][:30]
