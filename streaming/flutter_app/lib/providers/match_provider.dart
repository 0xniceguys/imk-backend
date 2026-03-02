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
  int _failureCount = 0;
  static const _basePollSeconds = 10;
  static const _maxPollSeconds = 120;

  MatchNotifier(this._api) : super([]) {
    refresh();
    _schedulePoll(_basePollSeconds);
  }

  void _schedulePoll(int seconds) {
    _pollTimer?.cancel();
    _pollTimer = Timer(Duration(seconds: seconds), () async {
      await refresh();
      // Next interval: double on failure, reset on success, cap at max
      final next = _failureCount == 0
          ? _basePollSeconds
          : (_basePollSeconds * (1 << _failureCount))
              .clamp(_basePollSeconds, _maxPollSeconds);
      _schedulePoll(next);
    });
  }

  Future<void> refresh() async {
    final matches = await _api.fetchMatches();
    if (matches.isNotEmpty) {
      _failureCount = 0;
      state = matches;
    } else if (state.isNotEmpty) {
      // Network may be down — keep old data, count failure for backoff
      _failureCount = (_failureCount + 1).clamp(0, 4);
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
