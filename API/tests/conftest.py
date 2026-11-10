"""Tests d'integration sur une vraie base PostgreSQL (TEST_DATABASE_URL).

    docker compose run --rm api pytest

Le schema est cree par les migrations Alembic (on teste donc aussi la migration),
puis chaque test repart de tables vides.
"""
import itertools
import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ["PAYMENTS_SIMULATED"] = "true"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="carlinq-tests-")

TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.exit("TEST_DATABASE_URL manquant (lancer via docker compose run --rm api pytest)", 2)

from app.core import cache, ratelimit  # noqa: E402
from app.core.config import settings as app_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402

# Cache des tests isole (base Redis 1) des donnees de demo (base 0).
app_settings.REDIS_URL = os.environ.get("TEST_REDIS_URL", "")
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    CarlinqMode, Driver, DriverValidation, ServiceClass, User, UserRole,
)

engine = create_engine(TEST_URL)
TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
PASSWORD = "Password123!"
_phones = itertools.count(1)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    os.environ["DATABASE_URL"] = TEST_URL
    from app.core import config as app_config
    app_config.settings.DATABASE_URL = TEST_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        )).scalars().all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    cache.flush_all()
    ratelimit.clear_local()
    yield


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    def _get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class Actor:
    """Utilisateur de test avec son jeton."""

    def __init__(self, client: TestClient, user_id: int, phone: str, token: str, driver_id: int | None = None):
        self.client, self.id, self.phone, self.token, self.driver_id = client, user_id, phone, token, driver_id

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url, json=None, **kw):
        return self.client.post(url, json=json, headers=self.headers, **kw)

    def put(self, url, json=None, **kw):
        return self.client.put(url, json=json, headers=self.headers, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self.headers, **kw)

    def patch(self, url, json=None, **kw):
        return self.client.patch(url, json=json, headers=self.headers, **kw)


def _phone() -> str:
    return f"+2376{next(_phones):08d}"


@pytest.fixture
def make_admin(client, db):
    def _make() -> Actor:
        phone = _phone()
        user = User(full_name="Admin Test", phone=phone, password_hash=hash_password(PASSWORD),
                    role=UserRole.admin, wallet_balance_xaf=0)
        db.add(user)
        db.commit()
        token = client.post("/api/v1/auth/login", json={"phone": phone, "password": PASSWORD}).json()["access_token"]
        return Actor(client, user.id, phone, token)
    return _make


@pytest.fixture
def admin(make_admin):
    return make_admin()


@pytest.fixture
def make_passenger(client):
    def _make(name="Passager Test", balance=0) -> Actor:
        phone = _phone()
        r = client.post("/api/v1/auth/signup", json={"full_name": name, "phone": phone, "password": PASSWORD,
                                                     "role": "passenger"})
        assert r.status_code == 201, r.text
        actor = Actor(client, r.json()["user"]["id"], phone, r.json()["access_token"])
        if balance:
            assert actor.post("/api/v1/wallet/topup", {"amount_xaf": balance, "channel": "orange_money"}).status_code == 201
        return actor
    return _make


@pytest.fixture
def make_driver(client, db):
    """Chauffeur inscrit par l'API, puis valide directement en base (Pack Premium si Copilote)."""
    plates = itertools.count(1)

    def _make(name="Chauffeur Test", role="drivers", mode="flexible", service_class="eco",
              approved=True) -> Actor:
        phone = _phone()
        r = client.post("/api/v1/auth/signup", json={"full_name": name, "phone": phone, "password": PASSWORD,
                                                     "role": role})
        assert r.status_code == 201, r.text
        actor = Actor(client, r.json()["user"]["id"], phone, r.json()["access_token"])
        body = {"mode": mode, "service_class": service_class if mode == "flexible" else None,
                "vehicle_brand": "Toyota", "vehicle_model": "Corolla", "vehicle_plate": f"LT {next(plates):04d} T",
                "company_type": "Societe de transport" if role == "copilote" else None}
        r = actor.post("/api/v1/drivers/me", body)
        assert r.status_code == 201, r.text
        actor.driver_id = r.json()["id"]
        if approved:
            driver = db.get(Driver, actor.driver_id)
            driver.validation_status = DriverValidation.approved
            driver.online = True
            db.commit()
        return actor
    return _make


RIDE = {
    "mode": "flexible", "service_class": "eco",
    "pickup": {"lat": 4.0500, "lng": 9.7000, "label": "Akwa"},
    "destination": {"lat": 4.0700, "lng": 9.7300, "label": "Bonamoussadi"},
    "stops": [], "places": 1, "payment_method": "wallet",
}


def run_ride(passenger: Actor, driver: Actor, **overrides) -> dict:
    """Course complete via l'API : commande -> acceptation -> depart -> fin."""
    body = {**RIDE, **overrides}
    r = passenger.post("/api/v1/rides", body)
    assert r.status_code == 201, r.text
    ride_id = r.json()["id"]
    assert driver.post(f"/api/v1/rides/{ride_id}/accept", {"eta_seconds": 240}).status_code == 200
    assert driver.post(f"/api/v1/rides/{ride_id}/start").status_code == 200
    r = driver.post(f"/api/v1/rides/{ride_id}/complete", {"cash_received": body["payment_method"] == "cash"})
    assert r.status_code == 200, r.text
    return r.json()
