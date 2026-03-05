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

class MatchState {
  final List<Match> matches;
  final bool hasLoaded; // true once first fetch completes (even if empty)

  const MatchState({required this.matches, required this.hasLoaded});

  MatchState copyWith({List<Match>? matches, bool? hasLoaded}) => MatchState(
        matches: matches ?? this.matches,
        hasLoaded: hasLoaded ?? this.hasLoaded,
      );
}

class MatchNotifier extends StateNotifier<MatchState> {
  final ApiService _api;
  Timer? _pollTimer;
  int _failureCount = 0;
  static const _basePollSeconds = 10;
  static const _maxPollSeconds = 120;

  MatchNotifier(this._api) : super(const MatchState(matches: [], hasLoaded: false)) {
    refresh();
    _schedulePoll(_basePollSeconds);
  }

  void _schedulePoll(int seconds) {
    _pollTimer?.cancel();
    _pollTimer = Timer(Duration(seconds: seconds), () async {
      await refresh();
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
      state = state.copyWith(matches: matches, hasLoaded: true);
    } else {
      // Empty list could be a real empty result OR a network failure.
      // Either way, mark hasLoaded = true so UI doesn't spin forever.
      if (state.matches.isNotEmpty) {
        // Network may be down — keep old data, backoff
        _failureCount = (_failureCount + 1).clamp(0, 4);
        state = state.copyWith(hasLoaded: true);
      } else {
        // First load returned empty — no matches in DB, not an error
        _failureCount = 0;
        state = state.copyWith(matches: [], hasLoaded: true);
      }
    }
  }

  Match? matchById(String id) {
    try {
      return state.matches.firstWhere((m) => m.id == id);
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


final matchProvider = StateNotifierProvider<MatchNotifier, MatchState>(
  (ref) => MatchNotifier(ref.read(apiServiceProvider)),
);
