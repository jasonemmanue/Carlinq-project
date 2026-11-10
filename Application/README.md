# Carlinq - Applications mobiles Flutter

Deux applications **distinctes**, publiées séparément sur les stores (cahier des charges v1.2 §5.1 / §5.2),
qui partagent un package de code commun :

```
Application/
├── passager/        # App « Carlinq » - passagers (com.carlinq.passager)
├── chauffeur/       # App « Carlinq Chauffeur » - Drivers + Copilote (com.carlinq.chauffeur)
├── carlinq_core/    # Package partagé : thème, modèles, carte, arrêts, chat, notifications
└── docs/            # MAQUETTES.md + captures des deux apps
```

| App | Public | Inscription | Navigation |
|---|---|---|---|
| **Carlinq** ([passager/](passager/README.md)) | Passagers | 2 étapes, activation immédiate | Accueil · Historique · Portefeuille · Profil |
| **Carlinq Chauffeur** ([chauffeur/](chauffeur/README.md)) | **Drivers** (affiliés, commission 8 %) et **Copilote** (indépendants / sociétés, Pack Premium 5 000 XAF/mois) | 3 étapes + **validation manuelle par un administrateur** | Tableau de bord · Itinéraires · Zones · Revenus · Profil |

Maquettes, captures et correspondance avec le cahier des charges : **[docs/MAQUETTES.md](docs/MAQUETTES.md)**.

## Lancer

```bash
cd passager      # ou cd chauffeur
flutter pub get
flutter run -d <device> --dart-define=CARLINQ_API_URL=http://10.0.2.2:8010
```

## Connexion à l'API

Les deux apps appellent l'API FastAPI (`../API`, `docker compose up -d`) via `ApiClient` (`carlinq_core`,
`dart:io`, rafraîchissement automatique du jeton). Deux modes :

| Mode | Entrée | Données |
|---|---|---|
| **Connecté** | Connexion / inscription | API réelle : comptes, devis serveur, courses, suivi (polling 3 s), portefeuille, objectifs et partage, offres chauffeur (polling 5 s) |
| **Démo** | « Explorer en mode démo » | Données embarquées, sans réseau (présentations) |

Adresse du serveur :
- au build : `--dart-define=CARLINQ_API_URL=http://<IP-du-PC>:8010` (défaut : `http://10.0.2.2:8010`, l'émulateur Android) ;
- dans l'app : écran de connexion → icône **Serveur API** (test de connexion puis enregistrement).

Téléphone réel : même Wi-Fi que le PC qui fait tourner Docker, et port 8010 autorisé dans le pare-feu Windows
(trafic HTTP en clair autorisé via `usesCleartextTraffic` : à remplacer par HTTPS en production).

Comptes de démo (mot de passe `<voir API/app/seed.py>`) : passager `+237690000001`, chauffeur `+237670000001`
(Kevin Kamga, objectif partagé avec Prisca), voir `../API/README.md`.

Les adresses saisies sont converties en coordonnées par un petit référentiel des quartiers de Douala
(`carlinq_core/lib/src/api/places.dart`) en attendant Google Places.

## Tests

À lancer dans chaque app :

```bash
flutter analyze   # 0 erreur / 0 warning (infos de style tolérées)
flutter test      # démarrage de l'app + smoke test des écrans à 360 dp
```

Les tests chargent les vraies polices Roboto / MaterialIcons du SDK Flutter (`carlinq_core/lib/testing.dart`) :
un débordement détecté correspond à un vrai téléphone de 360 dp.

Tests de bout en bout contre l'API réelle (Docker démarré) :

```bash
flutter test test/api_e2e_test.dart --dart-define=API_E2E=true --dart-define=CARLINQ_API_URL=http://localhost:8010
```

- chauffeur : inscription → profil véhicule → validation admin → en ligne → offre → course complète → objectif → invitation d'un aidant ;
- passager : inscription → recharge → devis → commande → course menée par un chauffeur de démo → paiement → notation.

## Build des APK

```bash
cd passager      # puis cd ../chauffeur
flutter build apk --release --dart-define=CARLINQ_API_URL=http://192.168.1.33:8010
flutter build apk --release --split-per-abi --dart-define=CARLINQ_API_URL=http://192.168.1.33:8010
```

Sorties dans `<app>/build/app/outputs/flutter-apk/` :

| Fichier | Appareils |
|---|---|
| `app-release.apk` | Tous (universel) |
| `app-arm64-v8a-release.apk` | Téléphones Android récents (64 bits) - recommandé |
| `app-armeabi-v7a-release.apk` | Téléphones anciens / entrée de gamme (32 bits) |
| `app-x86_64-release.apk` | Émulateurs |

Les APK sont signés avec la clé de debug (démo) : à remplacer par une clé de release avant publication sur le Play Store.

## Icônes

Même logo (`assets/images/logo.png`, copie de `LoGoCarlinq.png`) sur fond différent pour distinguer
les deux apps sur le téléphone : bleu royal `#0D47A1` (Passager), ardoise `#263238` (Chauffeur).

```bash
dart run flutter_launcher_icons   # dans l'app concernée
```

## Prochaines étapes (Phase MVP)

- Intégration Google Maps SDK (remplacement de `FakeMap` dans `carlinq_core`)
- Backend FastAPI (dossier `API/`), temps réel (WebSockets / Firebase)
- Paiement Orange Money / MTN Mobile Money (Phase 2)
