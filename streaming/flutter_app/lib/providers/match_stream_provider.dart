import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../core/constants.dart';
import '../models/game_state.dart';
import '../services/match_stream_service.dart';
import '../services/hls_player_service.dart';

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

/// Streaming state changes (initializing, ready, error, etc.)
final streamingStateProvider =
    StreamProvider<Map<String, dynamic>>((ref) {
  final service = ref.watch(matchStreamServiceProvider);
  return service.streamingStateStream;
});

// ── HLS Pre-loading ─────────────────────────────────────────────────────────

/// Singleton HLS player service — owns the [VideoPlayerController] lifecycle.
final hlsPlayerServiceProvider = Provider<HlsPlayerService>((ref) {
  final service = HlsPlayerService();
  ref.onDispose(service.dispose);
  return service;
});

/// Reactive stream of the currently pre-loaded [VideoPlayerController].
///
/// Bridges [HlsPlayerService.controllerNotifier] (a [ValueNotifier]) into
/// Riverpod so any widget watching this provider rebuilds the instant the
/// pre-loaded controller becomes available or is disposed.
final hlsControllerProvider = StreamProvider<VideoPlayerController?>((ref) {
  final svc = ref.watch(hlsPlayerServiceProvider);
  final streamController = StreamController<VideoPlayerController?>();

  // Emit whatever value is already cached immediately (no async gap)
  streamController.add(svc.controllerNotifier.value);

  void onChanged() {
    if (!streamController.isClosed) {
      streamController.add(svc.controllerNotifier.value);
    }
  }

  svc.controllerNotifier.addListener(onChanged);
  ref.onDispose(() {
    svc.controllerNotifier.removeListener(onChanged);
    streamController.close();
  });

  return streamController.stream;
});

/// Reactive stream of [HlsPreloadState] — rebuilds widgets watching loading state.
final hlsPreloadStateProvider = StreamProvider<HlsPreloadState>((ref) {
  final svc = ref.watch(hlsPlayerServiceProvider);
  final streamController = StreamController<HlsPreloadState>();

  streamController.add(svc.stateNotifier.value);

  void onChanged() {
    if (!streamController.isClosed) {
      streamController.add(svc.stateNotifier.value);
    }
  }

  svc.stateNotifier.addListener(onChanged);
  ref.onDispose(() {
    svc.stateNotifier.removeListener(onChanged);
    streamController.close();
  });

  return streamController.stream;
});

/// Always-alive side-effect that wires [streamingStateStream] →
/// [HlsPlayerService.preload] so the video controller pre-initialises
/// the moment the backend signals [streaming_state: ready], regardless of
/// which screen the user is on.
///
/// Both stream subscriptions are properly cancelled on dispose (fixes the
/// previous subscription-leak bug).
///
/// Watch this from the root widget ([app.dart]) so it is always alive.
final globalHlsPreloaderProvider = Provider<void>((ref) {
  ref.keepAlive();

  final hlsService = ref.watch(hlsPlayerServiceProvider);
  final wsService = ref.watch(matchStreamServiceProvider);

  // ── Match-end: clean up the pre-loaded controller ──────────────────────
  final matchEndSub = wsService.matchEndStream.listen((_) {
    debugPrint('[GlobalHlsPreloader] Match ended — stopping HLS preload');
    hlsService.stop();
  });

  // ── Streaming-state: trigger preload on "ready" ─────────────────────────
  final streamingStateSub = wsService.streamingStateStream.listen((event) {
    try {
      final state = event['state'] as String?;
      final rawUrl = event['hls_url'] as String?;
      final matchId = wsService.matchId;

      if (state != 'ready') return;
      if (matchId == null) {
        debugPrint('[GlobalHlsPreloader] streaming_state=ready but no matchId — skipping');
        return;
      }

      final hlsUrl = _resolveHlsUrl(matchId, rawUrl);
      debugPrint('[GlobalHlsPreloader] → preloading match=$matchId url=$hlsUrl');
      hlsService.preload(matchId, hlsUrl);
    } catch (e, st) {
      debugPrint('[GlobalHlsPreloader] Error in streaming state handler: $e\n$st');
    }
  });

  // ── Clean up BOTH subscriptions when the provider is disposed ──────────
  ref.onDispose(() {
    matchEndSub.cancel();
    streamingStateSub.cancel();
    debugPrint('[GlobalHlsPreloader] Disposed — subscriptions cancelled');
  });
});

String _resolveHlsUrl(String matchId, String? hintedUrl) {
  final canonical = '$kStreamBaseUrl/stream/$matchId/stream.m3u8';
  if (hintedUrl == null || hintedUrl.trim().isEmpty) return canonical;
  final trimmed = hintedUrl.trim();
  if (trimmed.contains('/stream/audio/')) return canonical;
  final uri = Uri.tryParse(trimmed);
  if (uri == null) return canonical;
  if (uri.hasScheme) return trimmed;
  if (trimmed.startsWith('/')) return '$kStreamBaseUrl$trimmed';
  return canonical;
}

