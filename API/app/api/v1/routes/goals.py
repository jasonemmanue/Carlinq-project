"""Objectifs hebdomadaires et partage d'objectif entre chauffeurs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_driver
from app.core.clock import utcnow, week_start
from app.core.errors import Forbidden, NotFound
from app.db.session import get_db
from app.models import Driver, GoalSettlement, GoalShare, GoalShareStatus, User, WeeklyGoal
from app.schemas import (
    GoalCreateIn, GoalOut, GoalPauseIn, HelperCandidateOut, IncomingShareOut, SettlementOut,
    ShareInviteIn, ShareOut, SplitLineOut,
)
from app.services import goals as svc
from app.services.settings import get_goals_config

router = APIRouter(prefix="/goals", tags=["goals"])


def _name(db: Session, driver_id: int) -> str:
    driver = db.get(Driver, driver_id)
    return driver.user.full_name if driver else "?"


def share_out(db: Session, share: GoalShare) -> ShareOut:
    out = ShareOut.model_validate(share)
    out.helper_name = _name(db, share.helper_driver_id)
    return out


def goal_out(db: Session, goal: WeeklyGoal) -> GoalOut:
    cfg = get_goals_config(db)
    out = GoalOut.model_validate(goal)
    out.deadline = svc.deadline(goal, cfg)
    out.shares = [share_out(db, s) for s in goal.shares]
    out.projected_split = [
        SplitLineOut(driver_id=line.driver_id, driver_name=_name(db, line.driver_id), role=line.role,
                     percent=line.percent, amount_xaf=line.amount_xaf,
                     contributed_rides=line.contributed_rides)
        for line in svc.compute_split(goal)
    ]
    return out


def _visible(goal: WeeklyGoal, driver: Driver):
    helpers = {s.helper_driver_id for s in goal.shares}
    if goal.driver_id != driver.id and driver.id not in helpers:
        raise Forbidden("GOAL_NOT_VISIBLE", "Objectif non accessible")


@router.get("/config", summary="Paliers de bonus et regles de partage")
def config(_: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    return get_goals_config(db).model_dump()


@router.post("", response_model=GoalOut, status_code=201, summary="Fixer son objectif de la semaine")
def create(payload: GoalCreateIn, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goal = svc.create_goal(db, driver, payload.target_rides)
    db.commit()
    return goal_out(db, goal)


@router.get("/current", response_model=GoalOut | None, summary="Objectif de la semaine en cours")
def current(driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goal = svc.goal_of(db, driver.id, week_start(utcnow()))
    if goal is None:
        return None
    svc.refresh(goal, utcnow())
    db.commit()
    return goal_out(db, goal)


@router.get("/history", response_model=list[GoalOut])
def history(limit: int = Query(12, ge=1, le=52), driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goals = db.execute(select(WeeklyGoal).where(WeeklyGoal.driver_id == driver.id)
                       .order_by(WeeklyGoal.week_start.desc()).limit(limit)).scalars().all()
    return [goal_out(db, g) for g in goals]


@router.get("/shares/incoming", response_model=list[IncomingShareOut],
            summary="Demandes de partage recues (en tant qu'aidant)")
def incoming(status: GoalShareStatus | None = None, driver: Driver = Depends(current_driver),
             db: Session = Depends(get_db)):
    stmt = select(GoalShare).where(GoalShare.helper_driver_id == driver.id)
    if status:
        stmt = stmt.where(GoalShare.status == status)
    result = []
    for share in db.execute(stmt.order_by(GoalShare.id.desc())).scalars():
        goal = share.goal
        base = share_out(db, share).model_dump()
        result.append(IncomingShareOut(
            **base, owner_driver_id=goal.driver_id, owner_name=_name(db, goal.driver_id),
            goal_target_rides=goal.target_rides, goal_progress_rides=goal.progress_rides,
            goal_bonus_xaf=goal.bonus_xaf, projected_amount_xaf=goal.bonus_xaf * share.percent // 100,
        ))
    return result


@router.post("/shares/{share_id}/accept", response_model=ShareOut, summary="Accepter d'aider un chauffeur")
def accept(share_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    share = svc.respond_share(db, driver, share_id, accept=True)
    db.commit()
    return share_out(db, share)


@router.post("/shares/{share_id}/decline", response_model=ShareOut)
def decline(share_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    share = svc.respond_share(db, driver, share_id, accept=False)
    db.commit()
    return share_out(db, share)


@router.post("/shares/{share_id}/cancel", response_model=ShareOut,
             summary="Annuler une invitation (proprietaire, avant toute course apportee)")
def cancel(share_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    share = svc.cancel_share(db, driver, share_id)
    db.commit()
    return share_out(db, share)


@router.get("/{goal_id}", response_model=GoalOut)
def detail(goal_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goal = svc.get_goal(db, goal_id)
    _visible(goal, driver)
    return goal_out(db, goal)


@router.post("/{goal_id}/pause", response_model=GoalOut, summary="Pause d'objectif (72 h max, une fois)")
def pause(goal_id: int, payload: GoalPauseIn, driver: Driver = Depends(current_driver),
          db: Session = Depends(get_db)):
    goal = svc.pause_goal(db, driver, svc.get_goal(db, goal_id), payload.hours)
    db.commit()
    return goal_out(db, goal)


@router.get("/{goal_id}/helpers", response_model=list[HelperCandidateOut],
            summary="Chauffeurs ayant atteint leur objectif, invitables")
def helpers(goal_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goal = svc.get_goal(db, goal_id)
    if goal.driver_id != driver.id:
        raise Forbidden("NOT_GOAL_OWNER", "Seul le proprietaire peut chercher des aidants")
    return [
        HelperCandidateOut(driver_id=d.id, full_name=d.user.full_name, points=d.points,
                           rating_avg=d.user.rating_avg, goal_target_rides=g.target_rides,
                           goal_progress_rides=g.progress_rides)
        for d, g in svc.eligible_helpers(db, goal)
    ]


@router.post("/{goal_id}/shares", response_model=ShareOut, status_code=201,
             summary="Partager son objectif avec un aidant (pourcentage du bonus)")
def invite(goal_id: int, payload: ShareInviteIn, driver: Driver = Depends(current_driver),
           db: Session = Depends(get_db)):
    share = svc.invite_helper(db, driver, goal_id, payload.helper_driver_id, payload.percent, payload.message)
    db.commit()
    return share_out(db, share)


@router.get("/{goal_id}/settlement", response_model=SettlementOut, summary="Detail du versement")
def settlement(goal_id: int, driver: Driver = Depends(current_driver), db: Session = Depends(get_db)):
    goal = svc.get_goal(db, goal_id)
    _visible(goal, driver)
    row = db.execute(select(GoalSettlement).where(GoalSettlement.goal_id == goal.id)).scalar_one_or_none()
    if row is None:
        raise NotFound("Versement", goal_id)
    return row
