import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

final clockTickProvider = StreamProvider<DateTime>((ref) async* {
  yield DateTime.now();
  while (true) {
    await Future<void>.delayed(const Duration(seconds: 1));
    yield DateTime.now();
  }
});

