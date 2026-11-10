"""Schemas Pydantic (entrees / sorties de l'API), un module par domaine."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    CarlinqMode, DisputeStatus, DocumentStatus, DriverValidation, GoalShareStatus, GoalStatus,
    PaymentMethod, ReportStatus, RideStatus, ServiceClass, UserRole,
)

XAF = int  # tous les montants sont des entiers XAF


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    total: int
    items: list


# ------------------------------------------------------------------ auth / users

class SignupIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=20, pattern=r"^\+?[0-9 ]+$")
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    role: UserRole

    @field_validator("role")
    @classmethod
    def _no_admin(cls, v: UserRole):
        if v == UserRole.admin:
            raise ValueError("Le role admin ne peut pas etre choisi a l'inscription")
        return v

    @field_validator("full_name", "password")
    @classmethod
    def _no_nul(cls, v: str):
        if "\x00" in v:
            raise ValueError("Caractere interdit")
        return v


class LoginIn(BaseModel):
    phone: str = Field(max_length=20, pattern=r"^\+?[0-9 ]+$")
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(ORM):
    id: int
    full_name: str
    phone: str
    email: str | None
    role: UserRole
    language: str
    wallet_balance_xaf: XAF
    home_address: str | None
    home_lat: float | None
    home_lng: float | None
    rating_avg: float
    rating_count: int
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    language: str | None = Field(None, pattern="^(fr|en)$")
    home_address: str | None = Field(None, max_length=255)
    home_lat: float | None = Field(None, ge=-90, le=90)
    home_lng: float | None = Field(None, ge=-180, le=180)


# ------------------------------------------------------------------ drivers

class DriverRegisterIn(BaseModel):
    mode: CarlinqMode
    service_class: ServiceClass | None = None
    vehicle_brand: str = Field(max_length=80)
    vehicle_model: str = Field(max_length=80)
    vehicle_color: str | None = Field(None, max_length=40)
    vehicle_plate: str = Field(max_length=20)
    company_type: str | None = Field(None, max_length=80)


class DriverOut(ORM):
    id: int
    user_id: int
    mode: CarlinqMode
    service_class: ServiceClass | None
    validation_status: DriverValidation
    vehicle_brand: str
    vehicle_model: str
    vehicle_color: str | None
    vehicle_plate: str
    company_type: str | None
    points: int
    completed_rides: int
    online: bool
    lat: float | None
    lng: float | None
    premium_until: datetime | None


class DriverPublicOut(ORM):
    id: int
    mode: CarlinqMode
    service_class: ServiceClass | None
    vehicle_brand: str
    vehicle_model: str
    vehicle_color: str | None
    vehicle_plate: str
    lat: float | None
    lng: float | None


class OnlineIn(BaseModel):
    online: bool


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class DocumentIn(BaseModel):
    doc_type: str = Field(pattern="^(cni|permis|carte_grise|assurance|autre)$")
    file_url: str = Field(max_length=500)


class DocumentOut(ORM):
    id: int
    driver_id: int
    doc_type: str
    file_url: str
    status: DocumentStatus
    rejection_reason: str | None
    created_at: datetime


class PointEventOut(ORM):
    id: int
    delta: int
    kind: str
    reason: str | None
    ride_id: int | None
    balance_after: int
    created_at: datetime


class RouteIn(BaseModel):
    origin: str = Field(max_length=160)
    waypoints: list[str] = Field(default_factory=list, max_length=10)
    destination: str = Field(max_length=160)
    avoid_traffic: bool = True
    avoid_degraded: bool = True


class RouteOut(ORM):
    id: int
    day: date
    slot: int
    origin: str
    waypoints: list[str]
    destination: str
    avoid_traffic: bool
    avoid_degraded: bool


class PremiumIn(BaseModel):
    channel: str = Field("wallet", pattern="^(wallet|orange_money|mtn_momo)$")
    months: int = Field(1, ge=1, le=12)


# ------------------------------------------------------------------ goals

class GoalCreateIn(BaseModel):
    target_rides: int = Field(gt=0, le=500)


class GoalPauseIn(BaseModel):
    hours: int = Field(ge=1, le=72)


class ShareInviteIn(BaseModel):
    helper_driver_id: int
    percent: int = Field(ge=1, le=99)
    message: str | None = Field(None, max_length=300)


class ShareOut(ORM):
    id: int
    goal_id: int
    helper_driver_id: int
    helper_name: str | None = None
    percent: int
    status: GoalShareStatus
    contributed_rides: int
    message: str | None
    responded_at: datetime | None
    created_at: datetime


class SplitLineOut(BaseModel):
    driver_id: int
    driver_name: str | None = None
    role: str
    percent: int
    amount_xaf: XAF
    contributed_rides: int


class GoalOut(ORM):
    id: int
    driver_id: int
    driver_name: str
    week_start: date
    target_rides: int
    bonus_xaf: XAF
    own_rides: int
    shared_rides: int
    progress_rides: int
    status: GoalStatus
    paused_until: datetime | None
    pause_used: bool
    achieved_at: datetime | None
    deadline: datetime | None = None
    shares: list[ShareOut] = []
    projected_split: list[SplitLineOut] = []


class HelperCandidateOut(BaseModel):
    driver_id: int
    full_name: str
    points: int
    rating_avg: float
    goal_target_rides: int
    goal_progress_rides: int


class SettlementLineOut(ORM):
    driver_id: int
    driver_name: str
    role: str
    percent: int
    amount_xaf: XAF
    wallet_tx_id: int | None


class SettlementOut(ORM):
    id: int
    goal_id: int
    week_start: date
    bonus_xaf: XAF
    shared_total_xaf: XAF
    owner_net_xaf: XAF
    created_at: datetime
    lines: list[SettlementLineOut]


class IncomingShareOut(ShareOut):
    owner_driver_id: int
    owner_name: str
    goal_target_rides: int
    goal_progress_rides: int
    goal_bonus_xaf: XAF
    projected_amount_xaf: XAF


# ------------------------------------------------------------------ rides

class PlaceIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    label: str | None = Field(None, max_length=200)


class EstimateIn(BaseModel):
    mode: CarlinqMode
    service_class: ServiceClass | None = None
    pickup: PlaceIn
    destination: PlaceIn
    stops: list[PlaceIn] = Field(default_factory=list, max_length=20)
    places: int = Field(1, ge=1, le=4)


class RideCreateIn(EstimateIn):
    payment_method: PaymentMethod = PaymentMethod.wallet
    taxi_zone_id: int | None = None


class EstimateOut(BaseModel):
    distance_km: float
    base_xaf: XAF
    places_supplement_xaf: XAF
    stop_supplement_per_stop_xaf: XAF
    stop_supplement_xaf: XAF
    degraded_percent: int
    degraded_supplement_xaf: XAF
    total_xaf: XAF
    commission_xaf: XAF


class StopOut(ORM):
    id: int
    order_index: int
    label: str | None
    lat: float
    lng: float
    supplement_xaf: XAF
    passed_at: datetime | None


class PauseOut(ORM):
    id: int
    started_at: datetime
    ended_at: datetime | None
    lat: float
    lng: float
    supplement_xaf: XAF


class TrafficOut(ORM):
    id: int
    started_at: datetime
    ended_at: datetime | None
    source: str
    supplement_xaf: XAF


class RideOut(ORM):
    id: int
    passenger_id: int
    passenger_name: str
    passenger_rating: float
    driver_id: int | None
    driver_name: str | None
    driver_rating: float | None
    vehicle: str | None
    vehicle_plate: str | None
    mode: CarlinqMode
    service_class: ServiceClass | None
    status: RideStatus
    places: int
    taxi_zone_id: int | None
    pickup_label: str | None
    pickup_lat: float
    pickup_lng: float
    destination_label: str | None
    destination_lat: float
    destination_lng: float
    distance_km: float
    base_xaf: XAF
    places_supplement_xaf: XAF
    stop_supplement_xaf: XAF
    degraded_percent: int
    degraded_supplement_xaf: XAF
    traffic_supplement_xaf: XAF
    pause_supplement_xaf: XAF
    total_xaf: XAF
    commission_xaf: XAF
    driver_earning_xaf: XAF
    cancellation_fee_xaf: XAF
    payment_method: PaymentMethod
    payment_status: str
    eta_seconds: int | None
    created_at: datetime
    accepted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    stops: list[StopOut]
    pauses: list[PauseOut]
    traffic_events: list[TrafficOut]


class AcceptIn(BaseModel):
    eta_seconds: int | None = Field(None, ge=0, le=7200)


class PauseStartIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class TrafficStartIn(BaseModel):
    source: str = Field("manual", pattern="^(manual|auto)$")


class GpsPointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    speed_kmh: float | None = None
    recorded_at: datetime | None = None


class GpsIn(BaseModel):
    points: list[GpsPointIn] = Field(min_length=1, max_length=500)


class CompleteIn(BaseModel):
    cash_received: bool = False


class CancelIn(BaseModel):
    reason: str | None = Field(None, max_length=300)


class RateIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=10)
    comment: str | None = Field(None, max_length=1000)


class MessageIn(BaseModel):
    text: str | None = Field(None, max_length=1000)
    photo_url: str | None = Field(None, max_length=500)


class MessageOut(ORM):
    id: int
    ride_id: int
    sender_id: int
    text: str | None
    photo_url: str | None
    created_at: datetime


# ------------------------------------------------------------------ wallet

class TopupIn(BaseModel):
    amount_xaf: XAF = Field(ge=500, le=1_000_000)
    channel: str = Field(pattern="^(orange_money|mtn_momo)$")
    phone: str | None = None


class WithdrawIn(BaseModel):
    amount_xaf: XAF = Field(ge=500, le=5_000_000)
    channel: str = Field(pattern="^(orange_money|mtn_momo)$")
    phone: str | None = None


class WalletTxOut(ORM):
    id: int
    amount_xaf: XAF
    balance_after_xaf: XAF
    kind: str
    label: str | None
    channel: str | None
    ride_id: int | None
    goal_settlement_id: int | None
    created_at: datetime


class WalletOut(BaseModel):
    balance_xaf: XAF
    min_balance_xaf: XAF
    can_order: bool


# ------------------------------------------------------------------ zones / roads / disputes

class ZoneIn(BaseModel):
    name: str = Field(max_length=120)
    district: str = Field(max_length=120)
    city: str = "Douala"
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    capacity: int = Field(6, ge=1, le=100)
    opening_hours: str = "24h/24"
    is_active: bool = True


class ZoneOut(ORM):
    id: int
    name: str
    district: str
    city: str
    lat: float
    lng: float
    capacity: int
    opening_hours: str
    is_active: bool
    distance_m: int | None = None
    available_places: int | None = None


class RoadIn(BaseModel):
    name: str = Field(max_length=160)
    district: str = Field(max_length=120)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(300, ge=50, le=5000)
    severity_percent: Literal[5, 10, 15]
    validated: bool = True


class RoadOut(ORM):
    id: int
    name: str
    district: str
    lat: float
    lng: float
    radius_m: int
    severity_percent: int
    validated: bool


class RoadReportIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    district: str | None = None
    suggested_percent: Literal[5, 10, 15]


class RoadReportOut(ORM):
    id: int
    driver_id: int
    district: str | None
    lat: float
    lng: float
    suggested_percent: int
    status: ReportStatus
    degraded_road_id: int | None
    created_at: datetime


class DisputeIn(BaseModel):
    ride_id: int
    kind: str = Field(pattern="^(pause_arret|price|behaviour|lost_item|other)$")
    description: str | None = Field(None, max_length=1000)


class DisputeOut(ORM):
    id: int
    ride_id: int
    kind: str
    opened_by: int
    description: str | None
    status: DisputeStatus
    refund_xaf: XAF
    points_penalty: int
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime


class NotificationOut(ORM):
    id: int
    kind: str
    title: str
    body: str
    data: dict
    read_at: datetime | None
    created_at: datetime
