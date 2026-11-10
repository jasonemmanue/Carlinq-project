import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'lien_api.dart';

/// Erreur renvoyee par l'API Carlinq : `{"error": {"code", "message", "details"}}`.
class ApiException implements Exception {
  ApiException(this.status, this.code, this.message, [this.details = const {}]);

  final int status;
  final String code;
  final String message;
  final Map<String, dynamic> details;

  /// L'API est injoignable (reseau, serveur arrete).
  bool get isNetwork => status == 0;

  @override
  String toString() => message;
}

/// Client HTTP de l'API FastAPI Carlinq (`/api/v1`).
///
/// URL par defaut : celle de l'API deployee, chiffree dans l'app (`lien_api.dart`).
/// Developpement : `--dart-define=CARLINQ_API_URL=http://<ip>:8010`.
class ApiClient {
  ApiClient({String? baseUrl}) : baseUrl = baseUrl ?? defaultBaseUrl;

  static const String _devUrl = String.fromEnvironment('CARLINQ_API_URL');

  static String get defaultBaseUrl =>
      _devUrl.isNotEmpty ? _devUrl : lienApiDefinitif();

  static const Duration timeout = Duration(seconds: 12);

  String baseUrl;
  String? _accessToken;
  String? _refreshToken;
  final HttpClient _http = HttpClient()..connectionTimeout = timeout;

  bool get isAuthenticated => _accessToken != null;

  void setTokens(String access, String refresh) {
    _accessToken = access;
    _refreshToken = refresh;
  }

  void clearTokens() {
    _accessToken = null;
    _refreshToken = null;
  }

  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Object? body]) =>
      _send('POST', path, body);
  Future<dynamic> put(String path, [Object? body]) => _send('PUT', path, body);
  Future<dynamic> patch(String path, [Object? body]) =>
      _send('PATCH', path, body);
  Future<dynamic> delete(String path) => _send('DELETE', path);

  /// Verifie que l'API repond (`/healthz`).
  Future<bool> ping() async {
    try {
      final res = await _raw('GET', Uri.parse('$baseUrl/healthz'), null)
          .timeout(const Duration(seconds: 4));
      return res.$1 == 200;
    } catch (_) {
      return false;
    }
  }

  Future<dynamic> _send(String method, String path, [Object? body,
      bool retried = false]) async {
    final uri = Uri.parse('$baseUrl/api/v1$path');
    final (int status, String text) res;
    try {
      res = await _raw(method, uri, body).timeout(timeout);
    } on TimeoutException {
      throw ApiException(0, 'NETWORK_TIMEOUT',
          'Le serveur Carlinq ne repond pas.');
    } on SocketException {
      throw ApiException(0, 'NETWORK_UNREACHABLE',
          'Serveur Carlinq injoignable.');
    } on HttpException {
      throw ApiException(0, 'NETWORK_ERROR', 'Erreur reseau.');
    }
    final (status, text) = res;
    final dynamic json = text.isEmpty ? null : jsonDecode(text);

    // Jeton expire : un seul essai de rafraichissement.
    if (status == 401 && !retried && _refreshToken != null &&
        !path.startsWith('/auth/')) {
      try {
        final tokens = await post('/auth/refresh', {'refresh_token': _refreshToken});
        setTokens(tokens['access_token'] as String,
            tokens['refresh_token'] as String);
        return _send(method, path, body, true);
      } on ApiException {
        clearTokens();
      }
    }
    if (status >= 400) {
      final err = (json is Map ? json['error'] : null) as Map?;
      if (err != null) {
        throw ApiException(status, err['code'] as String? ?? 'API_ERROR',
            err['message'] as String? ?? 'Erreur',
            Map<String, dynamic>.from(err['details'] as Map? ?? const {}));
      }
      // Erreurs de validation FastAPI (422) : {"detail": [...]}
      final detail = json is Map ? json['detail'] : null;
      final msg = detail is List && detail.isNotEmpty
          ? '${(detail.first as Map)['msg']}'
          : 'Requete refusee ($status)';
      throw ApiException(status, 'VALIDATION_ERROR', msg);
    }
    return json;
  }

  Future<(int, String)> _raw(String method, Uri uri, Object? body) async {
    final req = await _http.openUrl(method, uri);
    req.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (_accessToken != null) {
      req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_accessToken');
    }
    if (body != null) {
      req.headers.contentType = ContentType.json;
      req.add(utf8.encode(jsonEncode(body)));
    }
    final res = await req.close();
    final text = await res.transform(utf8.decoder).join();
    return (res.statusCode, text);
  }
}
