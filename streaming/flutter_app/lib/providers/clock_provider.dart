import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

final clockTickProvider = StreamProvider.autoDispose<DateTime>((ref) async* {
  // keepAlive with a grace period: prevents the stream being torn down when a
  // bottom-sheet or overlay temporarily removes all watchers (e.g.
  // BetBottomSheet), but allows genuine disposal after 5 seconds of no
  // watchers — so the infinite loop doesn't run forever when no countdown is
  // visible.
  final link = ref.keepAlive();

  Timer? disposeTimer;
  ref.onCancel(() {
    // All watchers gone — start a 5s grace period before disposing.
    disposeTimer = Timer(const Duration(seconds: 5), link.close);
  });
  ref.onResume(() {
    // Someone is watching again — cancel the dispose timer.
    disposeTimer?.cancel();
  });
  ref.onDispose(() {
    disposeTimer?.cancel();
  });

  yield DateTime.now();
  while (true) {
    await Future<void>.delayed(const Duration(seconds: 1));
    yield DateTime.now();
  }
});

