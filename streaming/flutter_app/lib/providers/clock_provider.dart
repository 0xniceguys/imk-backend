import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

final clockTickProvider = StreamProvider.autoDispose<DateTime>((ref) async* {
  // keepAlive prevents the stream being torn down when a bottom-sheet or
  // overlay temporarily removes all watchers (e.g. BetBottomSheet), which
  // would cause a visible 1-second freeze when the sheet closes.
  ref.keepAlive();
  yield DateTime.now();
  while (true) {
    await Future<void>.delayed(const Duration(seconds: 1));
    yield DateTime.now();
  }
});

