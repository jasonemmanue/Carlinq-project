# Déploiement Carlinq sur Railway (CLI)

Environnement de test hébergé : API FastAPI + PostgreSQL + Redis + Backoffice Next.js.

```
Railway (projet Carlinq, environnement production)
├── Postgres     base gérée Railway          ← DATABASE_URL = ${{Postgres.DATABASE_URL}}
├── Redis        cache (jamais source de vérité) ← REDIS_URL = ${{Redis.REDIS_URL}}
├── api          API/        Dockerfile, port 8000, santé /healthz, migrations Alembic au démarrage
└── backoffice   Backoffice/ Dockerfile (Next standalone), port 3000, santé /api/health, HTTP Basic
                 └── appelle l'API côté serveur : CARLINQ_API_URL = https://${{api.RAILWAY_PUBLIC_DOMAIN}}
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `API/railway.json` | Build Dockerfile, healthcheck `/healthz`, 1 réplique (migrations au boot). |
| `API/Dockerfile` | Écoute `$PORT` ; `INSTALL_DEV=false` sur Railway (pas de pytest en prod). |
| `Backoffice/railway.json`, `Backoffice/Dockerfile` | Image Next.js `standalone`. |
| `Backoffice/src/middleware.ts` | Protection HTTP Basic (le backoffice n'a pas encore de session admin). |
| `deploy/railway-setup.ps1` | Création des services, variables, domaines, puis déploiement. Relançable. |
| `deploy/railway-deploy.ps1` | Redéploiement du code local (`api`, `backoffice` ou les deux). |

## Première mise en place

```powershell
railway login
# Créer le projet sur railway.com (ou : railway init --name Carlinq), puis depuis la racine du monorepo :
railway link
powershell -ExecutionPolicy Bypass -File deploy\railway-setup.ps1
```

Options : `-NoSeed` (pas de comptes de démo), `-NoDeploy` (configurer sans déployer), `-Environment <nom>`.

Le script ne régénère pas `SECRET_KEY` ni le mot de passe du backoffice s'ils existent déjà.

## Redéployer après une modification

```powershell
powershell -ExecutionPolicy Bypass -File deploy\railway-deploy.ps1              # les deux
powershell -ExecutionPolicy Bypass -File deploy\railway-deploy.ps1 -Service api
```

Alternative : brancher GitHub (`railway service source connect --repo jasonemmanue/Carlinq-project --branch main --service api`)
et définir le *Root Directory* du service (`API` / `Backoffice`) dans le tableau de bord.

## Après le déploiement

- API : `https://<domaine api>/docs` et `/healthz` (`database: ok`, `cache: true`).
- Backoffice : utilisateur `admin`, mot de passe : `railway variable list --service backoffice --kv`.
- Apps Flutter : `flutter run --dart-define=CARLINQ_API_URL=https://<domaine api>`, ou l'écran *Serveur* de la page de connexion.
- Logs : `railway logs --service api` ; console SQL : `railway connect Postgres`.

## Points d'attention

- Fichiers envoyés (documents chauffeur, photos de messagerie) : volume Railway du service `api`, monté sur `/data`
  (`railway service link api`, puis `railway volume add --mount-path /data`). Sans volume, l'envoi renvoie 503. Avec un volume, garder
  1 seule réplique ; ne jamais supprimer ce volume.
- `SEED_DEMO_DATA=true` par défaut : comptes de démo (mot de passe commun connu, voir `API/app/seed.py`).
  Environnement de **test uniquement** ; passer à `false` et changer le mot de passe admin avant toute ouverture réelle.
- Les tests pytest ne tournent pas sur Railway (base `carlinq_test` inexistante) : rester sur Docker Compose en local.
- Les données Postgres (dont les Pauses Arrêt / traces GPS, conservées indéfiniment) vivent dans le volume du service
  Postgres : ne jamais supprimer ce service ni son volume.
