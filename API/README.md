# Carlinq - API FastAPI

API REST de la plateforme Carlinq (Carlinq Flexible + Carlinq Taxi) : **FastAPI + PostgreSQL + Redis**,
consommée par l'app Passager, l'app Chauffeur (`../Application`) et le Backoffice (`../Backoffice`).

## Démarrer (Docker, recommandé)

```bash
docker compose up --build -d
```

| Service | Hôte | Rôle |
|---|---|---|
| API | http://localhost:8010 (Swagger : `/docs`) | FastAPI / uvicorn |
| PostgreSQL 16 | `localhost:55432` (carlinq / carlinq) | Base `carlinq` (+ `carlinq_test` pour les tests) |
| Redis 7 | `localhost:56379` | Cache (règles métier, zones, routes dégradées) |

Ports décalés pour cohabiter avec d'autres projets locaux. Au démarrage, le conteneur applique les
migrations Alembic puis charge les **données de démo** (`SEED_DEMO_DATA=true`, idempotent).

Comptes de démo (mot de passe de développement dans `app/seed.py`, `SEED_DEMO_PASSWORD` ailleurs) :

| Rôle | Téléphone |
|---|---|
| Admin | `+237600000000` |
| Passagers | `+237690000001` à `+237690000003` |
| Chauffeurs (Drivers / Copilote) | `+237670000001` à `+237670000006` (Kevin Kamga = `…001`, objectif partagé) |

`GET /healthz` → `{"status":"ok","database":"ok","cache":"ok"}`.

## Tests

```bash
docker compose run --rm api pytest        # 23 tests, base carlinq_test + Redis base 1
```

Les tests créent le schéma **par les migrations Alembic** (la migration est donc testée), vident les tables et
le cache entre chaque test.

## Architecture

```
app/
├── main.py                 # FastAPI, CORS, gestion des erreurs metier, /healthz
├── core/                   # config (.env), securite (JWT + bcrypt), erreurs, horloge (Douala), cache Redis
├── db/session.py           # SQLAlchemy 2.0
├── models/                 # user, driver, goal, ride, misc (wallet, zones, litiges, config, audit...)
├── schemas/                # Pydantic v2 (entrees / sorties)
├── services/               # metier : settings, pricing, rides, goals, wallet, points, audit, notify
├── api/v1/routes/          # auth, users, drivers, goals, rides, wallet, zones, disputes, admin
└── seed.py                 # donnees de demo
alembic/versions/           # migrations
tests/                      # pytest (integration PostgreSQL)
```

Erreurs : `{"error": {"code": "GOAL_SHARE_TOTAL_EXCEEDED", "message": "...", "details": {...}}}` avec des codes
stables (401, 403 `INSUFFICIENT_ROLE`, 404, 409 règle métier, 422 validation).

## Routes (`/api/v1`, 101 opérations)

| Domaine | Principales routes |
|---|---|
| Auth | `POST /auth/signup` · `POST /auth/login` · `POST /auth/refresh` · `GET /auth/me` |
| Utilisateur | `PATCH /users/me` (domicile Retour maison) · `DELETE /users/me` · `GET /users/me/notifications` |
| Chauffeurs | `POST /drivers/me` (profil, validation admin) · `GET /drivers/me/dashboard` · `POST /drivers/me/online` · `/location` · `/refusal-window` · `/points` · `/documents` · `PUT/DELETE /drivers/me/routes/{slot}` · `POST /drivers/me/premium` · `/road-reports` · `GET /drivers/nearby` |
| Courses | `POST /rides/estimate` · `POST /rides` · `GET /rides/offers` · `/{id}/accept` · `/refuse` · `/start` · `/stops/{id}/pass` · `/pause` · `/pause/end` · `/traffic` · `/traffic/end` · `/gps` · `/complete` · `/cancel` · `/rate` · `/messages` |
| Objectifs & partage | `GET /goals/config` · `POST /goals` · `GET /goals/current` · `/history` · `/{id}` · `/{id}/pause` · `/{id}/helpers` · `POST /goals/{id}/shares` · `GET /goals/shares/incoming` · `/shares/{id}/accept` · `/decline` · `/cancel` · `GET /goals/{id}/settlement` |
| Portefeuille | `GET /wallet` · `/transactions` · `POST /wallet/topup` · `POST /wallet/withdraw` |
| Zones / routes | `GET /zones` · `/zones/nearest` · `GET /roads/degraded` |
| Litiges | `POST /disputes` · `GET /disputes/mine` |
| Admin | dashboard, chauffeurs (statut, classe, points), documents, utilisateurs (suspension, portefeuille), courses, litiges (preuves GPS, arbitrage), `settings/pricing` et `settings/goals`, **objectifs (`/admin/goals`, `/goals/shares`, `/goals/close-week`, `/goals/{id}/settle`, `/goals/settlements`)**, zones, routes dégradées, signalements, notifications, journal d'audit |

## Partage d'objectif entre chauffeurs

1. Chaque chauffeur fixe son objectif hebdomadaire (`POST /goals`). Le bonus dépend du palier (config `goals.tiers`).
2. Un chauffeur qui **n'atteint pas** son objectif (le propriétaire) invite un chauffeur ayant **atteint le sien la même semaine**
   (l'aidant) avec un pourcentage du bonus : `POST /goals/{id}/shares {helper_driver_id, percent}`.
3. L'aidant accepte (`/goals/shares/{id}/accept`) : ses courses suivantes sont créditées sur l'objectif partagé
   (dans l'ordre d'acceptation s'il aide plusieurs chauffeurs). Chaque course est tracée dans `goal_ride_credits`.
4. Versement (`POST /admin/goals/close-week`, après la fin de semaine) : si l'objectif partagé est atteint,
   chaque aidant ayant apporté au moins une course reçoit `bonus × pourcentage` (arrondi à l'entier inférieur),
   **déduit** du bonus du propriétaire qui reçoit le reste. Chaque part est une écriture du portefeuille
   (`goal_bonus` / `goal_share`), détaillée dans `goal_settlements` + `goal_settlement_lines`, et journalisée.
5. Objectif non atteint après la fin de semaine + jours de reprise → `failed` : personne n'est payé.

Règles par défaut (modifiables par `PUT /admin/settings/goals`, journalisé) : 3 aidants max, 5 à 30 % par aidant,
50 % du bonus cédé au maximum, versement unique par objectif (idempotent), une invitation ne peut plus être annulée
une fois que l'aidant a apporté des courses.

## Cache Redis

Mis en cache : `settings:pricing`, `settings:goals` (lus à chaque course), `zones:active`, `roads:validated`.
Invalidation au COMMIT de chaque écriture admin. **Jamais source de vérité** : si Redis tombe, l'API lit la
base et retente Redis après 30 s (`/healthz` indique `"cache": "down"`).

## Sans Docker

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env            # base et Redis exposes par docker compose (55432, 56379)
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload --port 8010
```
