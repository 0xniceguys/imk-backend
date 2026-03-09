import 'dart:typed_data';

const _alphabet =
    '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

String base58Encode(Uint8List data) {
  if (data.isEmpty) return '';

  var leadingZeros = 0;
  for (final b in data) {
    if (b != 0) break;
    leadingZeros++;
  }

  var value = BigInt.zero;
  for (final b in data) {
    value = (value << 8) + BigInt.from(b);
  }

  final chars = <String>[];
  final base = BigInt.from(58);
  while (value > BigInt.zero) {
    chars.add(_alphabet[(value % base).toInt()]);
    value = value ~/ base;
  }

  for (var i = 0; i < leadingZeros; i++) {
    chars.add('1');
  }

  return chars.reversed.join();
}

Uint8List base58Decode(String str) {
  if (str.isEmpty) return Uint8List(0);

  var leadingOnes = 0;
  for (final c in str.split('')) {
    if (c != '1') break;
    leadingOnes++;
  }

  var value = BigInt.zero;
  final base = BigInt.from(58);
  for (final c in str.split('')) {
    final digit = _alphabet.indexOf(c);
    if (digit < 0) throw FormatException('Invalid base58 character: $c');
    value = value * base + BigInt.from(digit);
  }

  final bytes = <int>[];
  while (value > BigInt.zero) {
    bytes.add((value % BigInt.from(256)).toInt());
    value = value ~/ BigInt.from(256);
  }

  for (var i = 0; i < leadingOnes; i++) {
    bytes.add(0);
  }

  return Uint8List.fromList(bytes.reversed.toList());
}
