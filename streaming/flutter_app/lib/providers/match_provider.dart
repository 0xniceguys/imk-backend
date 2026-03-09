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
  int _fastPollingRefs = 0;
  static const _basePollSeconds = 5;
  static const _fastPollSeconds = 2;
  static const _maxPollSeconds = 20;
  static const _maxFastPollSeconds = 6;

  MatchNotifier(this._api)
    : super(const MatchState(matches: [], hasLoaded: false)) {
    unawaited(refresh());
    _schedulePoll(_basePollSeconds);
  }

  bool get _isFastPolling => _fastPollingRefs > 0;

  int _resolvedBasePollSeconds() {
    if (_isFastPolling) return _fastPollSeconds;

    // Keep polling tighter around go-live transitions even on arena list.
    final nextUp = state.matches.cast<Match?>().firstWhere(
      (m) => m?.status == MatchStatus.upcoming && m?.queuePosition == 1,
      orElse: () => null,
    );
    if (nextUp != null) {
      final startsAt = nextUp.queueStartsAt;
      if (startsAt != null) {
        final remain = startsAt.difference(DateTime.now()).inSeconds;
        if (remain <= 15) return 2;
        if (remain <= 60) return 3;
      }
    }

    if (state.matches.any((m) => m.status == MatchStatus.live)) {
      return 3;
    }
    return _basePollSeconds;
  }

  void _schedulePoll(int seconds) {
    _pollTimer?.cancel();
    _pollTimer = Timer(Duration(seconds: seconds), () async {
      try {
        await refresh();
      } catch (_) {
        _failureCount = (_failureCount + 1).clamp(0, 4);
      }
      final base = _resolvedBasePollSeconds();
      final max = _isFastPolling ? _maxFastPollSeconds : _maxPollSeconds;
      final next = _failureCount == 0
          ? base
          : (base * (1 << _failureCount)).clamp(base, max).toInt();
      _schedulePoll(next);
    });
  }

  /// Speeds up status refresh for screens where match state transitions are
  /// time-sensitive (e.g. detail countdown and live stream lifecycle).
  void startFastPolling() {
    _fastPollingRefs++;
    if (_fastPollingRefs == 1) {
      _failureCount = 0;
      unawaited(refresh());
      _schedulePoll(_fastPollSeconds);
    }
  }

  void stopFastPolling() {
    if (_fastPollingRefs == 0) return;
    _fastPollingRefs--;
    if (_fastPollingRefs == 0) {
      _failureCount = 0;
      _schedulePoll(_basePollSeconds);
    }
  }

  Future<void> refresh() async {
    final matches = await _api.fetchMatches();
    if (matches.isNotEmpty) {
      _failureCount = 0;
      state = state.copyWith(matches: matches, hasLoaded: true);
    } else {
      // Empty list is a valid response (no matches in DB yet).
      // Never treat it as a network failure — keep polling at normal rate.
      _failureCount = 0;
      // Preserve previously loaded matches so UI doesn't flicker to empty.
      state = state.copyWith(
        matches: state.matches.isEmpty ? [] : state.matches,
        hasLoaded: true,
      );
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
