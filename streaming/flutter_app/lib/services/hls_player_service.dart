import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:video_player/video_player.dart';

class HlsPlayerService {
  final ValueNotifier<VideoPlayerController?> controllerNotifier =
      ValueNotifier(null);
  final ValueNotifier<String?> activeMatchIdNotifier = ValueNotifier(null);
  final ValueNotifier<HlsPreloadState> stateNotifier =
      ValueNotifier(HlsPreloadState.idle);

  VideoPlayerController? get controller => controllerNotifier.value;
  String? get activeMatchId => activeMatchIdNotifier.value;
  HlsPreloadState get state => stateNotifier.value;

  bool _disposed = false;
  bool _initializing = false;
  int _initToken = 0;

  // ── Stuck watchdog ────────────────────────────────────────────────────────
  Timer? _watchdogTimer;
  Duration _lastPosition = Duration.zero;
  int _stuckTicks = 0; // consecutive 5s ticks with no position advance
  static const _watchdogInterval = Duration(seconds: 5);
  static const _stuckTicksBeforeRestart = 2; // 2 × 5s = 10s frozen → restart

  Future<void> preload(String matchId, String hlsUrl) async {
    if (_disposed) return;

    final alreadyPlaying =
        activeMatchIdNotifier.value == matchId &&
        controller?.value.isInitialized == true;
    if (alreadyPlaying) {
      debugPrint('[HlsPlayerService] Already playing $matchId — skipping preload');
      return;
    }

    final alreadyInitializing =
        _initializing && activeMatchIdNotifier.value == matchId;
    if (alreadyInitializing) {
      debugPrint('[HlsPlayerService] Already initializing $matchId — skipping duplicate');
      return;
    }

    await _teardown();
    if (_disposed) return;

    final token = ++_initToken;
    _initializing = true;
    activeMatchIdNotifier.value = matchId;
    _setState(HlsPreloadState.initializing);

    debugPrint('[HlsPlayerService] Loading HLS: $matchId — $hlsUrl');

    VideoPlayerController? ctrl;
    try {
      ctrl = VideoPlayerController.networkUrl(
        Uri.parse(hlsUrl),
        videoPlayerOptions: VideoPlayerOptions(mixWithOthers: false),
      );

      await ctrl.initialize().timeout(const Duration(seconds: 15));

      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale init for $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      // Pre-load muted — audio only starts when user opens the live screen.
      // This prevents background audio leaking while user is browsing other tabs.
      await ctrl.setVolume(0.0);
      await ctrl.play();

      if (_disposed || _initToken != token) {
        debugPrint('[HlsPlayerService] Stale after play() for $matchId — discarding');
        await _safeDispose(ctrl);
        return;
      }

      controllerNotifier.value = ctrl;
      _setState(HlsPreloadState.playing);
      _startWatchdog(matchId, hlsUrl);
      debugPrint('[HlsPlayerService] ✅ Playing $matchId');
    } on TimeoutException {
      debugPrint('[HlsPlayerService] ⏰ Timeout initializing HLS $matchId');
      await _safeDispose(ctrl);
      if (!_disposed && _initToken == token) _setState(HlsPreloadState.error);
    } catch (e, st) {
      debugPrint('[HlsPlayerService] ❌ Init error $matchId: $e\n$st');
      await _safeDispose(ctrl);
      if (!_disposed && _initToken == token) _setState(HlsPreloadState.error);
    } finally {
      if (_initToken == token) _initializing = false;
    }
  }

  // ── Watchdog ──────────────────────────────────────────────────────────────

  void _startWatchdog(String matchId, String hlsUrl) {
    _stopWatchdog();
    _lastPosition = Duration.zero;
    _stuckTicks = 0;

    _watchdogTimer = Timer.periodic(_watchdogInterval, (_) {
      if (_disposed) {
        _stopWatchdog();
        return;
      }
      final ctrl = controllerNotifier.value;
      if (ctrl == null || !ctrl.value.isInitialized) {
        _stopWatchdog();
        return;
      }

      try {
        final pos = ctrl.value.position;
        final isPlaying = ctrl.value.isPlaying;
        final hasError = ctrl.value.hasError;

        if (hasError) {
          debugPrint('[HlsPlayerService] 🔴 Controller error detected — restarting');
          _stopWatchdog();
          preload(matchId, hlsUrl);
          return;
        }

        if (!isPlaying) {
          // Not playing: try to resume before flagging as stuck
          ctrl.play().catchError((_) {});
          return;
        }

        if (pos == _lastPosition) {
          _stuckTicks++;
          debugPrint(
            '[HlsPlayerService] ⚠️ Position stuck at $pos (tick $_stuckTicks/$_stuckTicksBeforeRestart)',
          );
          if (_stuckTicks >= _stuckTicksBeforeRestart) {
            debugPrint('[HlsPlayerService] 🔄 Stream frozen — restarting controller');
            _stopWatchdog();
            preload(matchId, hlsUrl);
          }
        } else {
          _stuckTicks = 0;
          _lastPosition = pos;
        }
      } catch (e) {
        debugPrint('[HlsPlayerService] Watchdog check error: $e');
      }
    });
  }

  void _stopWatchdog() {
    _watchdogTimer?.cancel();
    _watchdogTimer = null;
    _stuckTicks = 0;
  }

  /// Unmutes the active controller — called when user enters the live screen.
  void unmute() {
    try {
      controllerNotifier.value?.setVolume(1.0);
    } catch (e) {
      debugPrint('[HlsPlayerService] unmute error: $e');
    }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  Future<void> stop() async {
    debugPrint('[HlsPlayerService] stop() — match ${activeMatchIdNotifier.value}');
    _stopWatchdog();
    await _teardown();
    _setState(HlsPreloadState.idle);
  }

  Future<void> _teardown() async {
    _stopWatchdog();
    _initializing = false;
    ++_initToken;
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
    _stopWatchdog();
    _teardown();
    controllerNotifier.dispose();
    activeMatchIdNotifier.dispose();
    stateNotifier.dispose();
  }
}

enum HlsPreloadState { idle, initializing, playing, error }
