# CLAUDE.md - Applications mobiles Carlinq (Flutter)

Ce fichier oriente Claude Code lorsqu'il travaille dans le sous-dossier `Application/`.

## Contexte du projet

Carlinq est une plateforme mobile de transport bi-mode pour le marché africain (Cameroun d'abord). Elle combine :

- **Carlinq Flexible** : chauffeur entre dans les quartiers, 3 classes (Eco, Serenity, Prestige).
- **Carlinq Taxi** : points fixes en bordure de route, tarification simple.

## Deux applications distinctes

Le cahier des charges prévoit **deux apps publiées séparément** (« App passager + app chauffeur », §5.1 / §5.2).
Ne jamais les refusionner.

| Dossier | App | Package Android | Rôles |
|---|---|---|---|
| `passager/` | **Carlinq** | `com.carlinq.passager` | `passenger` |
| `chauffeur/` | **Carlinq Chauffeur** | `com.carlinq.chauffeur` | `drivers`, `copilote` |
| `carlinq_core/` | package partagé (pas une app) | - | - |

- `drivers` : chauffeur affilié Carlinq (reçoit les commandes, commission 8%).
- `copilote` : chauffeur indépendant ou société de transport qui paie un cota mensuel (5 000 XAF/mois - Pack Premium) pour utiliser la plateforme avec sa propre flotte.

## Règles UX importantes (issues du cahier des charges v1.2)

- **Arrêts intermédiaires illimités** repositionnables par glisser-déposer avec supplément par arrêt.
- **Pause Arrêt** : bouton chauffeur pour arrêt impromptu, chronomètre visible passager + chauffeur.
- **Anti-embouteillage** : bouton chauffeur qui appelle Google Routes API pour proposer 3 itinéraires alternatifs.
- **Supplement embouteillage** : détection auto (vitesse < 5 km/h > seuil) + déclaration manuelle, tarifié à la minute après tolérance.
- **Supplément route dégradée** : automatique en Carlinq Flexible (+5%, +10% ou +15%).
- **Retour maison** : 1 tap, respecte la règle du mode (Flexible = domicile exact, Taxi = bordure).
- **Portefeuille passager** : minimum 500 XAF. Annulation gratuite dans les 15 premières secondes ou si chauffeur en retard.
- **Système de points chauffeur** : +2 par course terminée, -5 par annulation injustifiée, suspension < 20 points.
- **Fenêtre de refus quotidienne** : 5-10 min/jour sans pénalité.

## Structure du code

- `carlinq_core/lib/carlinq_core.dart` (barrel) : `AppColors` / `AppTheme` (Table 18, Roboto), enums `UserRole`, `CarlinqMode`, `ServiceClass`, `BaseAppState` (thème, langue, mode, classe), `FakeMap` (placeholder à remplacer par `GoogleMap`), `StopsEditor` (arrêts réordonnables), `PriceLine`, `xaf()`, `ChatScreen`, `NotificationsScreen`, `editHomeAddress` / `confirmDeleteAccount`.
- `carlinq_core/lib/testing.dart` : `loadRealFonts()` pour les tests (non exporté par le barrel).
- `carlinq_core` : `ApiClient` (dart:io, jetons, refresh), `BaseAppState.login/signup/logout`, `placeFor()` (adresse → coordonnées), `showServerSettings()`.
- Chaque `AppState` a deux modes : **connecté** (`live`, données API) et **démo** (données embarquées). Toute nouvelle fonctionnalité doit gérer les deux.
- Chauffeur : `core/goals/goal_share.dart` (modèle du partage d'objectif, même calcul que l'API), `features/driver/goal_share_screen.dart`, `features/auth/session_router.dart` (profil manquant / validation / tableau de bord).
- Dans chaque app, `lib/core/state/app_state.dart` définit **son** `AppState extends BaseAppState` :
  - passager : portefeuille, domicile ;
  - chauffeur : rôle (Drivers / Copilote), points, objectifs, fenêtre de refus, Pack Premium, domicile.
- `passager/lib/features/` : `auth/` (Splash → Bienvenue → Inscription 2 étapes / Connexion), `passenger/` (écrans §5.1), `shared/main_shell.dart` (4 onglets).
- `chauffeur/lib/features/` : `auth/` (Splash → Bienvenue → Choix Drivers/Copilote → Inscription 3 étapes → `PendingValidationScreen`, Connexion), `driver/` (écrans §5.2, accent selon le rôle), `shared/main_shell.dart` (5 onglets).
- `docs/MAQUETTES.md` : correspondance écran ↔ cahier des charges pour les deux apps, captures dans `docs/maquettes/` (préfixe `p` passager, `c` chauffeur), régénérées par `test/maquettes_test.dart` de chaque app.
- Code utilisé par les deux apps → `carlinq_core` ; code propre à un public → l'app concernée. Pas d'import d'une app vers l'autre.

## Style de code

- `flutter analyze` doit passer sans erreurs (les `info` de style constants sont tolérées pour l'itération UI actuelle).
- Utiliser `Provider` pour l'état partagé, `setState` local pour l'état d'écran.
- Ne pas ajouter de dépendances majeures sans validation (`google_maps_flutter`, `firebase_*` sont attendues en Phase MVP).

## Bugs communs à surveiller

- En Windows, le `flutter build` peut échouer avec espaces dans le chemin - les apps vivent dans `Carlinq project/Application/<app>/`.
- `test/widget_test.dart` doit référencer `CarlinqPassagerApp` / `CarlinqChauffeurApp`, pas `MyApp`.
- Après modification de `carlinq_core`, relancer `flutter test` dans **les deux** apps.
- Ne pas lancer plusieurs `flutter test` / `flutter build` en parallèle : ils se bloquent sur le verrou du SDK.
- Les tests widget doivent charger les polices réelles (`loadRealFonts()` de `package:carlinq_core/testing.dart`), sinon la police de test à glyphes carrés (~2x plus large) produit de faux débordements.
- `AppTheme.light(googleFonts: false)` en test : Google Fonts tente sinon un téléchargement réseau.
- Tout écran avec `Timer.periodic` (suivi de course, navigation, commande) doit l'annuler dans `dispose()`.
- `flutter_localizations` est requis pour la locale `fr` (sinon exception MaterialLocalizations).
- `flutter test` remplace le client HTTP : les tests d'API (`test/api_e2e_test.dart`) exécutent leur corps dans `HttpOverrides.runWithHttpOverrides(..., _RealHttp())`.
- Release Android : HTTPS seul et `allowBackup=false` dans `src/main/AndroidManifest.xml` ; le HTTP local n'est autorisé qu'en debug. Sans `android/key.properties`, une release locale échoue.
- URL de l'API chiffrée dans `carlinq_core/lib/src/api/lien_api.dart` ; développement : `--dart-define=CARLINQ_API_URL=http://<ip>:8010`.
