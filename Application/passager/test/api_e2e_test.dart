// Test de bout en bout de l'app Passager contre l'API reelle (Docker).
//
//   cd API && docker compose up -d
//   flutter test test/api_e2e_test.dart --dart-define=API_E2E=true \
//       --dart-define=CARLINQ_API_URL=http://localhost:8010
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:carlinq_core/carlinq_core.dart';

import 'package:carlinq_passager/core/state/app_state.dart';

const _enabled = bool.fromEnvironment('API_E2E');

void main() {
  test(
      'passager : inscription, recharge, devis, course, notation',
      () => HttpOverrides.runWithHttpOverrides(() async {
            final app = AppState();
            final phone =
                '+2374${(DateTime.now().millisecondsSinceEpoch % 100000000).toString().padLeft(8, '0')}';
            await app.signup(
                fullName: 'Passager App E2E',
                phone: phone,
                password: 'Password123!',
                role: UserRole.passenger);
            expect(app.live, isTrue);
            expect(app.walletBalance, 0);

            await app.reloadWallet(15000, channel: 'mtn_momo');
            expect(app.walletBalance, 15000);
            expect(app.transactions.first['kind'], 'topup');

            await app.loadZones();
            expect(app.zones, isNotEmpty, reason: 'zones Carlinq Taxi de demo');

            final quote = await app.estimate(
                mode: CarlinqMode.flexible,
                serviceClass: ServiceClass.eco,
                pickup: 'Akwa',
                destination: 'Bonamoussadi',
                stops: ['Pharmacie'],
                places: 1);
            expect(quote['stop_supplement_xaf'], 300);

            final ride = await app.createRide(
                mode: CarlinqMode.flexible,
                serviceClass: ServiceClass.eco,
                pickup: 'Akwa',
                destination: 'Bonamoussadi',
                stops: ['Pharmacie'],
                places: 1,
                paymentMethod: 'wallet');
            expect(ride['status'], 'pending');
            expect(app.rides.first['id'], ride['id']);

            // Chauffeur de demo (Prisca, Flexible Eco) : acceptation et fin de course.
            final driver = ApiClient();
            final t = await driver.post('/auth/login',
                {'phone': '+237670000002', 'password': const String.fromEnvironment('CARLINQ_TEST_PASSWORD')});
            driver.setTokens(
                t['access_token'] as String, t['refresh_token'] as String);
            await driver
                .post('/rides/${ride['id']}/accept', {'eta_seconds': 120});
            expect((await app.getRide(ride['id'] as int))['driver_name'],
                'Prisca Lema');
            await driver.post('/rides/${ride['id']}/start');
            await driver.post(
                '/rides/${ride['id']}/complete', {'cash_received': false});

            final done = await app.getRide(ride['id'] as int);
            expect(done['status'], 'completed');
            await app.loadWallet();
            expect(app.walletBalance, 15000 - (done['total_xaf'] as int));
            await app.rateRide(ride['id'] as int, 5, ['Ponctuel'], 'Parfait');
          }, _RealHttp()),
      skip: !_enabled);
}

/// flutter test installe un faux client HTTP dans la zone du test :
/// on revient au client reel pour appeler l'API.
class _RealHttp extends HttpOverrides {}
