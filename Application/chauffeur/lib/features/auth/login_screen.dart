import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:carlinq_core/carlinq_core.dart';

import '../../core/state/app_state.dart';
import '../shared/main_shell.dart';
import 'session_router.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  UserRole _role = UserRole.drivers;
  final _phoneCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  bool _loading = false;
  String? _error;

  Future<void> _submit() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final app = context.read<AppState>();
    try {
      await app.login(_phoneCtrl.text.trim(), _passCtrl.text);
      if (app.me?['role'] == 'passenger' || app.me?['role'] == 'admin') {
        app.logout();
        throw ApiException(403, 'WRONG_APP',
            "Ce compte n'est pas un compte chauffeur : utilisez l'app Carlinq passager.");
      }
      if (!mounted) return;
      goHome(context, app);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.read<AppState>();
    return Scaffold(
      appBar: AppBar(
        title: const Text('Connexion'),
        actions: [
          if (!kReleaseMode)
            IconButton(
              tooltip: 'Serveur API',
              icon: const Icon(Icons.dns_outlined),
              onPressed: () => showServerSettings(context, app.api),
            ),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Content de vous revoir !',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 4),
              const Text(
                'Connectez-vous a votre espace chauffeur',
                style: TextStyle(color: AppColors.textSecondary),
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _phoneCtrl,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(
                    labelText: 'Telephone (+237...)',
                    prefixIcon: Icon(Icons.phone_iphone)),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _passCtrl,
                obscureText: true,
                onSubmitted: (_) => _submit(),
                decoration: const InputDecoration(
                    labelText: 'Mot de passe',
                    prefixIcon: Icon(Icons.lock_outline)),
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: AppColors.danger)),
              ],
              const SizedBox(height: 16),
              ElevatedButton(
                onPressed: _loading ? null : _submit,
                child: _loading
                    ? const SizedBox(
                        width: 22,
                        height: 22,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: Colors.white))
                    : const Text('Se connecter'),
              ),
              const SizedBox(height: 24),
              const Divider(),
              const SizedBox(height: 8),
              const Text('Mode demo (hors ligne)',
                  style: TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              SegmentedButton<UserRole>(
                segments: const [
                  ButtonSegment(
                      value: UserRole.drivers,
                      label: Text('Drivers'),
                      icon: Icon(Icons.directions_car)),
                  ButtonSegment(
                      value: UserRole.copilote,
                      label: Text('Copilote'),
                      icon: Icon(Icons.groups_2)),
                ],
                selected: {_role},
                onSelectionChanged: (s) => setState(() => _role = s.first),
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: () {
                  app.setRole(_role);
                  Navigator.of(context).pushAndRemoveUntil(
                    MaterialPageRoute(builder: (_) => const MainShell()),
                    (_) => false,
                  );
                },
                icon: const Icon(Icons.play_circle_outline),
                label: const Text('Explorer en mode demo'),
              ),
              const SizedBox(height: 16),
              if (!kReleaseMode)
                Text('Serveur : ${app.api.baseUrl}',
                    style: const TextStyle(
                        fontSize: 11, color: AppColors.textSecondary)),
            ],
          ),
        ),
      ),
    );
  }
}
