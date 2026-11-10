# Carlinq sur TestFlight

Les builds iOS se font sur des Mac dans le cloud. Deux apps sont publiées séparément :
`com.carlinq.passager` (Carlinq) et `com.carlinq.chauffeur` (Carlinq Chauffeur).

| Branche | Ce qui se lance | Compte Apple |
|---|---|---|
| `ios/build-check` | Codemagic : compilation iOS sans signature | non |
| `ios/testflight` | Codemagic : IPA signées envoyées à TestFlight | oui |

Le workflow GitHub **Build check** (API, backoffice, Android, iOS sans signature) ne part jamais sur un simple push :
il se lance sur une pull request, ou à la main depuis l'onglet Actions une fois le fichier présent sur `main`.

## À faire une seule fois

1. **Apple Developer** > Identifiers : créer les App IDs `com.carlinq.passager` et `com.carlinq.chauffeur`.
2. **App Store Connect** > Apps : créer une app pour chacun de ces identifiants.
3. **Apple Developer** > Profiles : créer un profil **App Store Connect** par app, avec le certificat Apple
   Distribution déjà utilisé par Prepaxia.
4. **Codemagic** > Add application > GitHub : ajouter ce dépôt, type **Flutter App (via codemagic.yaml)**.
5. **Codemagic** > Code signing identities > iOS provisioning profiles > **Fetch profiles** (clé `Prepaxia ASC`) :
   télécharger les deux profils. Si la clé porte un autre nom sur ce compte, adapter `app_store_connect` dans
   `codemagic.yaml`.

## Lancer un build

```bash
git push origin main:ios/build-check
```

```bash
git push origin main:ios/testflight
```

Les builds arrivent dans TestFlight sans revue bêta, pour les testeurs internes.

## APK Android

Sans `android/key.properties`, une release locale est refusée : c'est volontaire. Pour un APK de test signé avec
la clé de débogage, définir `CI=true`, ou récupérer l'artefact du workflow GitHub **Build check**. Le Play Store
exige une vraie clé de signature.
