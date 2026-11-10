"""Non-regression de l'audit de securite."""
from app.core.config import settings
from tests.conftest import PASSWORD, RIDE

LOGIN = "/api/v1/auth/login"


def test_login_blocked_after_five_failures(client, make_passenger):
    p = make_passenger()
    for _ in range(5):
        assert client.post(LOGIN, json={"phone": p.phone, "password": "mauvais"}).status_code == 401
    r = client.post(LOGIN, json={"phone": p.phone, "password": PASSWORD})
    assert r.status_code == 429 and r.headers["Retry-After"]
    assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_success_resets_failures(client, make_passenger):
    p = make_passenger()
    for _ in range(4):
        client.post(LOGIN, json={"phone": p.phone, "password": "mauvais"})
    assert client.post(LOGIN, json={"phone": p.phone, "password": PASSWORD}).status_code == 200
    assert client.post(LOGIN, json={"phone": p.phone, "password": "mauvais"}).status_code == 401
    assert client.post(LOGIN, json={"phone": p.phone, "password": PASSWORD}).status_code == 200


def test_no_money_without_provider(monkeypatch, make_passenger, make_driver):
    p = make_passenger(balance=5000)
    d = make_driver(role="copilote")
    monkeypatch.setattr(settings, "PAYMENTS_SIMULATED", False)
    body = {"amount_xaf": 1000, "channel": "orange_money"}
    for actor, url in ((p, "topup"), (d, "withdraw")):
        r = actor.post(f"/api/v1/wallet/{url}", body)
        assert r.status_code == 503 and r.json()["error"]["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"
    assert d.post("/api/v1/drivers/me/premium", {"channel": "orange_money"}).status_code == 503
    assert p.post("/api/v1/rides", {**RIDE, "payment_method": "mtn_momo"}).status_code == 503
    assert p.post("/api/v1/rides", RIDE).status_code == 201


def test_validation_never_echoes_input(client):
    r = client.post(LOGIN, json={"phone": "x" * 30, "password": "secret-a-ne-pas-renvoyer"})
    assert r.status_code == 422 and "secret-a-ne-pas-renvoyer" not in r.text
    assert r.json()["detail"][0]["msg"]


def test_bad_inputs_are_not_server_errors(client, admin, make_passenger):
    p = make_passenger()
    assert client.post(LOGIN, json={"phone": "+237\x000", "password": "x"}).status_code == 422
    assert p.get("/api/v1/rides?limit=-1").status_code == 422
    assert p.get("/api/v1/wallet/transactions?limit=-1").status_code == 422
    assert p.get("/api/v1/users/me/notifications?limit=0").status_code == 422
    assert p.get("/api/v1/rides/99999999999999999999").status_code == 422
    assert admin.get("/api/v1/admin/users?offset=-1").status_code == 422
    assert admin.get("/api/v1/admin/users?q=%00").status_code == 422


def test_zones_with_partial_coordinates(admin, make_passenger):
    p = make_passenger()
    for name in ("A", "B"):
        assert admin.post("/api/v1/admin/zones", {"name": name, "district": "Akwa", "lat": 4.05, "lng": 9.7}).status_code == 201
    assert p.get("/api/v1/zones?lat=4.05").status_code == 200


def test_accept_requires_online_and_class(make_passenger, make_driver, db):
    from app.models import Driver
    p = make_passenger(balance=50000)
    eco = make_driver(service_class="eco")
    ride = p.post("/api/v1/rides", {**RIDE, "service_class": "prestige"}).json()
    r = eco.post(f"/api/v1/rides/{ride['id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RIDE_CLASS_MISMATCH"
    p.post(f"/api/v1/rides/{ride['id']}/cancel")
    ride = p.post("/api/v1/rides", RIDE).json()
    db.get(Driver, eco.driver_id).online = False
    db.commit()
    assert eco.get("/api/v1/rides/offers").json() == []
    r = eco.post(f"/api/v1/rides/{ride['id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DRIVER_OFFLINE"


def test_flexible_requires_class(make_passenger):
    p = make_passenger(balance=50000)
    body = {**RIDE, "service_class": None}
    assert p.post("/api/v1/rides", body).status_code == 422


def test_driver_cannot_delete_account_during_ride(make_passenger, make_driver, db):
    from app.models import Driver
    p = make_passenger(balance=50000)
    d = make_driver()
    ride = p.post("/api/v1/rides", RIDE).json()
    assert d.post(f"/api/v1/rides/{ride['id']}/accept").status_code == 200
    assert d.delete("/api/v1/users/me").status_code == 409
    assert p.post(f"/api/v1/rides/{ride['id']}/cancel").status_code == 200
    assert d.delete("/api/v1/users/me").status_code == 204
    db.expire_all()
    assert db.get(Driver, d.driver_id).online is False


def test_refresh_refused_while_suspended(client, admin, make_passenger):
    p = make_passenger()
    refresh = client.post(LOGIN, json={"phone": p.phone, "password": PASSWORD}).json()["refresh_token"]
    assert admin.post(f"/api/v1/admin/users/{p.id}/suspend", {"hours": 48, "reason": "Test"}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 403


def test_message_without_driver(admin, make_passenger):
    p = make_passenger(balance=50000)
    ride = p.post("/api/v1/rides", RIDE).json()
    assert admin.post(f"/api/v1/rides/{ride['id']}/messages", {"text": "Bonjour"}).status_code == 409


def test_cors_and_security_headers(client):
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


PDF = b"%PDF-1.7 contenu"


def _upload(actor, url, data=PDF, name="piece.pdf", form=None):
    return actor.client.post(url, headers=actor.headers, data=form or {}, files={"file": (name, data)})


def test_document_upload_is_private(admin, make_driver):
    owner, other = make_driver(), make_driver()
    url = "/api/v1/drivers/me/documents/upload"
    r = _upload(owner, url, form={"doc_type": "permis"})
    assert r.status_code == 201, r.text
    file_url = r.json()["file_url"]
    assert file_url.endswith(".pdf") and "piece" not in file_url
    assert owner.client.get(file_url).status_code == 401
    assert owner.get(file_url).content == PDF
    assert admin.get(file_url).status_code == 200
    assert other.get(file_url).status_code == 403


def test_upload_rejects_bad_files(monkeypatch, make_driver):
    from app.services import storage
    d = make_driver()
    url = "/api/v1/drivers/me/documents/upload"
    assert _upload(d, url, b"<script>alert(1)</script>", "x.pdf", {"doc_type": "cni"}).status_code == 422
    assert _upload(d, url, form={"doc_type": "virus"}).status_code == 422
    monkeypatch.setattr(storage, "MAX_BYTES", 4)
    assert _upload(d, url, form={"doc_type": "cni"}).status_code == 422


def test_message_photo_for_participants_only(make_passenger, make_driver):
    p, intruder = make_passenger(balance=50000), make_passenger()
    d = make_driver()
    ride = p.post("/api/v1/rides", RIDE).json()
    url = f"/api/v1/rides/{ride['id']}/messages/photo"
    png = b"\x89PNG\r\n\x1a\n image"
    assert _upload(p, url, png, "a.png").status_code == 409  # pas encore de chauffeur
    assert d.post(f"/api/v1/rides/{ride['id']}/accept").status_code == 200
    assert _upload(p, url, PDF).status_code == 422
    r = _upload(p, url, png, "a.png")
    assert r.status_code == 201, r.text
    assert d.get(r.json()["photo_url"]).status_code == 200
    assert intruder.get(r.json()["photo_url"]).status_code == 403


def test_files_cannot_escape_storage(admin):
    assert admin.get("/api/v1/files/documents/1/..%2F..%2F..%2Fetc%2Fpasswd").status_code in (403, 404)
    assert admin.get("/api/v1/files/secrets/1/x.pdf").status_code == 404


def test_upload_refused_without_volume(monkeypatch, make_driver):
    from app.services import storage
    d = make_driver()
    monkeypatch.delenv("STORAGE_DIR")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr(storage.settings, "APP_ENV", "staging")
    r = _upload(d, "/api/v1/drivers/me/documents/upload", form={"doc_type": "cni"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
