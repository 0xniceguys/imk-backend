import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/game_state.dart';
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

/// Latest PNG frame bytes from the emulator.
final frameProvider = StreamProvider<Uint8List>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.frameStream;
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

/// Fires when the match ends.
final matchEndProvider = StreamProvider<void>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.matchEndStream;
});

/// Fires on round end with details.
final roundEndProvider = StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.roundEndStream;
});

/// Fires when backend emits stream pipeline status updates.
final streamingStateProvider = StreamProvider<StreamingStateEvent>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.streamingStateStream;
});
