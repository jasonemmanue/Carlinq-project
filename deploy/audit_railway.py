"""Audit de bout en bout de l'API Carlinq sur l'environnement de test Railway.

    python deploy\\audit_railway.py

A lancer soi-meme : le script demande le mot de passe admin (jamais stocke).
Ecrit sur la base de TEST : comptes jetables (anonymises a la fin), courses, recharges fictives,
une notification aux chauffeurs ; baisse les paliers d'objectifs quelques secondes puis les restaure.
Ne jamais lancer sur une base contenant de vrais utilisateurs.
"""
import getpass, json, os, random, re, secrets, sys, tempfile, time, traceback, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

BASE = os.environ.get("BASE", "https://api-production-ea298.up.railway.app")
if urllib.parse.urlparse(BASE).hostname not in ("localhost", "127.0.0.1", "api-production-ea298.up.railway.app"):
    sys.exit("Refus : hote non autorise")
RUN = random.randint(1000, 9999)
ADMIN_PHONE = os.environ.get("CARLINQ_ADMIN_PHONE", "+237600000000")
PW = secrets.token_urlsafe(12) + "Aa1!"
created = []
P = "/api/v1"

records, findings = [], []


def http(method, url, body=None, token=None, headers=None, raw=None):
    h = dict(headers or {})
    data = None
    if raw is not None:
        data = raw
        h.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, text, hdr = r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, text, hdr = e.code, e.read().decode(), dict(e.headers)
    try:
        js = json.loads(text) if text else None
    except ValueError:
        js = text
    return status, js, hdr


class A:
    def __init__(self, name, phone=None, token=None, refresh=None, user=None):
        self.name, self.phone, self.token, self.refresh, self.user = name, phone, token, refresh, user
        self.driver_id = None


def login(name, phone):
    s, j, _ = http("POST", f"{P}/auth/login", {"phone": phone, "password": PW})
    assert s == 200, (name, s, j)
    return A(name, phone, j["access_token"], j["refresh_token"], j["user"])


def op(a, method, tmpl, expected, body=None, query="", **params):
    """Appel fonctionnel, enregistre pour la couverture."""
    url = P + tmpl.format(**params) + query
    s, j, _ = http(method, url, body, a.token if a else None)
    exp = expected if isinstance(expected, tuple) else (expected,)
    rec = {"method": method, "route": P + tmpl, "status": s, "expected": exp, "ok": s in exp,
           "who": a.name if a else "anon"}
    if s not in exp:
        rec["body"] = str(j)[:400]
    records.append(rec)
    return s, j


def finding(sev, key, ok, detail):
    findings.append({"sev": sev, "key": key, "ok": bool(ok), "detail": str(detail)[:500]})


RIDE = {"mode": "flexible", "service_class": "eco", "pickup": {"lat": 4.05, "lng": 9.70, "label": "Akwa"},
        "destination": {"lat": 4.07, "lng": 9.73, "label": "Bonamoussadi"}, "stops": [], "places": 1,
        "payment_method": "wallet"}
counter = {"n": 100}


def new_phone():
    counter["n"] += 1
    return f"+2376{RUN}{counter['n']:04d}"


def signup(name, role):
    ph = new_phone()
    s, j = op(None, "POST", "/auth/signup", 201, {"full_name": name, "phone": ph, "password": PW, "role": role})
    a = A(name, ph, j["access_token"], j["refresh_token"], j["user"])
    created.append(a)
    return a


def new_driver(name, admin, role="drivers", mode="flexible", cls="eco"):
    d = signup(name, role)
    counter["n"] += 1
    s, j = op(d, "POST", "/drivers/me", 201, {"mode": mode, "service_class": cls if mode == "flexible" else None,
              "vehicle_brand": "Toyota", "vehicle_model": "Yaris", "vehicle_plate": f"AU{RUN}{counter['n']}",
              "company_type": "Independant" if role == "copilote" else None})
    d.driver_id = j["id"]
    op(admin, "POST", "/admin/drivers/{id}/status", 200, {"status": "approved"}, id=d.driver_id)
    http("POST", f"{P}/drivers/me/online", {"online": True}, d.token)
    return d


def fund(a, amount):
    s, j, _ = http("POST", f"{P}/admin/users/{a.user['id']}/wallet-adjustment", {"amount_xaf": amount, "reason": "Audit"}, ctx["admin"].token)
    assert s == 200, j


def section(fn):
    try:
        fn()
    except Exception:
        finding("BUG-SCRIPT", fn.__name__, False, traceback.format_exc())


ctx = {}


def s_root():
    records.append({"method": "GET", "route": "/", "status": http("GET", "/")[0], "expected": (200,), "ok": True, "who": "anon"})
    s, j, _ = http("GET", "/healthz")
    records.append({"method": "GET", "route": "/healthz", "status": s, "expected": (200,), "ok": s == 200, "who": "anon"})
    finding("INFO", "healthz", j == {"status": "ok", "database": "ok", "cache": "ok"}, j)


def s_auth_users():
    admin = ctx["admin"]
    n = signup("Nouveau Passager", "passenger")
    ctx["np"] = n
    op(None, "POST", "/auth/signup", 409, {"full_name": "Dup", "phone": n.phone, "password": PW, "role": "passenger"})
    op(None, "POST", "/auth/signup", 422, {"full_name": "Hack", "phone": new_phone(), "password": PW, "role": "admin"})
    op(None, "POST", "/auth/login", 200, {"phone": n.phone, "password": PW})
    op(None, "POST", "/auth/login", 401, {"phone": n.phone, "password": "mauvais"})
    op(None, "POST", "/auth/refresh", 200, {"refresh_token": n.refresh})
    op(None, "POST", "/auth/refresh", 401, {"refresh_token": n.token})
    op(n, "GET", "/auth/me", 200)
    op(None, "GET", "/auth/me", 401)
    op(A("bad", token="abc.def.ghi"), "GET", "/auth/me", 401)
    s, j = op(n, "PATCH", "/users/me", 200, {"home_address": "Akwa", "home_lat": 4.05, "home_lng": 9.7, "language": "en"})
    finding("INFO", "patch_me_persist", j and j["language"] == "en", j and j.get("language"))
    op(n, "GET", "/users/me/notifications", 200)
    op(n, "POST", "/users/me/notifications/read", 204)
    # suppression
    t = signup("A Supprimer", "passenger")
    op(t, "DELETE", "/users/me", 204)
    op(t, "GET", "/auth/me", 401)
    op(None, "POST", "/auth/login", 401, {"phone": t.phone, "password": PW})
    s, _ = op(None, "POST", "/auth/refresh", 401, {"refresh_token": t.refresh})
    finding("OK?", "compte_supprime_session_coupee", s == 401, s)


def s_driver_admin():
    admin, p1, d4 = ctx["admin"], ctx["p1"], ctx["d4"]
    nd = signup("Nouveau Chauffeur", "drivers")
    body = {"mode": "flexible", "service_class": "eco", "vehicle_brand": "Kia", "vehicle_model": "Rio",
            "vehicle_plate": f"AU{RUN}Z"}
    s, j = op(nd, "POST", "/drivers/me", 201, body)
    nd.driver_id = j["id"]
    ctx["nd"] = nd
    op(nd, "POST", "/drivers/me", 409, body)
    op(nd, "POST", "/drivers/me/online", 403, {"online": True})
    op(p1, "POST", "/drivers/me", 403, body)
    op(nd, "GET", "/drivers/me", 200)
    op(nd, "GET", "/drivers/me/dashboard", 200)
    op(nd, "GET", "/drivers/me/refusal-window", 200)
    op(nd, "GET", "/drivers/me/points", 200)
    s, j = op(nd, "POST", "/drivers/me/documents", 201, {"doc_type": "permis", "file_url": "https://x.test/p.pdf"})
    doc = j["id"]
    op(nd, "GET", "/drivers/me/documents", 200)
    op(nd, "PUT", "/drivers/me/routes/{slot}", 200, {"origin": "A", "destination": "B"}, slot=1)
    op(nd, "PUT", "/drivers/me/routes/{slot}", 422, {"origin": "A", "destination": "B"}, slot=4)
    op(nd, "GET", "/drivers/me/routes", 200)
    op(nd, "DELETE", "/drivers/me/routes/{slot}", 204, slot=1)
    op(nd, "DELETE", "/drivers/me/routes/{slot}", 404, slot=1)
    op(admin, "GET", "/admin/drivers", 200, query="?status=pending")
    op(admin, "POST", "/admin/drivers/{id}/status", 422, {"status": "rejected"}, id=nd.driver_id)
    op(admin, "POST", "/admin/drivers/{id}/status", 200, {"status": "approved"}, id=nd.driver_id)
    op(nd, "POST", "/drivers/me/online", 200, {"online": True})
    op(nd, "POST", "/drivers/me/location", 204, {"lat": 4.05, "lng": 9.70})
    op(p1, "GET", "/drivers/nearby", 200, query="?lat=4.05&lng=9.7&mode=flexible")
    s, j = op(d4, "POST", "/drivers/me/premium", 200, {"channel": "wallet", "months": 1})
    s, j = op(nd, "POST", "/drivers/me/road-reports", 201, {"lat": 4.04, "lng": 9.71, "suggested_percent": 10, "district": "Bali"})
    rep = j["id"]
    op(admin, "GET", "/admin/road-reports", 200)
    op(admin, "POST", "/admin/road-reports/{id}/review", 200, {"approve": True, "name": "Rue test"}, id=rep)
    op(admin, "POST", "/admin/road-reports/{id}/review", 409, {"approve": True}, id=rep)
    op(admin, "GET", "/admin/documents", 200)
    op(admin, "POST", "/admin/documents/{id}/review", 200, {"approve": True}, id=doc)
    op(admin, "POST", "/admin/drivers/{id}/service-class", 200, {"service_class": "eco", "inspection_notes": "Controle ok"}, id=nd.driver_id)
    op(admin, "POST", "/admin/drivers/{id}/service-class", 409, {"service_class": "eco", "inspection_notes": "Controle ok"}, id=ctx["d6"].driver_id)
    op(admin, "POST", "/admin/drivers/{id}/points", 200, {"delta": 1, "reason": "Test audit"}, id=nd.driver_id)
    op(admin, "GET", "/admin/users", 200, query="?role=passenger&q=Emmanuel")
    # suspension
    t = signup("Suspendu", "passenger")
    tid = t.user["id"]
    op(admin, "POST", "/admin/users/{id}/suspend", 200, {"hours": 48, "reason": "Test audit"}, id=tid)
    op(t, "GET", "/auth/me", 403)
    op(None, "POST", "/auth/login", 403, {"phone": t.phone, "password": PW})
    s, j, _ = http("POST", f"{P}/auth/refresh", {"refresh_token": t.refresh})
    finding("MOYEN", "refresh_bloque_si_suspendu", s in (401, 403), f"POST /auth/refresh pendant suspension -> {s}")
    op(admin, "POST", "/admin/users/{id}/reactivate", 200, id=tid)
    op(t, "GET", "/auth/me", 200)
    s, j = op(admin, "POST", "/admin/users/{id}/wallet-adjustment", 200, {"amount_xaf": 1000, "reason": "Test audit"}, id=tid)
    finding("INFO", "ajustement", j and j["balance_after_xaf"] == 1000, j)
    op(admin, "POST", "/admin/users/{id}/wallet-adjustment", 422, {"amount_xaf": 0, "reason": "Test audit"}, id=tid)


def s_settings_zones():
    admin, p1 = ctx["admin"], ctx["p1"]
    op(admin, "GET", "/admin/settings/pricing", 200)
    s, j = op(admin, "PUT", "/admin/settings/pricing", 200, {"commission_percent": 8})
    op(admin, "PUT", "/admin/settings/pricing", 422, {"commission_percent": 11})
    op(admin, "GET", "/admin/settings/goals", 200)
    op(admin, "PUT", "/admin/settings/goals", 200, {"grace_days": 3})
    s, zones = op(p1, "GET", "/zones", 200)
    ctx["zone"] = zones[0]["id"]
    op(p1, "GET", "/zones/nearest", 200, query="?lat=4.05&lng=9.7")
    op(p1, "GET", "/roads/degraded", 200)
    z = {"name": "Zone Audit", "district": "Akwa", "lat": 4.05, "lng": 9.7}
    s, j = op(admin, "POST", "/admin/zones", 201, z)
    zid = j["id"]
    op(admin, "PUT", "/admin/zones/{id}", 200, {**z, "capacity": 9}, id=zid)
    s, l = op(p1, "GET", "/zones", 200)
    finding("OK?", "cache_zones_invalide", any(x["id"] == zid and x["capacity"] == 9 for x in l), "PUT zone visible")
    op(admin, "DELETE", "/admin/zones/{id}", 204, id=zid)
    r = {"name": "Route Audit", "district": "Akwa", "lat": 4.0, "lng": 9.0, "severity_percent": 5}
    s, j = op(admin, "POST", "/admin/roads", 201, r)
    rid = j["id"]
    op(admin, "PUT", "/admin/roads/{id}", 200, {**r, "severity_percent": 15}, id=rid)
    op(admin, "DELETE", "/admin/roads/{id}", 204, id=rid)


def s_wallet():
    p2, d6 = ctx["p2"], ctx["d6"]
    op(p2, "GET", "/wallet", 200)
    op(p2, "GET", "/wallet/transactions", 200)
    op(p2, "POST", "/wallet/topup", 503, {"amount_xaf": 1000, "channel": "orange_money"})
    op(p2, "POST", "/wallet/topup", 422, {"amount_xaf": 499, "channel": "orange_money"})
    op(p2, "POST", "/wallet/topup", 422, {"amount_xaf": 500.5, "channel": "orange_money"})
    op(d6, "POST", "/wallet/withdraw", 503, {"amount_xaf": 500, "channel": "mtn_momo"})


def s_ride_full():
    admin, p1, p2, d2, d3, nd = ctx["admin"], ctx["p1"], ctx["p2"], ctx["d2"], ctx["d3"], ctx["nd"]
    est = {k: RIDE[k] for k in ("mode", "service_class", "pickup", "destination", "stops", "places")}
    est["stops"] = [{"lat": 4.06, "lng": 9.71, "label": "Deido"}]
    s, e = op(p1, "POST", "/rides/estimate", 200, est)
    finding("INFO", "estimate_int", e and isinstance(e["total_xaf"], int), e)
    s0, w0 = op(p1, "GET", "/wallet", 200)
    _, dw0, _ = http("GET", f"{P}/wallet", token=d2.token)
    s, r = op(p1, "POST", "/rides", 201, {**RIDE, "stops": est["stops"]})
    rid = r["id"]
    ctx["rid"] = rid
    op(p1, "POST", "/rides", 409, RIDE)
    op(d2, "POST", "/rides", 403, RIDE)
    op(d2, "GET", "/rides/offers", 200)
    op(nd, "POST", "/rides/{id}/refuse", 200, id=rid)
    op(nd, "POST", "/rides/{id}/refuse", 409, id=rid)
    op(d2, "POST", "/rides/{id}/accept", 200, {"eta_seconds": 200}, id=rid)
    op(nd, "POST", "/rides/{id}/accept", 409, id=rid)
    op(p1, "GET", "/rides/{id}", 200, id=rid)
    op(d2, "GET", "/rides/{id}", 200, id=rid)
    op(p2, "GET", "/rides/{id}", 403, id=rid)   # IDOR passager
    op(d3, "GET", "/rides/{id}", 403, id=rid)   # IDOR chauffeur
    op(p2, "GET", "/rides/{id}/messages", 403, id=rid)
    op(d3, "POST", "/rides/{id}/start", 403, id=rid)
    op(d2, "POST", "/rides/{id}/start", 200, id=rid)
    s, full = op(p1, "GET", "/rides/{id}", 200, id=rid)
    stop = full["stops"][0]["id"]
    op(d2, "POST", "/rides/{id}/stops/{sid}/pass", 200, id=rid, sid=stop)
    op(d2, "POST", "/rides/{id}/stops/{sid}/pass", 404, id=rid, sid=99999)
    op(d2, "POST", "/rides/{id}/gps", 200, {"points": [{"lat": 4.05, "lng": 9.7, "speed_kmh": 12.5}]}, id=rid)
    op(d3, "POST", "/rides/{id}/gps", 403, {"points": [{"lat": 4.05, "lng": 9.7}]}, id=rid)
    op(d2, "POST", "/rides/{id}/pause", 201, {"lat": 4.05, "lng": 9.7}, id=rid)
    op(d2, "POST", "/rides/{id}/pause", 409, {"lat": 4.05, "lng": 9.7}, id=rid)
    op(d2, "POST", "/rides/{id}/pause/end", 200, id=rid)
    op(d2, "POST", "/rides/{id}/traffic", 201, {"source": "manual"}, id=rid)
    op(d2, "POST", "/rides/{id}/traffic/end", 200, id=rid)
    op(p1, "POST", "/rides/{id}/messages", 201, {"text": "Bonjour"}, id=rid)
    op(d2, "GET", "/rides/{id}/messages", 200, id=rid)
    op(p1, "POST", "/rides/{id}/messages", 422, {}, id=rid)
    op(p1, "POST", "/rides/{id}/complete", 403, id=rid)
    s, done = op(d2, "POST", "/rides/{id}/complete", 200, id=rid)
    op(d2, "POST", "/rides/{id}/complete", 409, id=rid)   # double fin = pas de double debit
    s1, w1 = op(p1, "GET", "/wallet", 200)
    _, dw1, _ = http("GET", f"{P}/wallet", token=d2.token)
    finding("OK?", "comptes_course_exacts",
            w0["balance_xaf"] - w1["balance_xaf"] == done["total_xaf"] and dw1["balance_xaf"] - dw0["balance_xaf"] == done["driver_earning_xaf"]
            and done["commission_xaf"] + done["driver_earning_xaf"] == done["total_xaf"],
            {"total": done["total_xaf"], "commission": done["commission_xaf"], "earning": done["driver_earning_xaf"]})
    finding("OK?", "commission_8pct", abs(done["commission_xaf"] - done["total_xaf"] * 8 / 100) <= 1, done["commission_xaf"])
    op(p1, "POST", "/rides/{id}/rate", 201, {"stars": 5, "tags": ["ponctuel"]}, id=rid)
    op(p1, "POST", "/rides/{id}/rate", 409, {"stars": 5}, id=rid)
    op(d2, "POST", "/rides/{id}/rate", 201, {"stars": 4}, id=rid)
    op(p2, "POST", "/rides/{id}/rate", 403, {"stars": 1}, id=rid)
    op(p1, "GET", "/rides", 200, query="?limit=5")
    op(p2, "POST", "/disputes", 403, {"ride_id": rid, "kind": "price"})
    s, dsp = op(p1, "POST", "/disputes", 201, {"ride_id": rid, "kind": "pause_arret", "description": "Pause abusive"})
    op(p1, "POST", "/disputes", 409, {"ride_id": rid, "kind": "pause_arret"})
    op(p1, "GET", "/disputes/mine", 200)
    op(admin, "GET", "/admin/disputes", 200)
    op(admin, "GET", "/admin/disputes/{id}/evidence", 200, id=dsp["id"])
    op(admin, "POST", "/admin/disputes/{id}/claim", 200, id=dsp["id"])
    op(admin, "POST", "/admin/disputes/{id}/claim", 409, id=dsp["id"])
    op(admin, "POST", "/admin/disputes/{id}/resolve", 200, {"accepted": True, "refund_xaf": 100, "points_penalty": 1, "resolution_note": "Audit ok"}, id=dsp["id"])
    op(admin, "POST", "/admin/disputes/{id}/resolve", 409, {"accepted": True, "resolution_note": "Audit ok"}, id=dsp["id"])
    op(admin, "GET", "/admin/rides", 200)
    # annulation
    s, r2 = op(p2, "POST", "/rides", 201, RIDE)
    op(p1, "POST", "/rides/{id}/cancel", 403, {"reason": "x"}, id=r2["id"])
    op(p2, "POST", "/rides/{id}/cancel", 200, {"reason": "Change"}, id=r2["id"])
    op(p2, "POST", "/rides/{id}/cancel", 409, id=r2["id"])
    # GPS et Pause conservees
    n = len(http("GET", f"{P}/admin/disputes/{dsp['id']}/evidence", token=admin.token)[1]["gps_track"])
    finding("OK?", "gps_conserve", n >= 2, n)


def s_taxi():
    p3, d6 = ctx["p3"], ctx["d6"]
    taxi = {**RIDE, "mode": "taxi", "service_class": None, "taxi_zone_id": ctx["zone"]}
    op(p3, "POST", "/rides", 409, taxi)   # 400 XAF < minimum 500
    s, r = op(p3, "POST", "/rides", 201, {**taxi, "payment_method": "cash"})
    rid = r["id"]
    op(d6, "POST", "/rides/{id}/accept", 200, id=rid)
    op(d6, "POST", "/rides/{id}/start", 200, id=rid)
    op(d6, "POST", "/rides/{id}/complete", 409, id=rid)
    op(d6, "POST", "/rides/{id}/complete", 200, {"cash_received": True}, id=rid)
    op(ctx["p1"], "POST", "/rides", 422, {**taxi, "taxi_zone_id": None})


def s_access_matrix():
    """Toutes les routes : sans jeton = 401 ; admin avec jeton passager = 403."""
    spec = http("GET", "/openapi.json")[1]
    ctx["spec"] = spec
    public = {("post", P + "/auth/signup"), ("post", P + "/auth/login"), ("post", P + "/auth/refresh"), ("get", "/"), ("get", "/healthz")}
    bad_anon, bad_role, n_anon, n_admin = [], [], 0, 0
    for path, ops in spec["paths"].items():
        url = re.sub(r"\{(\w+)\}", "1", path)
        for m in ops:
            if (m, path) in public:
                continue
            n_anon += 1
            s, _, _ = http(m.upper(), url, {})
            if s != 401:
                bad_anon.append(f"{m.upper()} {path}->{s}")
            if path.startswith(P + "/admin"):
                n_admin += 1
                for who in ("p1", "d1"):
                    s, _, _ = http(m.upper(), url, {}, token=ctx[who].token)
                    if s != 403:
                        bad_role.append(f"{who} {m.upper()} {path}->{s}")
    finding("OK?", f"matrice_sans_jeton_401 ({n_anon} routes)", not bad_anon, bad_anon)
    finding("OK?", f"matrice_admin_refuse_non_admin ({n_admin} routes x2 roles)", not bad_role, bad_role)


def probe(sev, key, cond_fn, a, method, url, body=None, why=""):
    s, j, _ = http(method, P + url, body, a.token if a else None)
    finding(sev, key, cond_fn(s, j), f"{method} {url} -> {s} {why} {str(j)[:200]}")
    return s, j


def s_input_probes():
    p1, p2, admin, d1 = ctx["p1"], ctx["p2"], ctx["admin"], ctx["d1"]
    no500 = lambda s, j: s < 500
    probe("HAUT", "zones_lat_sans_lng", no500, p1, "GET", "/zones?lat=4.05")
    for u in ("/rides?limit=-1", "/wallet/transactions?limit=-1", "/users/me/notifications?limit=-1"):
        probe("MOYEN", f"limit_negatif {u}", no500, p1, "GET", u)
    probe("MOYEN", "limit_negatif /goals/history", no500, d1, "GET", "/goals/history?limit=-1")
    probe("MOYEN", "limit_negatif /drivers/me/points", no500, d1, "GET", "/drivers/me/points?limit=-1")
    probe("MOYEN", "offset_negatif /admin/users", no500, admin, "GET", "/admin/users?offset=-1")
    probe("MOYEN", "offset_negatif /admin/rides", no500, admin, "GET", "/admin/rides?offset=-1")
    probe("MOYEN", "id_trop_grand", no500, p1, "GET", "/rides/99999999999999999999")
    probe("MOYEN", "nul_byte_nom", no500, None, "POST", "/auth/signup",
          {"full_name": "ab\u0000c", "phone": new_phone(), "password": PW, "role": "passenger"})
    probe("MOYEN", "nul_byte_recherche", no500, admin, "GET", "/admin/users?q=%00")
    probe("OK?", "sql_injection_q", lambda s, j: s == 200 and j == [], admin, "GET", "/admin/users?q=%27%20OR%201%3D1--")
    probe("MOYEN", "course_flexible_sans_classe", lambda s, j: s in (409, 422), p1, "POST", "/rides/estimate",
          {k: v for k, v in RIDE.items() if k not in ("service_class", "payment_method")})
    s, j = probe("MOYEN", "commande_flexible_sans_classe", lambda s, j: s in (409, 422), p1, "POST", "/rides",
                 {**RIDE, "service_class": None})
    if s == 201:
        http("POST", f"{P}/rides/{j['id']}/cancel", {}, p1.token)
    s, j, _ = http("POST", f"{P}/auth/login", raw=b"{not json")
    finding("OK?", "json_invalide_pas_500", s == 422, s)
    probe("FAIBLE", "gps_coordonnees_hors_limites", lambda s, j: s == 422, ctx["d1"], "POST", "/rides/1/pause", {"lat": 999, "lng": 999})

    # message admin sur course sans chauffeur
    s, r = 0, None
    s, r, _ = http("POST", f"{P}/rides", RIDE, p2.token)
    if s == 201:
        probe("MOYEN", "admin_message_sans_chauffeur", no500, admin, "POST", f"/rides/{r['id']}/messages", {"text": "Bonjour"})
        http("POST", f"{P}/rides/{r['id']}/cancel", {}, p2.token)


def s_business_probes():
    admin = ctx["admin"]
    # chauffeur eco accepte une course prestige ; chauffeur hors ligne
    eco = new_driver("Eco Hors Ligne", admin)
    pa = signup("Passager Probe", "passenger")
    fund(pa, 100000)
    s, r, _ = http("POST", f"{P}/rides", {**RIDE, "service_class": "prestige"}, pa.token)
    s, _, _ = http("POST", f"{P}/rides/{r['id']}/accept", {}, eco.token)
    finding("MOYEN", "classe_respectee_a_acceptation", s == 409, f"chauffeur eco, course prestige -> accept {s}")
    http("POST", f"{P}/rides/{r['id']}/cancel", {}, pa.token)
    s, r, _ = http("POST", f"{P}/rides", RIDE, pa.token)
    http("POST", f"{P}/drivers/me/online", {"online": False}, eco.token)
    s, _, _ = http("POST", f"{P}/rides/{r['id']}/accept", {}, eco.token)
    finding("MOYEN", "hors_ligne_ne_peut_accepter", s == 409, f"chauffeur hors ligne -> accept {s}")
    http("POST", f"{P}/drivers/me/online", {"online": True}, eco.token)
    http("POST", f"{P}/rides/{r['id']}/accept", {}, eco.token)
    s, _, _ = http("DELETE", f"{P}/users/me", token=eco.token)
    finding("HAUT", "suppression_chauffeur_en_course_refusee", s == 409, f"DELETE /users/me pendant une course -> {s}")
    http("POST", f"{P}/rides/{r['id']}/cancel", {}, pa.token)

    # chauffeur supprime reste visible ?
    ghost = new_driver("Fantome", admin)
    http("POST", f"{P}/drivers/me/online", {"online": True}, ghost.token)
    http("POST", f"{P}/drivers/me/location", {"lat": 4.2, "lng": 9.9}, ghost.token)
    http("DELETE", f"{P}/users/me", token=ghost.token)
    s, near, _ = http("GET", f"{P}/drivers/nearby?lat=4.2&lng=9.9&mode=flexible&radius_km=1", token=pa.token)
    finding("MOYEN", "chauffeur_supprime_invisible", not any(x["id"] == ghost.driver_id for x in near), f"{len(near)} visibles")

    # paiement Mobile Money sans fonds ni operateur
    pb = signup("Passager Sans Fonds", "passenger")
    drv = new_driver("Driver Paiement", admin)
    s, r, _ = http("POST", f"{P}/rides", {**RIDE, "payment_method": "orange_money"}, pb.token)
    if s == 201:
        http("POST", f"{P}/rides/{r['id']}/accept", {}, drv.token)
        http("POST", f"{P}/rides/{r['id']}/start", None, drv.token)
        s, c, _ = http("POST", f"{P}/rides/{r['id']}/complete", {}, drv.token)
        _, w, _ = http("GET", f"{P}/wallet", token=pb.token)
        finding("CRITIQUE", "mobile_money_reel", w["balance_xaf"] >= 0,
                f"course orange_money, solde 0 : complete {s}, payment_status={c.get('payment_status')}, solde passager apres={w['balance_xaf']}")
    else:
        finding("INFO", "mobile_money_reel", True, f"creation refusee {s}")

    # recharge gratuite + premium gratuit + retrait
    pc = new_driver("Fraudeur", admin, role="copilote")
    s, t, _ = http("POST", f"{P}/wallet/topup", {"amount_xaf": 1000000, "channel": "mtn_momo"}, pc.token)
    s2, _, _ = http("POST", f"{P}/wallet/withdraw", {"amount_xaf": 900000, "channel": "mtn_momo"}, pc.token)
    finding("CRITIQUE", "argent_cree_sans_operateur", s != 201, f"topup 1 000 000 sans paiement -> {s}, puis retrait 900 000 -> {s2}")
    pd = new_driver("Premium Gratuit", admin, role="copilote")
    s, j, _ = http("POST", f"{P}/drivers/me/premium", {"channel": "orange_money", "months": 12}, pd.token)
    finding("CRITIQUE", "premium_sans_paiement", s != 200, f"premium 12 mois orange_money sans operateur -> {s}")

    # idempotence
    pe = signup("Double Clic", "passenger")
    body = {"amount_xaf": 1000, "channel": "orange_money"}
    hdr = {"Idempotency-Key": "audit-1"}
    http("POST", f"{P}/wallet/topup", body, pe.token, headers=hdr)
    http("POST", f"{P}/wallet/topup", body, pe.token, headers=hdr)
    _, w, _ = http("GET", f"{P}/wallet", token=pe.token)
    finding("HAUT", "idempotence_paiement", w["balance_xaf"] == 1000, f"meme demande envoyee 2 fois -> solde {w['balance_xaf']}")


def conc(n, fn):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, range(n)))


def s_concurrency():
    admin = ctx["admin"]
    # meme telephone x20
    ph = new_phone()
    st = conc(20, lambda i: http("POST", f"{P}/auth/signup", {"full_name": "Course", "phone": ph, "password": PW, "role": "passenger"})[0])
    finding("MOYEN", "signup_concurrent_sans_500", 500 not in st and st.count(201) == 1, {x: st.count(x) for x in set(st)})
    # meme passager 2 courses
    pa = signup("Concurrent", "passenger")
    fund(pa, 100000)
    res = conc(10, lambda i: http("POST", f"{P}/rides", RIDE, pa.token))
    st = [r[0] for r in res]
    finding("HAUT", "une_seule_course_active", st.count(201) == 1 and 500 not in st, {x: st.count(x) for x in set(st)})
    for r in res:
        if r[0] == 201:
            http("POST", f"{P}/rides/{r[1]['id']}/cancel", {}, pa.token)
    # 8 chauffeurs, une course
    ds = [new_driver(f"Conc {i}", admin) for i in range(8)]
    s, r, _ = http("POST", f"{P}/rides", RIDE, pa.token)
    st = conc(8, lambda i: http("POST", f"{P}/rides/{r['id']}/accept", {}, ds[i].token)[0])
    finding("OK?", "un_seul_chauffeur_accepte", st.count(200) == 1 and 500 not in st, {x: st.count(x) for x in set(st)})
    winner = ds[st.index(200)]
    http("POST", f"{P}/rides/{r['id']}/start", None, winner.token)
    st = conc(6, lambda i: http("POST", f"{P}/rides/{r['id']}/complete", {}, winner.token)[0])
    n = str(sum(1 for t in http("GET", f"{P}/wallet/transactions?kind=ride_payment", token=pa.token)[1] if t["ride_id"] == r["id"]))
    finding("OK?", "fin_de_course_concurrente_un_seul_debit", st.count(200) == 1 and n == "1", {"statuts": st, "debits": n})
    # meme chauffeur, 2 courses
    d = ds[(st and 0)] if ds[0] is not winner else ds[1]
    pb, pc = signup("Conc B", "passenger"), signup("Conc C", "passenger")
    for x in (pb, pc):
        fund(x, 50000)
    _, r1, _ = http("POST", f"{P}/rides", RIDE, pb.token)
    _, r2, _ = http("POST", f"{P}/rides", RIDE, pc.token)
    rids = [r1["id"], r2["id"]] * 4
    st = conc(8, lambda i: http("POST", f"{P}/rides/{rids[i]}/accept", {}, d.token)[0])
    a = sum(len(http("GET", f"{P}/rides?status={x}", token=d.token)[1]) for x in ("accepted", "in_progress"))
    finding("HAUT", "chauffeur_une_course_a_la_fois", a == 1, {"courses_actives": a, "statuts": sorted(st)})
    # retraits simultanes
    w = new_driver("Retrait", admin)
    fund(w, 10000)
    st = conc(10, lambda i: http("POST", f"{P}/wallet/withdraw", {"amount_xaf": 10000, "channel": "mtn_momo"}, w.token)[0])
    _, b, _ = http("GET", f"{P}/wallet", token=w.token)
    finding("OK?", "retraits_fermes_sans_operateur", st.count(503) == 10 and b["balance_xaf"] == 10000, {"statuts": sorted(st), "solde": b["balance_xaf"]})
    # objectif concurrent
    g = new_driver("Goal Conc", admin)
    st = conc(8, lambda i: http("POST", f"{P}/goals", {"target_rides": 30}, g.token)[0])
    finding("MOYEN", "objectif_concurrent_sans_500", 500 not in st and st.count(201) == 1, {x: st.count(x) for x in set(st)})


def s_security():
    p1 = ctx["p1"]
    # force brute
    t = signup("Cible Brute", "passenger")
    st = [http("POST", f"{P}/auth/login", {"phone": t.phone, "password": f"faux{i}"})[0] for i in range(30)]
    finding("HAUT", "limite_essais_connexion", 429 in st, {x: st.count(x) for x in set(st)})
    s, j, _ = http("POST", f"{P}/auth/login", {"phone": t.phone, "password": PW})
    finding("INFO", "compte_ok_apres_30_essais", s == 200, s)
    # enumeration
    s, j, _ = http("POST", f"{P}/auth/signup", {"full_name": "Enum", "phone": t.phone, "password": PW, "role": "passenger"})
    finding("MOYEN", "inscription_ne_revele_pas_compte", s != 409, f"{s} {str(j)[:120]}")
    def timed(phone):
        t0 = time.perf_counter(); http("POST", f"{P}/auth/login", {"phone": phone, "password": "faux12345"}); return time.perf_counter() - t0
    ex = sorted(timed(t.phone) for _ in range(7))[3]
    no = sorted(timed("+237000000000") for _ in range(7))[3]
    finding("FAIBLE", "temps_reponse_constant", abs(ex - no) < 0.05, f"compte existant {ex*1000:.0f} ms, inconnu {no*1000:.0f} ms")
    # CORS
    s, _, h = http("GET", f"{P}/auth/me", token=p1.token, headers={"Origin": "https://evil.example"})
    h = {k.lower(): v for k, v in h.items()}
    finding("MOYEN", "cors_liste_exacte", h.get("access-control-allow-origin") not in ("*", "https://evil.example"),
            {"acao": h.get("access-control-allow-origin"), "credentials": h.get("access-control-allow-credentials")})
    finding("FAIBLE", "entetes_securite", all(x in h for x in ("x-content-type-options", "x-frame-options")), sorted(h))
    finding("FAIBLE", "docs_masquees_hors_dev", http("GET", "/docs")[0] == 404, f"/docs -> {http('GET','/docs')[0]}")
    # admin suspendu sur lui meme / auto suspension
    # jeton encore valide apres changement de mot de passe : pas de route de changement
    spec = ctx["spec"]
    has = any("password" in p or "reset" in p or "logout" in p or "otp" in p for p in spec["paths"])
    finding("HAUT", "routes_mdp_oublie_logout_otp", has, [p for p in spec["paths"] if "auth" in p])


def report():
    spec = ctx["spec"]
    all_ops = {(m.upper(), p) for p, o in spec["paths"].items() for m in o}
    ok2xx = {(r["method"], r["route"]) for r in records if r["ok"] and 200 <= r["status"] < 300}
    tested = {(r["method"], r["route"]) for r in records}
    fails = [r for r in records if not r["ok"]]
    print(f"OPERATIONS OPENAPI: {len(all_ops)} | testees: {len(tested & all_ops)} | succes 2xx obtenu: {len(ok2xx & all_ops)}")
    print(f"APPELS FONCTIONNELS: {len(records)} | conformes: {len(records) - len(fails)} | ecarts: {len(fails)}")
    print("SANS 2xx:", sorted(all_ops - ok2xx))
    print("--- ECARTS")
    for r in fails:
        print(r["who"], r["method"], r["route"], "attendu", r["expected"], "obtenu", r["status"], r.get("body", ""))
    print("--- SONDES")
    for f in sorted(findings, key=lambda x: x["ok"]):
        print("PASS" if f["ok"] else "FAIL", f["sev"], f["key"], "::", f["detail"])
    json.dump({"records": records, "findings": findings}, open(RESULTS, "w"), indent=1)


RESULTS = os.path.join(tempfile.gettempdir(), "carlinq_audit_results.json")


def setup():
    pw = getpass.getpass(f"Mot de passe admin ({ADMIN_PHONE}) : ")
    s, j, _ = http("POST", f"{P}/auth/login", {"phone": ADMIN_PHONE, "password": pw})
    if s != 200:
        sys.exit(f"Connexion admin refusee ({s})")
    admin = A("admin", ADMIN_PHONE, j["access_token"], j["refresh_token"], j["user"])
    ctx["admin"] = admin
    for i, money in ((1, 500000), (2, 100000), (3, 0)):
        p = signup(f"Audit Passager {i}", "passenger")
        if money:
            fund(p, money)
        ctx[f"p{i}"] = p
    for i, role, mode, cls in ((1, "drivers", "flexible", "eco"), (2, "drivers", "flexible", "eco"),
                               (3, "drivers", "flexible", "eco"), (4, "copilote", "flexible", "prestige"),
                               (5, "copilote", "taxi", None), (6, "drivers", "taxi", None)):
        d = new_driver(f"Audit Chauffeur {i}", admin, role, mode, cls)
        fund(d, 15000)
        ctx[f"d{i}"] = d


def one_ride(p, d):
    s, r, _ = http("POST", f"{P}/rides", RIDE, p.token)
    assert s == 201, r
    for action, body in (("accept", {}), ("start", None), ("complete", {})):
        s, j, _ = http("POST", f"{P}/rides/{r['id']}/{action}", body, d.token)
        assert s == 200, (action, j)


def s_goals():
    admin, p1, d1, d2, d3, d4, d6, nd = (ctx[k] for k in ("admin", "p1", "d1", "d2", "d3", "d4", "d6", "nd"))
    orig = http("GET", f"{P}/admin/settings/goals", token=admin.token)[1]
    try:
        http("PUT", f"{P}/admin/settings/goals", {"tiers": [{"target_rides": 1, "bonus_xaf": 1000}, {"target_rides": 3, "bonus_xaf": 5000}]}, admin.token)
        op(d1, "GET", "/goals/config", 200)
        s, g = op(d1, "POST", "/goals", 201, {"target_rides": 3})
        gid = g["id"]
        op(d1, "POST", "/goals", 409, {"target_rides": 3})
        op(d1, "GET", "/goals/current", 200)
        op(d1, "GET", "/goals/history", 200)
        op(d1, "GET", "/goals/{id}", 200, id=gid)
        op(d4, "GET", "/goals/{id}", 403, id=gid)
        for h in (d2, d3):
            http("POST", f"{P}/goals", {"target_rides": 1}, h.token)
            one_ride(p1, h)
        op(d1, "GET", "/goals/{id}/helpers", 200, id=gid)
        op(d2, "GET", "/goals/{id}/helpers", 403, id=gid)
        op(d1, "POST", "/goals/{id}/shares", 422, {"helper_driver_id": d3.driver_id, "percent": 40}, id=gid)
        op(d1, "POST", "/goals/{id}/shares", 409, {"helper_driver_id": d4.driver_id, "percent": 10}, id=gid)
        s, sh = op(d1, "POST", "/goals/{id}/shares", 201, {"helper_driver_id": d3.driver_id, "percent": 10, "message": "Aide"}, id=gid)
        op(d2, "POST", "/goals/shares/{id}/accept", 403, id=sh["id"])
        op(d3, "GET", "/goals/shares/incoming", 200)
        op(d3, "POST", "/goals/shares/{id}/decline", 200, id=sh["id"])
        s, sh2 = op(d1, "POST", "/goals/{id}/shares", 201, {"helper_driver_id": d3.driver_id, "percent": 10}, id=gid)
        op(d3, "POST", "/goals/shares/{id}/accept", 200, id=sh2["id"])
        op(d3, "POST", "/goals/shares/{id}/cancel", 403, id=sh2["id"])
        op(d1, "POST", "/goals/shares/{id}/cancel", 200, id=sh2["id"])
        s, sh3 = op(d1, "POST", "/goals/{id}/shares", 201, {"helper_driver_id": d2.driver_id, "percent": 20}, id=gid)
        op(d2, "POST", "/goals/shares/{id}/accept", 200, id=sh3["id"])
        one_ride(p1, d2)
        s, g2 = op(d1, "GET", "/goals/{id}", 200, id=gid)
        c = next(x for x in g2["shares"] if x["helper_driver_id"] == d2.driver_id)["contributed_rides"]
        finding("OK?", "aidant_credit_course", c == 1, c)
        op(d1, "POST", "/goals/{id}/pause", 200, {"hours": 1}, id=gid)
        op(d1, "POST", "/goals/{id}/pause", 409, {"hours": 1}, id=gid)
        op(d6, "POST", "/goals/{id}/pause", 403, {"hours": 1}, id=gid)
        op(nd, "POST", "/goals", 422, {"target_rides": 0})
        op(nd, "POST", "/goals", 201, {"target_rides": 1})
        op(admin, "GET", "/admin/goals", 200)
        op(admin, "GET", "/admin/goals/shares", 200)
        monday = date.fromisoformat(g["week_start"])
        op(admin, "POST", "/admin/goals/close-week", 409, {"week_start": monday.isoformat()})
        op(admin, "POST", "/admin/goals/close-week", 422, {"week_start": (monday + timedelta(days=1)).isoformat()})
        op(admin, "POST", "/admin/goals/close-week", 200, {"week_start": "2020-01-06"})   # semaine vide
        # Versement reel impossible sans changer la date : semaine en cours = 409 attendu.
        goal_d2 = http("GET", f"{P}/goals/current", token=d2.token)[1]["id"]
        op(admin, "POST", "/admin/goals/{id}/settle", 409, id=goal_d2)
        op(d2, "GET", "/goals/{id}/settlement", 404, id=goal_d2)
        op(d1, "GET", "/goals/{id}/settlement", 403, id=goal_d2)
        op(admin, "GET", "/admin/goals/settlements", 200)
        op(admin, "POST", "/admin/notifications", 201, {"title": "Audit technique", "body": "Message de test", "role": "drivers", "mode": "flexible"})
        op(admin, "GET", "/admin/audit-logs", 200)
        op(admin, "GET", "/admin/dashboard", 200)
    finally:
        s, _, _ = http("PUT", f"{P}/admin/settings/goals", {"tiers": orig["tiers"]}, admin.token)
        finding("OK?", "paliers_restaures", s == 200, s)


def cleanup():
    for a in created:
        try:
            if http("GET", f"{P}/auth/me", token=a.token)[0] != 200:
                continue
            for st in ("pending", "accepted", "in_progress"):
                for r in http("GET", f"{P}/rides?status={st}", token=a.token)[1]:
                    http("POST", f"{P}/rides/{r['id']}/cancel", {"reason": "Fin audit"}, a.token)
            if a.user["role"] != "passenger":
                http("POST", f"{P}/drivers/me/online", {"online": False}, a.token)
            http("DELETE", f"{P}/users/me", token=a.token)
        except Exception as exc:
            print("Nettoyage incomplet pour", a.name, exc)


try:
    setup()
    for fn in (s_root, s_auth_users, s_driver_admin, s_settings_zones, s_wallet, s_ride_full, s_taxi, s_goals,
               s_access_matrix, s_input_probes, s_business_probes, s_concurrency, s_security):
        section(fn)
    report()
    print("Note : settle et settlement ne peuvent pas obtenir 2xx sans changer la date en base.")
    print("Resultats :", RESULTS)
finally:
    cleanup()
