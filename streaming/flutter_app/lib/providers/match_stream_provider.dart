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
final gameStateProvider = StreamProvider.autoDispose<GameState>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.gameStateStream;
});

/// Latest PNG frame bytes from the emulator.
final frameProvider = StreamProvider.autoDispose<Uint8List>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.frameStream;
});

/// Current viewer count for the match.
final viewerCountProvider = StreamProvider.autoDispose<int>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.viewerCountStream;
});

/// Whether the WebSocket is connected.
final wsConnectedProvider = StreamProvider.autoDispose<bool>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.connectionStream;
});

/// Fires when the match ends.
final matchEndProvider = StreamProvider.autoDispose<void>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.matchEndStream;
});

/// Fires on round end with details.
final roundEndProvider =
    StreamProvider.autoDispose<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.roundEndStream;
});
