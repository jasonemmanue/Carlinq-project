// Test de bout en bout de l'app Chauffeur contre l'API reelle (Docker).
//
//   cd API && docker compose up -d
//   flutter test test/api_e2e_test.dart --dart-define=API_E2E=true \
//       --dart-define=CARLINQ_API_URL=http://localhost:8010
//
// Cree de nouveaux comptes a chaque execution (telephones uniques).
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:carlinq_core/carlinq_core.dart';

import 'package:carlinq_chauffeur/core/goals/goal_share.dart';
import 'package:carlinq_chauffeur/core/state/app_state.dart';

const _enabled = bool.fromEnvironment('API_E2E');

String _phone(int suffix) =>
    '+2375${(DateTime.now().millisecondsSinceEpoch % 10000000).toString().padLeft(7, '0')}$suffix';

void main() {
  test(
      'chauffeur : inscription, validation, course, objectif et partage',
      () => HttpOverrides.runWithHttpOverrides(() async {
            final admin = ApiClient();
            final tokens = await admin.post('/auth/login',
                {'phone': '+237600000000', 'password': const String.fromEnvironment('CARLINQ_TEST_PASSWORD')});
            admin.setTokens(tokens['access_token'] as String,
                tokens['refresh_token'] as String);

            // Passager (client HTTP brut) avec un portefeuille approvisionne.
            final passenger = ApiClient();
            final p = await passenger.post('/auth/signup', {
              'full_name': 'Passager E2E',
              'phone': _phone(1),
              'password': 'Password123!',
              'role': 'passenger',
            });
            passenger.setTokens(
                p['access_token'] as String, p['refresh_token'] as String);
            await passenger.post('/wallet/topup',
                {'amount_xaf': 20000, 'channel': 'orange_money'});

            // Chauffeur : l'app elle-meme.
            final app = AppState();
            await app.signup(
                fullName: 'Chauffeur E2E',
                phone: _phone(2),
                password: 'Password123!',
                role: UserRole.drivers);
            expect(app.live, isTrue);
            await app.registerDriverProfile(
                mode: CarlinqMode.flexible,
                serviceClass: ServiceClass.eco,
                brand: 'Toyota',
                model: 'Yaris',
                plate: 'E2E ${DateTime.now().millisecondsSinceEpoch % 100000}');
            expect(app.validationStatus, 'pending');

            await admin.post('/admin/drivers/${app.driver!['id']}/status',
                {'status': 'approved'});
            await app.refreshDriver();
            expect(app.approved, isTrue);
            await app.refreshAll();
            await app.setOnline(true);
            expect(app.online, isTrue);

            // Commande du passager -> offre recue par l'app -> course complete.
            final ride = await passenger.post('/rides', {
              'mode': 'flexible',
              'service_class': 'eco',
              'pickup': placeFor('Akwa').toJson(),
              'destination': placeFor('Bonamoussadi').toJson(),
              'stops': [placeFor('Pharmacie').toJson()],
              'places': 1,
              'payment_method': 'wallet',
            });
            await app.pollOffers();
            expect(app.offers.map((o) => o['id']), contains(ride['id']));
            await app.acceptOffer(ride['id'] as int);
            await app.rideAction('start');
            final done = await app.completeActiveRide();
            expect(done['status'], 'completed');
            expect(app.todayRides, 1);
            expect(app.walletBalance, done['driver_earning_xaf']);
            expect(app.driverPoints, 82); // 80 + 2

            // Objectif de la semaine puis partage avec un chauffeur l'ayant atteint.
            expect(app.hasGoal, isFalse);
            expect(
                await app
                    .createGoal(app.goalTiers.first['target_rides'] as int),
                isNull);
            expect(app.hasGoal, isTrue);
            expect(app.ownRides, 0);
            await app.loadHelperCandidates();
            expect(app.helperCandidates, isNotEmpty,
                reason: 'chauffeurs de demo ayant atteint leur objectif');
            final helper = app.helperCandidates.first;
            expect(await app.inviteHelper(helper, 10), isNull);
            expect(app.shares.single.status, ShareStatus.pending);
            expect(app.livePercent, 10);
            // Regle serveur : au-dela du maximum par aidant.
            expect(await app.inviteHelper(app.helperCandidates.first, 45),
                isNotNull);
          }, _RealHttp()),
      skip: !_enabled);
}

/// flutter test installe un faux client HTTP dans la zone du test :
/// on revient au client reel pour appeler l'API.
class _RealHttp extends HttpOverrides {}
