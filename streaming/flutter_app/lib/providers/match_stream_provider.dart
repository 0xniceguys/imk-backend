import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/game_state.dart';
import '../models/match.dart';
import '../providers/match_provider.dart';
import '../services/match_stream_service.dart';

/// Singleton match stream service.
final matchStreamServiceProvider = Provider<MatchStreamService>((ref) {
  final service = MatchStreamService();
  ref.onDispose(service.dispose);
  return service;
});

/// Live game state from WebSocket — updates every time the backend sends one.
final gameStateProvider = StreamProvider<GameState>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.gameStateStream;
});

/// Current viewer count for the match.
final viewerCountProvider = StreamProvider<int>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.viewerCountStream;
});

/// Whether the WebSocket is connected.
final wsConnectedProvider = StreamProvider<bool>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.connectionStream;
});

/// Fires when the match ends — payload includes winner_player, rounds_won_p1/p2.
final matchEndProvider = StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.matchEndStream;
});

/// Fires on round end with details.
final roundEndProvider = StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.roundEndStream;
});

/// Streaming state changes (initializing, ready, error, etc.)
final streamingStateProvider =
    StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.streamingStateStream;
});

/// Always-alive side-effect that watches [matchProvider] for a live match
/// and pre-connects the WebSocket as soon as one appears.
final autoWsPreconnectProvider = Provider<void>((ref) {
  ref.keepAlive();

  final wsService = ref.watch(matchStreamServiceProvider);
  String? preconnectedMatchId;

  ref.listen<MatchState>(matchProvider, (_, next) {
    final liveMatch = next.matches
        .where((m) => m.status == MatchStatus.live)
        .firstOrNull;
    if (liveMatch == null) return;
    if (preconnectedMatchId == liveMatch.id) return;

    preconnectedMatchId = liveMatch.id;
    debugPrint('[AutoWsPreconnect] Pre-connecting WS for live match: ${liveMatch.id}');
    wsService.connect(liveMatch.id);
  }, fireImmediately: true);
});
