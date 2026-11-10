"""Panneau administrateur (cahier des charges 5.3, Table 17). Toutes les routes : role admin."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_only
from app.api.v1.routes.goals import goal_out, share_out
from app.core import cache
from app.core.clock import utcnow, week_start
from app.core.errors import Conflict, Invalid, NotFound
from app.db.session import get_db
from app.models import (
    AuditLog, DegradedRoad, Dispute, DisputeStatus, DocumentStatus, Driver, DriverDocument,
    DriverValidation, GoalSettlement, GoalShare, GoalShareStatus, GoalStatus, Notification,
    PushCampaign, ReportStatus, Ride, RidePause, RideStatus, RoadReport, User, UserRole, WeeklyGoal,
    Zone,
)
from app.models.ride import RideGpsPoint
from app.schemas import (
    DisputeOut, DocumentOut, DriverOut, GoalOut, RideOut, RoadIn, RoadOut, RoadReportOut,
    SettlementOut, ShareOut, UserOut, ZoneIn, ZoneOut,
)
from app.services import goals as goals_svc
from app.services import points as points_svc
from app.services import wallet
from app.services.audit import audit
from app.services.notify import notify
from app.services.settings import get_goals_config, get_pricing, update_setting

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_only)])


def _snapshot(obj, *fields) -> dict:
    return {f: (v.value if hasattr(v, "value") else v.isoformat() if isinstance(v, datetime) else v)
            for f in fields for v in [getattr(obj, f)]}


# ------------------------------------------------------------------ dashboard

@router.get("/dashboard", summary="KPIs temps reel")
def dashboard(db: Session = Depends(get_db)):
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    active = (RideStatus.pending, RideStatus.accepted, RideStatus.in_progress)
    rides_by_mode = dict(db.execute(select(Ride.mode, func.count(Ride.id)).where(Ride.status.in_(active))
                                    .group_by(Ride.mode)).all())
    revenue = db.execute(select(func.coalesce(func.sum(Ride.total_xaf), 0),
                                func.coalesce(func.sum(Ride.commission_xaf), 0))
                         .where(Ride.status == RideStatus.completed, Ride.completed_at >= day_ago)).one()
    week = week_start(now)
    goals_by_status = dict(db.execute(select(WeeklyGoal.status, func.count(WeeklyGoal.id))
                                      .where(WeeklyGoal.week_start == week).group_by(WeeklyGoal.status)).all())
    return {
        "active_rides": sum(rides_by_mode.values()),
        "active_rides_by_mode": {k.value: v for k, v in rides_by_mode.items()},
        "online_drivers": db.scalar(select(func.count(Driver.id)).where(Driver.online.is_(True))),
        "pending_driver_validations": db.scalar(select(func.count(Driver.id)).where(
            Driver.validation_status == DriverValidation.pending)),
        "passengers": db.scalar(select(func.count(User.id)).where(User.role == UserRole.passenger)),
        "completed_rides_24h": db.scalar(select(func.count(Ride.id)).where(
            Ride.status == RideStatus.completed, Ride.completed_at >= day_ago)),
        "revenue_24h_xaf": int(revenue[0]),
        "commission_24h_xaf": int(revenue[1]),
        "open_disputes": db.scalar(select(func.count(Dispute.id)).where(
            Dispute.status.in_((DisputeStatus.open, DisputeStatus.under_review)))),
        "pending_road_reports": db.scalar(select(func.count(RoadReport.id)).where(
            RoadReport.status == ReportStatus.pending)),
        "goals_this_week": {k.value: v for k, v in goals_by_status.items()},
        "active_goal_shares": db.scalar(select(func.count(GoalShare.id)).join(WeeklyGoal).where(
            WeeklyGoal.week_start == week, GoalShare.status == GoalShareStatus.accepted)),
    }


# ------------------------------------------------------------------ chauffeurs

@router.get("/drivers", response_model=list[DriverOut])
def list_drivers(status: DriverValidation | None = None, role: UserRole | None = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    stmt = select(Driver).join(User)
    if status:
        stmt = stmt.where(Driver.validation_status == status)
    if role:
        stmt = stmt.where(User.role == role)
    return db.execute(stmt.order_by(Driver.id.desc()).offset(offset).limit(limit)).scalars().all()


class DriverDecisionIn(BaseModel):
    status: DriverValidation
    reason: str | None = Field(None, max_length=300)


@router.post("/drivers/{driver_id}/status", response_model=DriverOut,
             summary="Valider, rejeter, suspendre, reactiver ou exclure un chauffeur")
def set_driver_status(driver_id: int, payload: DriverDecisionIn, admin: User = Depends(admin_only),
                      db: Session = Depends(get_db)):
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise NotFound("Chauffeur", driver_id)
    if driver.validation_status == DriverValidation.excluded:
        raise Conflict("DRIVER_EXCLUDED", "Exclusion definitive : aucun changement possible")
    if payload.status in (DriverValidation.rejected, DriverValidation.suspended, DriverValidation.excluded) \
            and not payload.reason:
        raise Invalid("REASON_REQUIRED", "Motif obligatoire")
    if payload.status != DriverValidation.approved:
        active = (RideStatus.accepted, RideStatus.in_progress)
        if db.execute(select(Ride.id).where(Ride.driver_id == driver.id, Ride.status.in_(active))).first():
            raise Conflict("DRIVER_HAS_ACTIVE_RIDE", "Le chauffeur a une course en cours")
        driver.online = False
    before = _snapshot(driver, "validation_status")
    driver.validation_status = payload.status
    audit(db, admin.id, f"driver.{payload.status.value}", "drivers", driver.id, before,
          {"validation_status": payload.status.value, "reason": payload.reason})
    notify(db, driver.user_id, "driver_status", "Statut de votre compte chauffeur",
           {"approved": "Votre compte est valide : vous pouvez passer en ligne.",
            "rejected": f"Inscription refusee : {payload.reason}",
            "suspended": f"Compte suspendu : {payload.reason}",
            "excluded": f"Compte exclu : {payload.reason}",
            "pending": "Votre dossier est en cours de verification."}[payload.status.value])
    db.commit()
    return driver


class ServiceClassIn(BaseModel):
    service_class: str = Field(pattern="^(eco|serenity|prestige)$")
    inspection_notes: str = Field(min_length=3, max_length=500)


@router.post("/drivers/{driver_id}/service-class", response_model=DriverOut)
def set_service_class(driver_id: int, payload: ServiceClassIn, admin: User = Depends(admin_only),
                      db: Session = Depends(get_db)):
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise NotFound("Chauffeur", driver_id)
    if driver.mode.value == "taxi":
        raise Conflict("CLASS_NOT_APPLICABLE_TO_MODE", "Pas de classe en Carlinq Taxi")
    before = _snapshot(driver, "service_class")
    driver.service_class = payload.service_class
    audit(db, admin.id, "driver.service_class", "drivers", driver.id, before,
          {"service_class": payload.service_class, "notes": payload.inspection_notes})
    db.commit()
    return driver


class PointsIn(BaseModel):
    delta: int = Field(ge=-100, le=100)
    reason: str = Field(min_length=3, max_length=300)


@router.post("/drivers/{driver_id}/points", response_model=DriverOut, summary="Ajustement manuel de points")
def adjust_points(driver_id: int, payload: PointsIn, admin: User = Depends(admin_only),
                  db: Session = Depends(get_db)):
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise NotFound("Chauffeur", driver_id)
    before = driver.points
    points_svc.add_points(db, driver, payload.delta, "admin_adjustment", payload.reason)
    audit(db, admin.id, "driver.points.adjust", "drivers", driver.id, {"points": before},
          {"points": driver.points, "reason": payload.reason})
    db.commit()
    return driver


@router.get("/documents", response_model=list[DocumentOut])
def pending_documents(status: DocumentStatus = DocumentStatus.pending, db: Session = Depends(get_db)):
    return db.execute(select(DriverDocument).where(DriverDocument.status == status)
                      .order_by(DriverDocument.id)).scalars().all()


class DocumentReviewIn(BaseModel):
    approve: bool
    reason: str | None = Field(None, max_length=500)


@router.post("/documents/{doc_id}/review", response_model=DocumentOut)
def review_document(doc_id: int, payload: DocumentReviewIn, admin: User = Depends(admin_only),
                    db: Session = Depends(get_db)):
    doc = db.get(DriverDocument, doc_id)
    if doc is None:
        raise NotFound("Document", doc_id)
    if not payload.approve and not payload.reason:
        raise Invalid("REASON_REQUIRED", "Motif de rejet obligatoire")
    doc.status = DocumentStatus.approved if payload.approve else DocumentStatus.rejected
    doc.rejection_reason = None if payload.approve else payload.reason
    doc.reviewed_by = admin.id
    doc.reviewed_at = utcnow()
    audit(db, admin.id, "document.review", "driver_documents", doc.id,
          after={"status": doc.status.value, "reason": payload.reason})
    db.commit()
    return doc


# ------------------------------------------------------------------ utilisateurs

@router.get("/users", response_model=list[UserOut])
def list_users(role: UserRole | None = None, q: str | None = None, limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    stmt = select(User).where(User.deleted_at.is_(None))
    if role:
        stmt = stmt.where(User.role == role)
    if q:
        stmt = stmt.where(User.full_name.ilike(f"%{q}%") | User.phone.ilike(f"%{q}%"))
    return db.execute(stmt.order_by(User.id.desc()).offset(offset).limit(limit)).scalars().all()


class SuspendIn(BaseModel):
    hours: int | None = Field(None, ge=1, le=24 * 365)   # None = bannissement
    reason: str = Field(min_length=3, max_length=300)


@router.post("/users/{user_id}/suspend", response_model=UserOut, summary="Suspendre (48h, 7j...) ou bannir")
def suspend_user(user_id: int, payload: SuspendIn, admin: User = Depends(admin_only),
                 db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None or user.role == UserRole.admin:
        raise NotFound("Utilisateur", user_id)
    if payload.hours:
        user.suspended_until = utcnow() + timedelta(hours=payload.hours)
    else:
        user.is_active = False
    if user.driver:
        user.driver.online = False
    audit(db, admin.id, "user.suspend" if payload.hours else "user.ban", "users", user.id,
          after={"hours": payload.hours, "reason": payload.reason})
    db.commit()
    return user


@router.post("/users/{user_id}/reactivate", response_model=UserOut)
def reactivate_user(user_id: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Utilisateur", user_id)
    user.is_active = True
    user.suspended_until = None
    audit(db, admin.id, "user.reactivate", "users", user.id)
    db.commit()
    return user


class WalletAdjustIn(BaseModel):
    amount_xaf: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/users/{user_id}/wallet-adjustment", summary="Ajustement manuel de portefeuille (motif obligatoire)")
def adjust_wallet(user_id: int, payload: WalletAdjustIn, admin: User = Depends(admin_only),
                  db: Session = Depends(get_db)):
    if payload.amount_xaf == 0:
        raise Invalid("AMOUNT_ZERO", "Montant nul")
    if db.get(User, user_id) is None:
        raise NotFound("Utilisateur", user_id)
    tx = wallet.post(db, user_id, payload.amount_xaf, "adjustment", label=payload.reason)
    audit(db, admin.id, "wallet.adjust", "users", user_id, after={"amount_xaf": payload.amount_xaf,
                                                                    "reason": payload.reason})
    db.commit()
    return {"transaction_id": tx.id, "balance_after_xaf": tx.balance_after_xaf}


# ------------------------------------------------------------------ courses et litiges

@router.get("/rides", response_model=list[RideOut])
def list_rides(status: RideStatus | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    stmt = select(Ride)
    if status:
        stmt = stmt.where(Ride.status == status)
    return db.execute(stmt.order_by(Ride.id.desc()).offset(offset).limit(limit)).scalars().all()


@router.get("/disputes", response_model=list[DisputeOut])
def list_disputes(status: DisputeStatus | None = None, db: Session = Depends(get_db)):
    stmt = select(Dispute)
    if status:
        stmt = stmt.where(Dispute.status == status)
    return db.execute(stmt.order_by(Dispute.id.desc())).scalars().all()


@router.get("/disputes/{dispute_id}/evidence", summary="Dossier de preuve : Pauses Arret + trace GPS")
def dispute_evidence(dispute_id: int, db: Session = Depends(get_db)):
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise NotFound("Litige", dispute_id)
    pauses = db.execute(select(RidePause).where(RidePause.ride_id == dispute.ride_id)).scalars().all()
    track = db.execute(select(RideGpsPoint).where(RideGpsPoint.ride_id == dispute.ride_id)
                       .order_by(RideGpsPoint.recorded_at)).scalars().all()
    return {
        "dispute": DisputeOut.model_validate(dispute),
        "pauses": [{"id": p.id, "started_at": p.started_at, "ended_at": p.ended_at, "lat": p.lat, "lng": p.lng,
                    "supplement_xaf": p.supplement_xaf} for p in pauses],
        "gps_track": [{"lat": g.lat, "lng": g.lng, "speed_kmh": g.speed_kmh, "at": g.recorded_at} for g in track],
    }


@router.post("/disputes/{dispute_id}/claim", response_model=DisputeOut)
def claim_dispute(dispute_id: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise NotFound("Litige", dispute_id)
    if dispute.status != DisputeStatus.open:
        raise Conflict("DISPUTE_NOT_OPEN", "Litige deja pris en charge ou resolu")
    dispute.status = DisputeStatus.under_review
    audit(db, admin.id, "dispute.claim", "disputes", dispute.id)
    db.commit()
    return dispute


class ResolveIn(BaseModel):
    accepted: bool                       # True = raison donnee au plaignant
    refund_xaf: int = Field(0, ge=0)
    points_penalty: int = Field(0, ge=0, le=20)
    resolution_note: str = Field(min_length=5, max_length=1000)


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeOut,
             summary="Arbitrage : remboursement passager et penalite chauffeur")
def resolve_dispute(dispute_id: int, payload: ResolveIn, admin: User = Depends(admin_only),
                    db: Session = Depends(get_db)):
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise NotFound("Litige", dispute_id)
    if dispute.status in (DisputeStatus.accepted, DisputeStatus.rejected):
        raise Conflict("DISPUTE_ALREADY_RESOLVED", "Litige deja resolu")
    ride = db.get(Ride, dispute.ride_id)
    driver = db.get(Driver, ride.driver_id)
    if payload.accepted:
        if payload.refund_xaf > ride.total_xaf:
            raise Invalid("REFUND_TOO_HIGH", "Remboursement superieur au prix de la course")
        if payload.refund_xaf:
            wallet.post(db, ride.passenger_id, payload.refund_xaf, "refund",
                        label=f"Remboursement litige #{dispute.id}", ride_id=ride.id)
            wallet.post(db, driver.user_id, -payload.refund_xaf, "refund",
                        label=f"Retenue litige #{dispute.id}", ride_id=ride.id, allow_negative=True)
        if payload.points_penalty:
            points_svc.add_points(db, driver, -payload.points_penalty,
                                  "pause_abuse" if dispute.kind == "pause_arret" else "dispute",
                                  payload.resolution_note, ride.id)
    dispute.status = DisputeStatus.accepted if payload.accepted else DisputeStatus.rejected
    dispute.refund_xaf = payload.refund_xaf if payload.accepted else 0
    dispute.points_penalty = payload.points_penalty if payload.accepted else 0
    dispute.resolution_note = payload.resolution_note
    dispute.resolved_by = admin.id
    dispute.resolved_at = utcnow()
    notify(db, dispute.opened_by, "dispute_resolved", "Litige traite", payload.resolution_note,
           dispute_id=dispute.id)
    audit(db, admin.id, "dispute.resolve", "disputes", dispute.id, after=payload.model_dump())
    db.commit()
    return dispute


# ------------------------------------------------------------------ configuration

@router.get("/settings/pricing")
def get_pricing_settings(db: Session = Depends(get_db)):
    return get_pricing(db).model_dump()


@router.put("/settings/pricing", summary="Modifier la tarification (commission <= 10 %)")
def put_pricing_settings(payload: dict, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    cfg = update_setting(db, "pricing", payload, admin.id)
    db.commit()
    return cfg.model_dump()


@router.get("/settings/goals", summary="Paliers d'objectifs et regles de partage")
def get_goals_settings(db: Session = Depends(get_db)):
    return get_goals_config(db).model_dump()


@router.put("/settings/goals", summary="Modifier paliers, bonus et regles de partage d'objectif")
def put_goals_settings(payload: dict, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    cfg = update_setting(db, "goals", payload, admin.id)
    db.commit()
    return cfg.model_dump()


# ------------------------------------------------------------------ objectifs et partages

@router.get("/goals", response_model=list[GoalOut], summary="Objectifs d'une semaine (partages inclus)")
def list_goals(week: date | None = None, status: GoalStatus | None = None, db: Session = Depends(get_db)):
    stmt = select(WeeklyGoal).where(WeeklyGoal.week_start == (week or week_start(utcnow())))
    if status:
        stmt = stmt.where(WeeklyGoal.status == status)
    return [goal_out(db, g) for g in db.execute(stmt.order_by(WeeklyGoal.id)).scalars()]


@router.get("/goals/shares", response_model=list[ShareOut])
def list_shares(week: date | None = None, status: GoalShareStatus | None = None, db: Session = Depends(get_db)):
    stmt = select(GoalShare).join(WeeklyGoal).where(WeeklyGoal.week_start == (week or week_start(utcnow())))
    if status:
        stmt = stmt.where(GoalShare.status == status)
    return [share_out(db, s) for s in db.execute(stmt.order_by(GoalShare.id)).scalars()]


class CloseWeekIn(BaseModel):
    week_start: date


@router.post("/goals/close-week", summary="Cloture : versement des bonus reparti selon les partages")
def close_week(payload: CloseWeekIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    if payload.week_start.weekday() != 0:
        raise Invalid("WEEK_START_NOT_MONDAY", "La semaine commence un lundi")
    result = goals_svc.close_week(db, payload.week_start, admin.id)
    db.commit()
    return result


@router.post("/goals/{goal_id}/settle", response_model=SettlementOut, summary="Verser le bonus d'un objectif")
def settle_goal(goal_id: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    settlement = goals_svc.settle_goal(db, goal_id, admin.id)
    db.commit()
    return settlement


@router.get("/goals/settlements", response_model=list[SettlementOut])
def list_settlements(week: date | None = None, db: Session = Depends(get_db)):
    stmt = select(GoalSettlement).join(WeeklyGoal, WeeklyGoal.id == GoalSettlement.goal_id)
    if week:
        stmt = stmt.where(WeeklyGoal.week_start == week)
    return db.execute(stmt.order_by(GoalSettlement.id.desc()).limit(200)).scalars().all()


# ------------------------------------------------------------------ zones et routes degradees

@router.post("/zones", response_model=ZoneOut, status_code=201)
def create_zone(payload: ZoneIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    zone = Zone(**payload.model_dump())
    db.add(zone)
    db.flush()
    audit(db, admin.id, "zone.create", "zones", zone.id, after=payload.model_dump())
    cache.invalidate_on_commit(db, "zones:active")
    db.commit()
    return zone


@router.put("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, payload: ZoneIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise NotFound("Zone", zone_id)
    before = _snapshot(zone, *payload.model_dump().keys())
    for k, v in payload.model_dump().items():
        setattr(zone, k, v)
    audit(db, admin.id, "zone.update", "zones", zone.id, before, payload.model_dump())
    cache.invalidate_on_commit(db, "zones:active")
    db.commit()
    return zone


@router.delete("/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise NotFound("Zone", zone_id)
    if db.execute(select(Ride.id).where(Ride.taxi_zone_id == zone.id)).first():
        raise Conflict("ZONE_IN_USE", "Zone referencee par des courses : desactivez-la plutot")
    audit(db, admin.id, "zone.delete", "zones", zone.id, _snapshot(zone, "name", "district"))
    db.delete(zone)
    cache.invalidate_on_commit(db, "zones:active")
    db.commit()


@router.post("/roads", response_model=RoadOut, status_code=201)
def create_road(payload: RoadIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    road = DegradedRoad(**payload.model_dump())
    db.add(road)
    db.flush()
    audit(db, admin.id, "road.create", "degraded_roads", road.id, after=payload.model_dump())
    cache.invalidate_on_commit(db, "roads:validated")
    db.commit()
    return road


@router.put("/roads/{road_id}", response_model=RoadOut)
def update_road(road_id: int, payload: RoadIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    road = db.get(DegradedRoad, road_id)
    if road is None:
        raise NotFound("Route degradee", road_id)
    for k, v in payload.model_dump().items():
        setattr(road, k, v)
    audit(db, admin.id, "road.update", "degraded_roads", road.id, after=payload.model_dump())
    cache.invalidate_on_commit(db, "roads:validated")
    db.commit()
    return road


@router.delete("/roads/{road_id}", status_code=204)
def delete_road(road_id: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    road = db.get(DegradedRoad, road_id)
    if road is None:
        raise NotFound("Route degradee", road_id)
    for report in db.execute(select(RoadReport).where(RoadReport.degraded_road_id == road.id)).scalars():
        report.degraded_road_id = None
    audit(db, admin.id, "road.delete", "degraded_roads", road.id, _snapshot(road, "name", "severity_percent"))
    db.delete(road)
    cache.invalidate_on_commit(db, "roads:validated")
    db.commit()


@router.get("/road-reports", response_model=list[RoadReportOut])
def list_road_reports(status: ReportStatus = ReportStatus.pending, db: Session = Depends(get_db)):
    return db.execute(select(RoadReport).where(RoadReport.status == status)
                      .order_by(RoadReport.id)).scalars().all()


class RoadReportReviewIn(BaseModel):
    approve: bool
    name: str | None = Field(None, max_length=160)
    severity_percent: int | None = Field(None, ge=5, le=15)


@router.post("/road-reports/{report_id}/review", response_model=RoadReportOut)
def review_road_report(report_id: int, payload: RoadReportReviewIn, admin: User = Depends(admin_only),
                       db: Session = Depends(get_db)):
    report = db.get(RoadReport, report_id)
    if report is None:
        raise NotFound("Signalement", report_id)
    if report.status != ReportStatus.pending:
        raise Conflict("REPORT_ALREADY_REVIEWED", "Signalement deja traite")
    if payload.approve:
        severity = payload.severity_percent or report.suggested_percent
        if severity not in (5, 10, 15):
            raise Invalid("SEVERITY_INVALID", "Severite : 5, 10 ou 15 %")
        road = DegradedRoad(name=payload.name or f"Signalement #{report.id}",
                            district=report.district or "-", lat=report.lat, lng=report.lng,
                            severity_percent=severity, validated=True, radius_m=300)
        db.add(road)
        db.flush()
        report.degraded_road_id = road.id
    report.status = ReportStatus.approved if payload.approve else ReportStatus.rejected
    report.reviewed_by = admin.id
    audit(db, admin.id, "road_report.review", "road_reports", report.id, after=payload.model_dump())
    cache.invalidate_on_commit(db, "roads:validated")
    db.commit()
    return report


# ------------------------------------------------------------------ notifications et audit

class PushIn(BaseModel):
    title: str = Field(max_length=160)
    body: str = Field(max_length=500)
    role: UserRole | None = None
    mode: str | None = Field(None, pattern="^(flexible|taxi)$")


@router.post("/notifications", status_code=201, summary="Notification globale ou ciblee (role, mode)")
def push_campaign(payload: PushIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    stmt = select(User.id).where(User.deleted_at.is_(None), User.is_active.is_(True), User.role != UserRole.admin)
    if payload.role:
        stmt = stmt.where(User.role == payload.role)
    if payload.mode:
        stmt = stmt.join(Driver, Driver.user_id == User.id).where(Driver.mode == payload.mode)
    ids = db.execute(stmt).scalars().all()
    for uid in ids:
        db.add(Notification(user_id=uid, kind="campaign", title=payload.title, body=payload.body, data={}))
    campaign = PushCampaign(title=payload.title, body=payload.body, recipients=len(ids), created_by=admin.id,
                            audience={"role": payload.role.value if payload.role else None, "mode": payload.mode})
    db.add(campaign)
    audit(db, admin.id, "push.send", "push_campaigns", None, after={**payload.model_dump(mode="json"),
                                                                     "recipients": len(ids)})
    db.commit()
    return {"id": campaign.id, "recipients": len(ids)}


@router.get("/audit-logs", summary="Journal d'audit")
def audit_logs(action: str | None = None, entity: str | None = None, limit: int = Query(100, ge=1, le=500),
               db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    return [
        {"id": a.id, "actor_id": a.actor_id, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
         "before": a.before, "after": a.after, "created_at": a.created_at}
        for a in db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit)).scalars()
    ]
