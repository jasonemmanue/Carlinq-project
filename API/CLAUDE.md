# CLAUDE.md - API Carlinq (FastAPI)

Contexte pour Claude Code dans `API/`.

## Rôle

Backend REST des deux apps mobiles (`/Application/passager`, `/Application/chauffeur`) et du backoffice
Next.js (`/Backoffice`). Voir `README.md` pour les routes, les comptes de démo et le partage d'objectif.

## Stack

- Python 3.11, FastAPI 0.115, Pydantic v2, SQLAlchemy 2.0 (sync), Alembic.
- PostgreSQL 16 (pas de PostGIS pour l'instant : distances Haversine en Python).
- Redis 7 : cache des règles métier, zones et routes dégradées (`app/core/cache.py`).
- JWT (python-jose) + bcrypt.
- Docker Compose : `docker compose up --build -d` → API sur **8010**, Postgres **55432**, Redis **56379**.

## Organisation

- `app/models/` : un module par domaine ; enums PostgreSQL via `pg_enum` (valeurs, pas noms).
- `app/services/` : toute la logique métier ; les routes ne font que valider, appeler un service et `commit`.
- `app/services/settings.py` : règles métier **en base** (`app_settings` : `pricing`, `goals`), validées par Pydantic.
- `app/services/goals.py` : objectifs, partage entre chauffeurs, crédit des courses, versement réparti.
- Tests : `docker compose run --rm api pytest` (base `carlinq_test`, Redis base 1).

## Règles à ne pas violer

- Commission 8 % par défaut, jamais > 10 % (validé par `PricingConfig`).
- Montants toujours en **entiers XAF** ; tout mouvement d'argent passe par `services/wallet.post` (écriture
  `wallet_txs` avec solde après, verrou `FOR UPDATE`).
- Toute transition de course est journalisée (`ride_status_logs`) ; toute mutation admin dans `audit_logs`
  (même transaction).
- Les Pauses Arrêt et la trace GPS (`ride_pauses`, `ride_gps_points`) ne sont **jamais** supprimées.
- Jamais de validation automatique d'un chauffeur : `validation_status` passe par `POST /admin/drivers/{id}/status`.
- Partage d'objectif : versement unique par objectif (`goal_settlements.goal_id` unique), parts arrondies à
  l'entier inférieur, reste au propriétaire ; aidant sans course apportée = rien.
- Le cache Redis n'est jamais source de vérité : toute écriture sur une donnée cachée doit appeler
  `cache.invalidate_on_commit(db, <cle>)`.

- Mobile Money : `PAYMENTS_SIMULATED=false` par défaut (recharge, retrait, Premium et course Mobile Money renvoient 503). `true` seulement en local et en test.
- Connexion : 5 échecs par numéro = 2 min de blocage (`app/core/ratelimit.py`).
- Les erreurs 422 ne renvoient jamais la valeur saisie.

- Fichiers envoyés : `services/storage.py`, sur le volume Railway (`RAILWAY_VOLUME_MOUNT_PATH`) ; type vérifié sur les octets, lecture contrôlée par `GET /files/...`.

## Pièges

- Modifier un modèle → `alembic revision --autogenerate` (dans le conteneur avec le dossier `alembic/versions`
  monté), relire la migration (types ENUM partagés), tester `downgrade base` puis `upgrade head`.
- Les tests utilisent `TEST_DATABASE_URL` / `TEST_REDIS_URL` : ne jamais les pointer sur la base de démo.
- Le port 8000 de la machine est utilisé par un autre projet : garder 8010.

## Prochaines étapes

- WebSockets (suivi temps réel au lieu du polling des apps), Firebase Cloud Messaging.
- Google Routes API (anti-embouteillage), Google Places (géocodage des adresses saisies).
- Paiement Mobile Money réel (CinetPay) : aujourd'hui recharge et retrait sont enregistrés sans opérateur.
- Tâche planifiée : clôture automatique des semaines d'objectifs (aujourd'hui `POST /admin/goals/close-week`).
