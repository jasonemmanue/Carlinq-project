from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core import cache
from app.db.session import get_db
from app.models import Ride, RideStatus, User, Zone
from app.schemas import RoadOut, ZoneOut
from app.services.pricing import haversine_km, validated_roads

ZONE_FIELDS = ("id", "name", "district", "city", "lat", "lng", "capacity", "opening_hours", "is_active")


def active_zones(db: Session) -> list[dict]:
    """Zones actives (cache Redis) ; la disponibilite est calculee en direct."""
    def load():
        zones = db.execute(select(Zone).where(Zone.is_active.is_(True)).order_by(Zone.name)).scalars()
        return [{f: getattr(z, f) for f in ZONE_FIELDS} for z in zones]
    return cache.get_or_set("zones:active", load)


router = APIRouter(tags=["zones"])


def _with_availability(db: Session, zones: list[dict], lat: float | None, lng: float | None) -> list[ZoneOut]:
    busy = dict(db.execute(
        select(Ride.taxi_zone_id, func.count(Ride.id))
        .where(Ride.status.in_((RideStatus.pending, RideStatus.accepted)), Ride.taxi_zone_id.is_not(None))
        .group_by(Ride.taxi_zone_id)
    ).all())
    out = []
    for z in zones:
        item = ZoneOut(**z)
        item.available_places = max(0, z["capacity"] - busy.get(z["id"], 0))
        if lat is not None and lng is not None:
            item.distance_m = round(haversine_km((lat, lng), (z["lat"], z["lng"])) * 1000)
        out.append(item)
    return out


@router.get("/zones", response_model=list[ZoneOut], summary="Zones de stationnement Carlinq Taxi")
def list_zones(district: str | None = None, lat: float | None = None, lng: float | None = None,
               _: User = Depends(current_user), db: Session = Depends(get_db)):
    zones = [z for z in active_zones(db) if district is None or z["district"] == district]
    items = _with_availability(db, zones, lat, lng)
    if lat is not None and lng is not None:
        items.sort(key=lambda z: z.distance_m)
    return items


@router.get("/zones/nearest", response_model=list[ZoneOut])
def nearest(lat: float, lng: float, limit: int = Query(3, ge=1, le=20), _: User = Depends(current_user),
            db: Session = Depends(get_db)):
    items = _with_availability(db, active_zones(db), lat, lng)
    return sorted(items, key=lambda z: z.distance_m)[:limit]


@router.get("/roads/degraded", response_model=list[RoadOut], summary="Routes degradees validees")
def degraded_roads(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return [RoadOut(**r) for r in validated_roads(db)]
