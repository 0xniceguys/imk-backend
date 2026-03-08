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

  // When true, unmute after init completes (live screen is in the foreground).
  bool _wantsAudio = false;

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
      // If live screen entered while we were still initializing, unmute now.
      if (_wantsAudio) {
        await ctrl.setVolume(1.0);
        debugPrint('[HlsPlayerService] ✅ Playing $matchId (with audio — wantsAudio=true)');
      } else {
        debugPrint('[HlsPlayerService] ✅ Playing $matchId (muted — live screen not open)');
      }
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

  /// Unmutes — called when user enters the live screen.
  /// If init is still in progress, sets wantsAudio=true so preload() will
  /// unmute automatically once the controller is ready.
  void requestAudio() {
    _wantsAudio = true;
    try {
      controllerNotifier.value?.setVolume(1.0);
    } catch (e) {
      debugPrint('[HlsPlayerService] requestAudio error: $e');
    }
  }

  /// Mutes and clears the wantsAudio flag — called when user leaves the live screen.
  void silenceAndReset() {
    _wantsAudio = false;
    try {
      controllerNotifier.value?.setVolume(0.0);
    } catch (e) {
      debugPrint('[HlsPlayerService] silenceAndReset error: $e');
    }
  }

  /// Legacy alias kept for call sites that already use unmute().
  void unmute() => requestAudio();

  /// Legacy alias kept for call sites that already use mute().
  void mute() => silenceAndReset();

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  Future<void> stop() async {
    debugPrint('[HlsPlayerService] stop() — match ${activeMatchIdNotifier.value}');
    _wantsAudio = false; // clear flag so restart doesn't unmute in background
    _stopWatchdog();
    await _teardown();
    // Use 'stopped' (not 'idle') so globalHlsPreloaderProvider knows the user
    // intentionally left and should NOT restart the preload automatically.
    _setState(HlsPreloadState.stopped);
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

/// `stopped` = user intentionally left the live screen; globalHlsPreloader
/// will not auto-restart until the arena list calls preload() again.
enum HlsPreloadState { idle, initializing, playing, error, stopped }
