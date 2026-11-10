// Adresse de l'API jamais ecrite en clair : un `strings` ou un decompilateur sur
// l'APK / l'IPA ne la trouve pas. Morceaux chiffres (XOR avec cle et masque de position),
// reconstitues en memoire au premier usage.
//
// Ce n'est PAS un secret : l'adresse reste visible dans le trafic du telephone de son
// proprietaire. La vraie protection est cote serveur (jetons, droits, limites d'essais).
// Ne jamais passer l'URL de production en --dart-define : elle serait compilee en clair.

const List<int> _cle = [141, 219, 100, 170, 56, 89, 151, 195, 112, 243, 232, 121, 153, 190, 97, 219, 16, 99, 15, 185, 200, 55, 183];
const List<int> _m1 = [194, 131, 41, 246, 127, 122, 34, 97, 39, 175, 103, 1, 11, 133];
const List<int> _m2 = [46, 165, 8, 175, 59, 19, 90, 124, 62, 6, 97, 86, 196, 13];
const List<int> _m3 = [145, 65, 69, 81, 90, 166, 14, 94, 79, 233, 243, 26, 122, 230, 81];

String? _cache;

/// URL de base de l'API deployee.
String lienApiDefinitif() {
  final c = _cache;
  if (c != null) return c;
  final octets = [..._m1, ..._m2, ..._m3];
  final sortie = List<int>.generate(octets.length,
      (i) => octets[i] ^ _cle[(i * 7 + 3) % _cle.length] ^ ((i * 31) & 0xFF));
  return _cache = String.fromCharCodes(sortie);
}
