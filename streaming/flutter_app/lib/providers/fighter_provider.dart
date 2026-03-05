import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'dart:async';
import '../models/fighter.dart';
import 'match_provider.dart';
import 'fighter_image_cache_provider.dart';

class FighterNotifier extends StateNotifier<List<Fighter>> {
  final Ref _ref;

  FighterNotifier(this._ref) : super([]) {
    refresh();
  }

  Future<void> refresh() async {
    final api = _ref.read(apiServiceProvider);
    final fighters = await api.fetchFighters();
    if (fighters.isNotEmpty || state.isNotEmpty) {
      state = fighters;
      unawaited(
        _ref.read(fighterImageCacheServiceProvider).prefetchFighters(fighters),
      );
    }
  }
}

final fighterProvider =
    StateNotifierProvider<FighterNotifier, List<Fighter>>(
  (ref) => FighterNotifier(ref),
);
