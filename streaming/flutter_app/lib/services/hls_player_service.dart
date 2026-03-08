import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:video_player/video_player.dart';

/// Manages a single [VideoPlayerController] that is pre-initialized as soon
/// as the backend signals [streaming_state: ready] — before the user navigates
/// to the live screen.
///
/// The screen simply *attaches* to the already-running controller, so playback
/// appears instant on navigation.
///
/// Lifecycle:
///   1. [preload] — called when WS fires streaming_state=ready.
///   2. Controller initialises + starts playing in the background.
///   3. [LiveMatchScreen] calls [controller] to get the ready-to-display widget.
///   4. [stop] — called when match ends or user leaves; disposes controller.
///   5. [dispose] — called when provider is destroyed (app teardown).
class HlsPlayerService {
  // Publicly observable state
  final ValueNotifier<VideoPlayerController?> controllerNotifier =
      ValueNotifier(null);
  final ValueNotifier<String?> activeMatchIdNotifier = ValueNotifier(null);
  final ValueNotifier<HlsPreloadState> stateNotifier =
      ValueNotifier(HlsPreloadState.idle);

  VideoPlayerController? get controller => controllerNotifier.value;
  String? get activeMatchId => activeMatchIdNotifier.value;
  HlsPreloadState get state => stateNotifier.value;

  // Internal
  bool _disposed = false;
  bool _initializing = false;
  int _initToken = 0; // Incremented on each new preload() call to cancel stale awaits

  /// Pre-loads the HLS stream for [matchId] at [hlsUrl].
  ///
  /// Safe to call multiple times; subsequent calls for the same match+url are
  /// no-ops once initialised. A call for a different match cancels any
  /// in-progress initialisation and starts fresh.
  Future<void> preload(String matchId, String hlsUrl) async {
    if (_disposed) return;

    final alreadyPlaying =
        activeMatchIdNotifier.value == matchId &&
        controller?.value.isInitialized == true;
    if (alreadyPlaying) {
      debugPrint('[HlsPlayerService] Already playing match $matchId — skipping preload');
      return;
    }

    final alreadyInitializing =
        _initializing && activeMatchIdNotifier.value == matchId;
    if (alreadyInitializing) {
      debugPrint('[HlsPlayerService] Already initializing match $matchId — skipping duplicate');
      return;
    }

    // Cancel any previous controller / in-flight init
    await _teardown();
    if (_disposed) return;

    final token = ++_initToken;
    _initializing = true;
    activeMatchIdNotifier.value = matchId;
    _setState(HlsPreloadState.initializing);

    debugPrint('[HlsPlayerService] Pre-loading HLS: matchId=$matchId url=$hlsUrl');

    VideoPlayerController? ctrl;
    try {
      ctrl = VideoPlayerController.networkUrl(
        Uri.parse(hlsUrl),
        videoPlayerOptions: VideoPlayerOptions(mixWithOthers: false),
      );

      await ctrl.initialize().timeout(const Duration(seconds: 15));

      // Guard: check nothing changed while we were awaiting
      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale init for match $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      await ctrl.setVolume(1.0);
      await ctrl.play();

      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale after play() for match $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      controllerNotifier.value = ctrl;
      _setState(HlsPreloadState.playing);
      debugPrint('[HlsPlayerService] ✅ Pre-loaded and playing match $matchId');
    } on TimeoutException {
      debugPrint('[HlsPlayerService] ⏰ Timeout initializing HLS for match $matchId');
      await _safeDispose(ctrl);
      if (!_disposed && _initToken == token) {
        _setState(HlsPreloadState.error);
      }
    } catch (e, st) {
      debugPrint('[HlsPlayerService] ❌ Error initializing HLS for match $matchId: $e\n$st');
      await _safeDispose(ctrl);
      if (!_disposed && _initToken == token) {
        _setState(HlsPreloadState.error);
      }
    } finally {
      if (_initToken == token) _initializing = false;
    }
  }

  /// Called when the active match ends. Disposes the controller and resets state.
  Future<void> stop() async {
    debugPrint('[HlsPlayerService] stop() called for match ${activeMatchIdNotifier.value}');
    await _teardown();
    _setState(HlsPreloadState.idle);
  }

  Future<void> _teardown() async {
    _initializing = false;
    ++_initToken; // Invalidate in-flight init
    final old = controllerNotifier.value;
    controllerNotifier.value = null;
    activeMatchIdNotifier.value = null;
    await _safeDispose(old);
  }

  Future<void> _safeDispose(VideoPlayerController? ctrl) async {
    if (ctrl == null) return;
    try {
      await ctrl.dispose();
    } catch (e) {
      debugPrint('[HlsPlayerService] Error disposing controller: $e');
    }
  }

  void _setState(HlsPreloadState s) {
    if (!_disposed) stateNotifier.value = s;
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _teardown(); // fire-and-forget on app teardown
    controllerNotifier.dispose();
    activeMatchIdNotifier.dispose();
    stateNotifier.dispose();
  }
}

enum HlsPreloadState { idle, initializing, playing, error }
