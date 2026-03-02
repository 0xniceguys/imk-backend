import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/match.dart';
import '../services/api_service.dart';

/// Singleton API service.
final apiServiceProvider = Provider<ApiService>((ref) {
  final service = ApiService();
  ref.onDispose(service.dispose);
  return service;
});

class MatchNotifier extends StateNotifier<List<Match>> {
  final ApiService _api;
  Timer? _pollTimer;

  MatchNotifier(this._api) : super([]) {
    refresh();
    // Poll every 10s for match list updates
    _pollTimer = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
  }

  Future<void> refresh() async {
    final matches = await _api.fetchMatches();
    if (matches.isNotEmpty || state.isNotEmpty) {
      state = matches;
    }
  }

  Match? matchById(String id) {
    try {
      return state.firstWhere((m) => m.id == id);
    } catch (_) {
      return null;
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }
}

final matchProvider = StateNotifierProvider<MatchNotifier, List<Match>>(
  (ref) => MatchNotifier(ref.read(apiServiceProvider)),
);
