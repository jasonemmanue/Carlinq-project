"""Donnees de demonstration (developpement local uniquement).

    python -m app.seed

Idempotent : ne fait rien si l'administrateur de demo existe deja.
Hors developpement, le mot de passe vient de SEED_DEMO_PASSWORD ; le definir
le change aussi sur les comptes de demo deja crees.
Comptes :
    admin            +237600000000
    passagers        +237690000001 .. +237690000003
    chauffeurs       +237670000001 .. +237670000006
"""
import os
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utcnow, week_start
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (
    AppSetting, CarlinqMode, DegradedRoad, Driver, DriverValidation, GoalShare, GoalShareStatus,
    GoalStatus, ServiceClass, User, UserRole, WeeklyGoal, Zone,
)
from app.services.settings import GoalsConfig, PricingConfig

PASSWORD = os.environ.get("SEED_DEMO_PASSWORD") or (
    "Carlinq2026!" if settings.APP_ENV == "development" else "")

PASSENGERS = [
    ("Emmanuel Saka", "+237690000001", 25000),
    ("Aline Poko", "+237690000002", 8000),
    ("Yves Sime", "+237690000003", 400),
]

# nom, telephone, role, mode, classe, vehicule, plaque, objectif (cible, courses)
DRIVERS = [
    ("Kevin Kamga", "+237670000001", UserRole.drivers, CarlinqMode.flexible, ServiceClass.serenity,
     ("Toyota", "Camry", "Gris"), "LT 8342 A", (50, 32)),
    ("Prisca Lema", "+237670000002", UserRole.drivers, CarlinqMode.flexible, ServiceClass.eco,
     ("Toyota", "Corolla", "Blanc"), "LT 1207 B", (30, 30)),
    ("Ekue Atangana", "+237670000003", UserRole.drivers, CarlinqMode.flexible, ServiceClass.serenity,
     ("Hyundai", "Accent", "Noir"), "CE 5531 C", (50, 50)),
    ("Samuel Ngo", "+237670000004", UserRole.copilote, CarlinqMode.flexible, ServiceClass.prestige,
     ("Toyota", "Prado", "Noir"), "LT 9900 D", (30, 12)),
    ("Transports Wouri SARL", "+237670000005", UserRole.copilote, CarlinqMode.taxi, None,
     ("Renault", "Logan", "Jaune"), "LT 4410 E", (50, 41)),
    ("Blaise Fotso", "+237670000006", UserRole.drivers, CarlinqMode.taxi, None,
     ("Dacia", "Logan", "Jaune"), "OU 3322 F", (30, 30)),
]

ZONES = [
    ("Rue Joss", "Akwa", 4.0511, 9.7043, 8, "05:00 - 23:00"),
    ("Boulevard de la Liberte", "Akwa", 4.0480, 9.6990, 10, "24h/24"),
    ("Boulevard Bonapriso", "Bonapriso", 4.0330, 9.6920, 5, "06:00 - 22:00"),
    ("Marche Deido", "Deido", 4.0660, 9.7090, 12, "05:00 - 21:00"),
    ("Hopital Laquintinie", "Bali", 4.0440, 9.7020, 6, "24h/24"),
    ("Rond-point Deido", "Deido", 4.0620, 9.7150, 8, "05:30 - 22:30"),
    ("Carrefour Ndokoti", "Ndokoti", 4.0450, 9.7420, 15, "24h/24"),
]

ROADS = [
    ("Rue des ecoles - Nkololoun", "Nkololoun", 4.0380, 9.7200, 10),
    ("Piste Bonamoussadi Nord", "Bonamoussadi", 4.0900, 9.7400, 15),
    ("Rue Deido Plage", "Deido", 4.0700, 9.7050, 5),
]


def run() -> None:
    db = SessionLocal()
    try:
        if not PASSWORD:
            print("SEED_DEMO_PASSWORD absent : donnees de demo ignorees.")
            return
        if db.execute(select(User).where(User.phone == "+237600000000")).scalar_one_or_none():
            if os.environ.get("SEED_DEMO_PASSWORD"):
                phones = ["+237600000000", *(p[1] for p in PASSENGERS), *(d[1] for d in DRIVERS)]
                new_hash = hash_password(PASSWORD)
                for user in db.execute(select(User).where(User.phone.in_(phones))).scalars():
                    user.password_hash = new_hash
                db.commit()
                print("Mot de passe des comptes de demo mis a jour.")
            else:
                print("Donnees de demo deja presentes.")
            return
        pw = hash_password(PASSWORD)
        now = utcnow()

        db.add(AppSetting(key="pricing", value=PricingConfig().model_dump(), updated_at=now))
        db.add(AppSetting(key="goals", value=GoalsConfig().model_dump(), updated_at=now))

        db.add(User(full_name="Admin Carlinq", phone="+237600000000", email="admin@carlinq.local",
                    password_hash=pw, role=UserRole.admin, wallet_balance_xaf=0))
        for name, phone, balance in PASSENGERS:
            db.add(User(full_name=name, phone=phone, password_hash=pw, role=UserRole.passenger,
                        wallet_balance_xaf=balance, home_address="Douala, Akwa - Rue 12",
                        home_lat=4.0500, home_lng=9.7000))

        week = week_start(now)
        goals = {}
        for i, (name, phone, role, mode, cls, (brand, model, color), plate, (target, done)) in enumerate(DRIVERS):
            user = User(full_name=name, phone=phone, password_hash=pw, role=role, wallet_balance_xaf=15000)
            db.add(user)
            db.flush()
            driver = Driver(user_id=user.id, mode=mode, service_class=cls,
                            validation_status=DriverValidation.approved, vehicle_brand=brand,
                            vehicle_model=model, vehicle_color=color, vehicle_plate=plate,
                            company_type="Societe de transport" if role == UserRole.copilote else None,
                            points=82 - i * 3, completed_rides=300 + i * 40, online=i % 2 == 0,
                            lat=4.05 + i * 0.004, lng=9.70 + i * 0.004, refusal_seconds_used=0,
                            premium_until=now + timedelta(days=20) if role == UserRole.copilote else None)
            db.add(driver)
            db.flush()
            achieved = done >= target
            goal = WeeklyGoal(driver_id=driver.id, week_start=week, target_rides=target,
                              bonus_xaf=GoalsConfig().bonus_for(target), own_rides=done, shared_rides=0,
                              status=GoalStatus.achieved if achieved else GoalStatus.active,
                              achieved_at=now if achieved else None, pause_used=False)
            db.add(goal)
            db.flush()
            goals[name] = (driver, goal)

        # Demo de partage : Kevin (32/50) a invite Prisca (acceptee, 3 courses
        # apportees) et Ekue (en attente).
        kevin_goal = goals["Kevin Kamga"][1]
        db.add(GoalShare(goal_id=kevin_goal.id, helper_driver_id=goals["Prisca Lema"][0].id, percent=20,
                         status=GoalShareStatus.accepted, contributed_rides=3, responded_at=now,
                         message="Il me manque quelques courses, merci !"))
        db.add(GoalShare(goal_id=kevin_goal.id, helper_driver_id=goals["Ekue Atangana"][0].id, percent=15,
                         status=GoalShareStatus.pending, contributed_rides=0))
        kevin_goal.shared_rides = 3

        for name, district, lat, lng, cap, hours in ZONES:
            db.add(Zone(name=name, district=district, lat=lat, lng=lng, capacity=cap, opening_hours=hours))
        for name, district, lat, lng, sev in ROADS:
            db.add(DegradedRoad(name=name, district=district, lat=lat, lng=lng, severity_percent=sev,
                                radius_m=400, validated=True))
        db.commit()
        print("Donnees de demo creees.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
