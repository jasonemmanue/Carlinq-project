import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:provider/provider.dart';
import 'package:carlinq_core/carlinq_core.dart';

import 'core/state/app_state.dart';
import 'features/auth/splash_screen.dart';

void main() {
  // Aucun journal lisible par adb logcat dans les versions publiees.
  if (kReleaseMode) {
    debugPrint = (String? message, {int? wrapWidth}) {};
  }
  runApp(const CarlinqChauffeurApp());
}

/// App Chauffeur Carlinq, Drivers et Copilote (§5.2 du cahier des charges).
class CarlinqChauffeurApp extends StatelessWidget {
  const CarlinqChauffeurApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => AppState(),
      child: Consumer<AppState>(
        builder: (context, app, _) => MaterialApp(
          title: 'Carlinq Chauffeur',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          themeMode: app.themeMode,
          locale: app.locale,
          supportedLocales: const [Locale('fr'), Locale('en')],
          localizationsDelegates: GlobalMaterialLocalizations.delegates,
          home: const SplashScreen(),
        ),
      ),
    );
  }
}
